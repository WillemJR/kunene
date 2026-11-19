"""
Read an OpenFOAM case in memory through VTK's OpenFOAM reader (via pyvista).

The case is read straight from ``constant/polyMesh`` and the time directories:
``foamToVTK`` need not have run and no file is written, apart from the empty
``<case>.foam`` marker the reader is opened with (the same file
``paraFoam -builtin -touch`` creates).

Cells are returned grouped by VTK cell type, in the format of
:class:`kunene.vtk_actions.VTK_MetaData`::

    {'coords':   (n_points, 3) float array,
     'cells':    {vtk_type: connectivity},
     'cell_ids': {vtk_type: (n,) int array}}

For a fixed-size type (tetra 10, hexahedron 12, wedge 13, pyramid 14, and on a
patch triangle 5 and quad 9) the connectivity is an ``(n, k)`` int array in
VTK node order. A variable-size type -- polyhedron (42) in the internal mesh,
polygon (7) on a patch -- is a list with one entry per cell: for a polygon its
point ids, for a polyhedron a list of its faces, each an array of point ids.
``cell_ids`` gives the OpenFOAM cell index of each row, so a cell field (which
is in OpenFOAM cell order) is picked per type as ``field[cell_ids[12]]``.

pyvista is imported only when a case is read, so the module can be imported
without it; :func:`available` tells whether it can be used.
"""
import os
from pathlib import Path

import numpy as np

from kunene.args import Location
from kunene.errors import DataNotFoundError, MissingPathError

import logging
logger = logging.getLogger(__name__)

VTK_POLYGON = 7
VTK_POLYHEDRON = 42

# number of nodes of the fixed-size VTK cell types an OpenFOAM mesh contains
FIXED_SIZE_TYPES = { 1: 1,    # vertex
                     3: 2,    # line
                     5: 3,    # triangle
                     9: 4,    # quad
                     10: 4,   # tetra
                     12: 8,   # hexahedron
                     13: 6,   # wedge
                     14: 5 }  # pyramid

INTERNAL_MESH = 'internalMesh'


def available():
    """ True when pyvista (and so VTK) can be imported. """
    try:
        import pyvista  # noqa: F401
    except ImportError:
        return False
    return True


def _pyvista():
    try:
        import pyvista
    except ImportError as e:
        raise ImportError( "reading an OpenFOAM case needs pyvista: pip install 'kunene[vtk]'" ) from e
    return pyvista


def case_marker( case_dir='.' ):
    """
    The ``*.foam`` file the reader opens the case with. An existing one is
    used; otherwise ``<case name>.foam`` is created (empty), as
    ``paraFoam -builtin -touch`` would.
    """
    case = Path( case_dir ).resolve()
    if not ( case / 'constant' / 'polyMesh' ).is_dir():
        raise MissingPathError( f"Not an OpenFOAM case (no constant/polyMesh): {case}" )
    markers = sorted( case.glob( '*.foam' ) )
    if markers:
        return markers[0]
    marker = case / f'{case.name}.foam'
    marker.touch()
    return marker


def open_case( case_dir='.' ):
    """ A ``pyvista.OpenFOAMReader`` on the case in ``case_dir``. """
    pv = _pyvista()
    reader = pv.OpenFOAMReader( os.fspath( case_marker( case_dir ) ) )
    reader.skip_zero_time = False
    return reader


def select_time( reader, time=-1 ):
    """
    Make ``time`` the reader's active time and return it. ``-1`` (or ``None``)
    is the last time; any other value must match a time directory (compared
    with ``np.isclose``, so ``0.5`` finds the directory ``0.5``).
    """
    times = list( reader.time_values )
    if not times:
        return None     # mesh only: no time directory to choose from
    if time is None or float( time ) == -1:
        t = times[-1]
    else:
        match = [ t for t in times if np.isclose( t, float( time ) ) ]
        if not match:
            raise DataNotFoundError( f"No OpenFOAM time {time}; the case has times {times}" )
        t = match[0]
    reader.set_active_time_value( t )
    return t


def _blocks( reader, patches ):
    """ The internal mesh and, when asked, the boundary patches as a dict of grids. """
    if patches:
        reader.enable_all_patch_arrays()
    else:
        reader.disable_all_patch_arrays()
        reader.enable_patch_array( INTERNAL_MESH )
    data = reader.read()
    internal = data[INTERNAL_MESH] if INTERNAL_MESH in data.keys() else None
    if internal is None:
        raise DataNotFoundError( f"No internal mesh in the OpenFOAM case {reader.path}" )
    boundary = {}
    if patches and 'boundary' in data.keys():
        bnd = data['boundary']
        for name in bnd.keys():
            if bnd[name] is not None:
                boundary[name] = bnd[name]
    return internal, boundary


