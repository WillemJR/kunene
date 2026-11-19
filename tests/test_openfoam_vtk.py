"""
OpenFOAM cases read in memory through pyvista (kunene/util/openfoam_vtk.py)
and the actions using it.

The fixtures in tests/openfoam_vtk_exa were made with OpenFOAM 13 from the
lid-driven cavity in tests/openfoam_exa (nCells 3: a 0.1 x 0.1 x 0.01 box):
  hex/   blockMesh + icoFoam, time directories 0.1 and 0.5 (9 hexahedra)
  poly/  blockMesh + 'polyDualMesh 70', mesh only (hexahedra and polyhedra)
so the tests need pyvista but not OpenFOAM.
"""
import os
import pickle
import shutil
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

pytest.importorskip( 'pyvista' )

from kunene import save_workflow, load_workflow
from kunene.args import Location
from kunene.errors import DataNotFoundError, MissingPathError
from kunene.openfoam_actions import OpenFOAM_Field, OpenFOAM_Mesh, foam_to_vtk_cmd
from kunene.util import openfoam_vtk
from kunene.util.openfoam_reader import OpenFOAMFieldReader

FIXTURES = Path( __file__ ).parent / 'openfoam_vtk_exa'
BOX_VOLUME = 0.1 * 0.1 * 0.01
HEX, WEDGE, POLYHEDRON, QUAD, POLYGON = 12, 13, 42, 9, 7


@pytest.fixture
def hex_case( tmp_path ):
    case = tmp_path / 'hex'
    shutil.copytree( FIXTURES / 'hex', case )
    return case


@pytest.fixture
def poly_case( tmp_path ):
    case = tmp_path / 'poly'
    shutil.copytree( FIXTURES / 'poly', case )
    return case


@pytest.fixture
def in_dir():
    old = os.getcwd()
    def _chdir( path ):
        os.chdir( path )
    yield _chdir
    os.chdir( old )


# --- geometry helpers --------------------------------------------------------

def _tet_volume( a, b, c, d ):
    return np.einsum( 'ij,ij->i', b - a, np.cross( c - a, d - a ) ) / 6.0

# a VTK hexahedron (0-3 bottom, 4-7 above them) as five tetrahedra, each
# positively oriented when the node order is VTK's
HEX_TETS = [ (0, 1, 3, 4), (1, 2, 3, 6), (1, 6, 4, 5), (3, 4, 6, 7), (1, 3, 4, 6) ]

def _hex_volumes( coords, conn ):
    p = coords[conn]
    return sum( _tet_volume( p[:, a], p[:, b], p[:, c], p[:, d] ) for a, b, c, d in HEX_TETS )

def _polyhedron_volume( coords, faces ):
    """ Divergence theorem over fan-triangulated faces; faces must be
    consistently oriented, which is what the test checks. """
    vol = 0.0
    for f in faces:
        p = coords[f]
        for i in range( 1, len( f ) - 1 ):
            vol += np.dot( p[0], np.cross( p[i], p[i + 1] ) ) / 6.0
    return abs( vol )


# --- mesh --------------------------------------------------------------------

def test_hex_mesh_typed_cells( hex_case ):
    mesh = openfoam_vtk.read_mesh( hex_case )

    assert set( mesh['cells'] ) == { HEX }
    assert mesh['cells'][HEX].shape == (9, 8)
    assert mesh['coords'].shape == (32, 3)
    assert sorted( mesh['cell_ids'][HEX] ) == list( range( 9 ) )
    assert mesh['time'] == pytest.approx( 0.5 )

    vols = _hex_volumes( mesh['coords'], mesh['cells'][HEX] )
    assert ( vols > 0 ).all(), 'a hexahedron is not in VTK node order'
    assert vols.sum() == pytest.approx( BOX_VOLUME )


def test_polyhedral_mesh( poly_case ):
    mesh = openfoam_vtk.read_mesh( poly_case )
    cells, coords = mesh['cells'], mesh['coords']

    assert set( cells ) == { HEX, POLYHEDRON }
    assert isinstance( cells[POLYHEDRON], list )
    ids = np.concatenate( [ mesh['cell_ids'][t] for t in cells ] )
    assert sorted( ids ) == list( range( len( ids ) ) )

    for faces in cells[POLYHEDRON]:
        # a closed polyhedron uses each of its edges in exactly two faces
        edges = Counter( frozenset( ( int( f[i] ), int( f[(i + 1) % len( f )] ) ) )
                         for f in faces for i in range( len( f ) ) )
        assert set( edges.values() ) == { 2 }

    hex_vol = _hex_volumes( coords, cells[HEX] )
    assert ( hex_vol > 0 ).all()
    total = hex_vol.sum() + sum( _polyhedron_volume( coords, f ) for f in cells[POLYHEDRON] )
    assert total == pytest.approx( BOX_VOLUME )


def test_patches( hex_case, poly_case ):
    mesh = openfoam_vtk.read_mesh( hex_case, patches=True )
    patches = mesh['patches']
    assert set( patches ) == { 'movingWall', 'fixedWalls', 'frontAndBack' }
    assert patches['movingWall']['cells'][QUAD].shape == (3, 4)
    assert patches['frontAndBack']['cells'][QUAD].shape == (18, 4)
    for p in patches.values():
        assert p['cells'][QUAD].max() < len( p['coords'] )   # own point numbering

    # the dual mesh has polygonal faces on its front and back
    poly = openfoam_vtk.read_mesh( poly_case, patches=True )['patches']['frontAndBack']
    assert isinstance( poly['cells'][POLYGON], list )
    assert all( len( f ) > 4 for f in poly['cells'][POLYGON] )

    assert 'patches' not in openfoam_vtk.read_mesh( hex_case )


def test_case_marker( hex_case, tmp_path ):
    assert openfoam_vtk.case_marker( hex_case ) == ( hex_case / 'hex.foam' ).resolve()
    assert ( hex_case / 'hex.foam' ).exists()
    # an existing marker is used, whatever its name
    ( hex_case / 'hex.foam' ).rename( hex_case / 'case.foam' )
    assert openfoam_vtk.case_marker( hex_case ).name == 'case.foam'
    assert not ( hex_case / 'hex.foam' ).exists()

    with pytest.raises( MissingPathError ):
        openfoam_vtk.case_marker( tmp_path )


# --- fields and times --------------------------------------------------------

def test_cell_field_matches_ascii_parser( hex_case, in_dir ):
    in_dir( hex_case )
    for quantity in ( 'p', 'U' ):
        _, expected = OpenFOAMFieldReader( '.' ).field( quantity, time_dir='0.5' )
        np.testing.assert_allclose( openfoam_vtk.read_field( '.', quantity, 0.5 ), expected )


def test_time_selection( hex_case ):
    last = openfoam_vtk.read_field( hex_case, 'p', -1 )
    np.testing.assert_array_equal( last, openfoam_vtk.read_field( hex_case, 'p', 0.5 ) )
    assert not np.allclose( last, openfoam_vtk.read_field( hex_case, 'p', 0.1 ) )

    with pytest.raises( DataNotFoundError, match='0.3' ):
        openfoam_vtk.read_field( hex_case, 'p', 0.3 )


def test_nodal_field_and_missing_field( hex_case ):
    assert openfoam_vtk.read_field( hex_case, 'U', location=Location.NODAL ).shape == (32, 3)
    assert openfoam_vtk.read_field( hex_case, 'p', location=Location.NODAL ).shape == (32,)

    with pytest.raises( DataNotFoundError, match='T' ):
        openfoam_vtk.read_field( hex_case, 'T' )


# --- actions -----------------------------------------------------------------

def test_field_action_uses_reader_and_falls_back( hex_case, in_dir ):
    in_dir( hex_case )
    action = OpenFOAM_Field( name='p_field', quantity='p', time=0.5 )
    with_vtk = action.solve( {} )
    with patch.object( openfoam_vtk, 'available', return_value=False ):
        ascii_only = action.solve( {} )
    np.testing.assert_allclose( with_vtk, ascii_only )

    nodal = OpenFOAM_Field( name='u_nodal', quantity='U', time=-1, location=Location.NODAL )
    assert nodal.solve( {} ).shape == (32, 3)


def test_mesh_action( poly_case, in_dir, tmp_path ):
    in_dir( poly_case )
    action = OpenFOAM_Mesh( name='mesh', patches=True )
    mesh = action.solve( {} )
    assert set( mesh['cells'] ) == { HEX, POLYHEDRON }
    assert 'frontAndBack' in mesh['patches']

    # the output is plain data: it goes into actions_output.pkl as it is
    again = pickle.loads( pickle.dumps( mesh ) )
    np.testing.assert_array_equal( again['cells'][HEX], mesh['cells'][HEX] )

    spec = tmp_path / 'mesh.kunene.json'
    save_workflow( action, spec )
    loaded = load_workflow( spec )
    assert isinstance( loaded, OpenFOAM_Mesh )
    assert loaded.patches is True and loaded.time == -1


def test_foam_to_vtk_command_name():
    on_path = lambda *names: ( lambda cmd: f'/bin/{cmd}' if cmd in names else None )
    with patch( 'kunene.openfoam_actions.shutil.which', on_path( 'foamToVtk' ) ):
        assert foam_to_vtk_cmd() == 'foamToVtk'        # OpenFOAM.com
    with patch( 'kunene.openfoam_actions.shutil.which', on_path( 'foamToVTK' ) ):
        assert foam_to_vtk_cmd() == 'foamToVTK'        # OpenFOAM.org
    with patch( 'kunene.openfoam_actions.shutil.which', on_path() ):
        assert foam_to_vtk_cmd() == 'foamToVtk'