def _polyhedron_faces( grid, cell_idx ):
    """ Faces (arrays of point ids) of the polyhedra ``cell_idx`` of ``grid``. """
    if hasattr( grid, 'GetPolyhedronFaces' ):   # VTK >= 9.4
        from vtk.util.numpy_support import vtk_to_numpy
        locs, faces = grid.GetPolyhedronFaceLocations(), grid.GetPolyhedronFaces()
        loc_off = vtk_to_numpy( locs.GetOffsetsArray() )
        loc_con = vtk_to_numpy( locs.GetConnectivityArray() )
        face_off = vtk_to_numpy( faces.GetOffsetsArray() )
        face_con = vtk_to_numpy( faces.GetConnectivityArray() )
        return [ [ face_con[face_off[f]:face_off[f+1]] for f in loc_con[loc_off[c]:loc_off[c+1]] ]
                 for c in cell_idx ]
    return [ [ np.asarray( f.point_ids ) for f in grid.get_cell( int( c ) ).faces ] for c in cell_idx ]


def group_cells( grid ):
    """
    ``(cells, cell_ids)`` of a VTK grid, grouped by cell type: see the module
    docstring for the format. Only the types present get a key.
    """
    if not hasattr( grid, 'celltypes' ):    # a patch is PolyData
        grid = grid.cast_to_unstructured_grid()
    types = np.asarray( grid.celltypes )
    conn = np.asarray( grid.cell_connectivity )
    offsets = np.asarray( grid.offset )
    cells, cell_ids = {}, {}
    for vtk_type in np.unique( types ):
        vtk_type = int( vtk_type )
        idx = np.flatnonzero( types == vtk_type )
        if vtk_type == VTK_POLYHEDRON:
            cells[vtk_type] = _polyhedron_faces( grid, idx )
        elif vtk_type in FIXED_SIZE_TYPES:
            numn = FIXED_SIZE_TYPES[vtk_type]
            cells[vtk_type] = conn[ offsets[idx][:, None] + np.arange( numn ) ]
        else:
            cells[vtk_type] = [ conn[offsets[i]:offsets[i+1]] for i in idx ]
        cell_ids[vtk_type] = idx
    return cells, cell_ids


def _mesh_dict( grid ):
    cells, cell_ids = group_cells( grid )
    return { 'coords': np.asarray( grid.points ), 'cells': cells, 'cell_ids': cell_ids }


def read_mesh( case_dir='.', time=-1, patches=False ):
    """
    The mesh of an OpenFOAM case, cells grouped by VTK type (module docstring).

    Args:
        case_dir: the case directory.
        time: the time to read the mesh at, ``-1`` for the last; it matters
            only for a moving mesh.
        patches: also return the boundary patches, under ``'patches'``, as
            ``{patch_name: {'coords', 'cells', 'cell_ids'}}``. A patch has its
            own point numbering, and its ``cell_ids`` count its own faces.
    """
    reader = open_case( case_dir )
    t = select_time( reader, time )
    internal, boundary = _blocks( reader, patches )
    mesh = _mesh_dict( internal )
    mesh['time'] = t
    if patches:
        mesh['patches'] = { name: _mesh_dict( grid ) for name, grid in boundary.items() }
    return mesh


def read_field( case_dir='.', quantity='p', time=-1, location=Location.CELL ):
    """
    A field of the internal mesh at ``time`` (``-1`` for the last).

    ``Location.CELL`` (and ``UNKNOWN``) returns the cell values in OpenFOAM
    cell order, ``(n_cells,)`` or ``(n_cells, 3)``; ``Location.NODAL``
    returns point values interpolated by VTK, ``(n_points,)`` or
    ``(n_points, 3)``. A uniform field is returned expanded to every cell.
    """
    reader = open_case( case_dir )
    select_time( reader, time )
    reader.cell_to_point_creation = ( location == Location.NODAL )
    internal, _ = _blocks( reader, patches=False )
    data = internal.point_data if location == Location.NODAL else internal.cell_data
    if quantity not in data.keys():
        raise DataNotFoundError( f"No OpenFOAM field '{quantity}' at time {reader.active_time_value}"
                                 f" in {reader.path}; fields are {sorted( internal.cell_data.keys() )}" )
    return np.asarray( data[quantity] )
