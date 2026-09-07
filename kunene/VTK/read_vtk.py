
import pyvista as pv
from vtk.util.numpy_support import vtk_to_numpy
import numpy as np

from collections import  namedtuple

from vtk import VTK_EMPTY_CELL, VTK_TRIANGLE, VTK_QUAD
from vtk import VTK_HEXAHEDRON, VTK_WEDGE, VTK_TETRA

########################
#   // Linear cells
#   VTK_EMPTY_CELL = 0,
#   VTK_VERTEX = 1,
#   VTK_POLY_VERTEX = 2,
#   VTK_LINE = 3,          <<<<<<<<<<
#   VTK_POLY_LINE = 4,
#   VTK_TRIANGLE = 5,          <<<<<<<<<<
#   VTK_TRIANGLE_STRIP = 6,
#   VTK_POLYGON = 7,
#   VTK_PIXEL = 8,
#   VTK_QUAD = 9,
#   VTK_TETRA = 10,          <<<<<<<<<<
#   VTK_VOXEL = 11,
#   VTK_HEXAHEDRON = 12,          <<<<<<<<<<
#   VTK_WEDGE = 13,
#   VTK_PYRAMID = 14,   # 5 nodes, rectangular base
#   VTK_PENTAGONAL_PRISM = 15,
#   VTK_HEXAGONAL_PRISM = 16,

ElProp = namedtuple( 'ElProp', ['vtk_type', 'num_node'], defaults=[0] )

el_props = {
            0:  ElProp(VTK_EMPTY_CELL, 0),
            5:  ElProp(VTK_TRIANGLE,   3),
            9:  ElProp(VTK_QUAD,       4),
            10: ElProp(VTK_TETRA,      4),
            12: ElProp(VTK_HEXAHEDRON, 8),
            13: ElProp(VTK_WEDGE,      6),
           }
#MAX_CONN = 8 # increase if needed


def print_mesh_data( vtk_file_name ):

    print( '==========================================================' )
    print( '== MESH DATA for', vtk_file_name )
    print( '==========================================================' )

    mesh =  pv.read( vtk_file_name )

    print( 'num_points', mesh.n_points )  # nodes for all parts
    print( 'num_cells', mesh.n_cells )  # for all parts


    print( '\n\ndata at nodes:\n', mesh.point_data )  
    print( '\n\ndata at els:\n', mesh.cell_data )  

    
    print( '\n\nSOME ARRAY DATA\n' )
    print( 'points', mesh.points )  # nodes for all parts

    print( 'cells', mesh.cells )  # nodes for all parts


    #node_ids = mesh.point_data['NODE_ID']  
    #print( 'node_ids', node_ids )
    #print( mesh.point_data['Contact_Forces'] ) # nodes for all parts

    #part_ids = mesh.cell_data['PART_ID'] 
    #print( part_ids )
    #el_ids = mesh.cell_data['ELEMENT_ID'] 
    #print( el_ids )
    #print( mesh.cell_data['3DELEM_Von_Mises'] )  # nodes for all parts

    cells = mesh.GetCells()
    num_cell = cells.GetNumberOfCells()
    cellConns = vtk_to_numpy( cells.GetConnectivityArray() )
    cellOffsets = vtk_to_numpy( cells.GetOffsetsArray() )
    cellTypes = vtk_to_numpy( mesh.GetCellTypesArray() )
    coords = vtk_to_numpy( mesh.GetPoints().GetData() )

    #print( 'part_ids', part_ids )
    print( 'cellConns', cellConns.shape, cellConns.shape[0]+num_cell, cellConns )
    print( 'cellTypes', cellTypes.shape, cellTypes )
    print( 'coords', coords.shape, coords )
    print( 'cellOffsets', cellOffsets )
    print( 'cellOffsets', cellOffsets[0] )

    print( '==========================================================' )
    print( '== END MESH DATA for', vtk_file_name )
    print( '==========================================================\n\n' )



def read_mesh_conns( vtk_file_name, required_part_id,
                     node_data_names=None, el_data_names=None, el_nodal_data_names=None ):

    mesh =  pv.read( vtk_file_name )

    node_ids = mesh.point_data['NODE_ID']  

    part_ids = mesh.cell_data['PART_ID'] 
    el_ids = mesh.cell_data['ELEMENT_ID'] 

    cells = mesh.GetCells()
    num_cell = cells.GetNumberOfCells()
    cellConns = vtk_to_numpy( cells.GetConnectivityArray() )
    cellOffsets = vtk_to_numpy( cells.GetOffsetsArray() )
    cellTypes = vtk_to_numpy( mesh.GetCellTypesArray() )
    coords = vtk_to_numpy( mesh.GetPoints().GetData() )

    if el_data_names :
        el_data = {}
        for d_name in el_data_names:
            el_data[d_name] = mesh.cell_data[ d_name ]
    else: el_data = None
    node_data = {'node_ids':node_ids}
    if node_data_names :
        for d_name in node_data_names:
            node_data[d_name] =  mesh.point_data[ d_name ]
    #else: node_data = None

    if el_nodal_data_names:
        el_nodal_mesh = mesh.cell_data_to_point_data()
        #if node_data is None : node_data = {}
        for d_name in el_nodal_data_names:
            node_data[d_name] =  el_nodal_mesh.point_data[ d_name ]

    #
    # Build connectivity matrix for required part
    #

    subset_el_types = []

    if el_data_names:
       subset_el_data = {}
       for d_name in el_data_names:
           subset_el_data[d_name] = [] 
    else: subset_el_data = None

    new_conn_offset = 0
    new_cellCons = np.array( [], dtype=np.int64  )
    new_cellOffsets = []
 

    for i, e_type in enumerate( cellTypes ):
        if el_ids[i] == 0:
           pass # internal elements
        elif part_ids[i] == required_part_id:
           if e_type in( VTK_TRIANGLE,   VTK_QUAD,VTK_HEXAHEDRON, VTK_WEDGE):
              num_node = el_props[ e_type ].num_node
              #assert( num_node <= MAX_CONN )
              offset = cellOffsets[i]
              conn = cellConns[ offset: offset+num_node]
              if e_type == VTK_QUAD and conn[3] == conn[2]:
                conn = conn[:-1] 
                e_type = VTK_TRIANGLE 

              subset_el_types.append( e_type )

              new_cellCons = np.append( new_cellCons, conn )
              new_cellOffsets.append( new_conn_offset )
              new_conn_offset = new_conn_offset + len(conn)

              if el_data_names:
                for d_name in el_data_names:
                  subset_el_data[d_name].append( el_data[d_name][i] )

        else:
            pass

    return coords, node_data,  \
           { 'subset_el_data':subset_el_data,
             'cell_conns':new_cellCons,
             'cell_offsets': np.array(new_cellOffsets),
             'el_types': np.array(subset_el_types) }

def compact_nodes( coords, node_data, el_dict ):
    """
    Remove nodes not connected to elements (of the part)
    """

    num_node_all = coords.shape[0]

    used = np.zeros( num_node_all ).astype( bool )
    new_node_idx  = np.zeros( num_node_all )

    cell_con = el_dict['cell_conns']
    for idx in cell_con:
        used[idx]=True

    num_node_new = 0
    for old_idx in range( num_node_all ):
        if used[old_idx]:
            new_node_idx[old_idx] = num_node_new
            num_node_new += 1
        else:
            pass


    # data at nodes
    if node_data is not None:
        node_data_subset = {}
        for k in node_data.keys():
            shape = list( node_data[k].shape )
            shape[0] = num_node_new
            node_data_subset[k] = np.zeros( shape, dtype=node_data[k].dtype )

        nidx = 0
        for old_idx in range( num_node_all ):
            if used[old_idx]:
                for k in node_data.keys():
                    node_data_subset[k] [ nidx ] = node_data[k][ old_idx ]
                nidx += 1
            else:
                pass
    else:
        node_data_subset = None

    # reset coords
    coords_subset = np.zeros( (num_node_new,3), dtype=float ) # NYI 2D
    nidx = 0
    for old_idx in range( num_node_all ):
        if used[old_idx]:
            coords_subset[nidx] = coords[old_idx] 
            nidx += 1
        else:
            #print( 'idx', old_idx, 'not used' )
            pass



    # reset connectivity
    cell_con = el_dict['cell_conns']
    cell_off = el_dict['cell_offsets']
    new_cell_con = np.zeros_like( cell_con)
    #new_cell_off = np.zeros_like( cell_off ) 
    for i,n_idx in enumerate( cell_con ):
        new_cell_con[i] = new_node_idx[n_idx]
    el_dict['cell_conns'] = new_cell_con

    return coords_subset, node_data_subset 


def to_cells( el_types, cell_conns, cell_offsets ):

    cells = {}
    for etype,ep in el_props.items() :
        numn = ep.num_node
        mask = np.where( el_types == etype )
        oo = cell_offsets[mask]
        cells[etype] = [cell_conns[k:k+numn] for k in oo]
    cells = { k:np.array(v) for k,v in cells.items() }
    return cells


def read_part_mesh( vtk_file_name, required_part_id,
                    node_data_names=None, el_data_names=None, el_nodal_data_names=None ):

    coords, node_data, el_dict = read_mesh_conns( vtk_file_name,
                                                  required_part_id,
                                                  node_data_names, el_data_names, el_nodal_data_names )


    coords_subset, node_data_subset = compact_nodes( coords, node_data, el_dict )

    cells = to_cells( el_dict['el_types'], el_dict['cell_conns'], el_dict['cell_offsets'] )

    return { 'coords' : coords_subset,
             'data'   : node_data_subset,
           },  \
           { #'conns' : new_subset_el_conns,
             #'types' : el_dict['el_types'],
             #'cell_conns' : el_dict['cell_conns'],
             #'cell_conn_offset' : el_dict['cell_offsets'],
             'cells' : cells,
             'data'  : el_dict['subset_el_data'],
           }


def to_lcl_id( trgt_ids, srcs_ids, srcs_vals):
    """ remap data to follow id sorting in trgt_ids instead of srcs_ids"""
    idx_for_node_id = np.full( int(trgt_ids.max())+1, -1, dtype=int )
    for idx,nid in enumerate(srcs_ids):
        idx_for_node_id [int(nid)] = np.where( trgt_ids==nid )[0][0]

    lcl_vals = np.zeros_like( srcs_vals )
    #lcl_i = np.zeros_like( trgt_ids )
    for i,nid in enumerate( srcs_ids ):
        lcl_vals[ idx_for_node_id[nid] ] = srcs_vals[i]
        #lcl_i[ idx_for_node_id[nid] ] = nid

    return lcl_vals




if __name__ == '__main__':
    #fname = 'RUBBER_SEAL_IMPDISP_GEOM_001.vtk'
    fname = 'test.vtk'
    #fname = 'test2.vtk'
    #fname = 'test2_us.vtk'

    required_part_id = 3
    node_data_names = [ 'NODE_ID' ]
    el_data_names = [ 'ELEMENT_ID' ]

    print_mesh_data( fname )

    node_data, el_data = \
           read_part_mesh( fname, required_part_id,
                           node_data_names = node_data_names,
                           el_data_names = el_data_names )

    print( '****** NODAL' )
    print( 'coords:\n', node_data['coords'] )
    print( 'node_data:\n', node_data['data'])

    print( '****** ELEMENTS' )
    #print( 'conns:\n', el_data['conns'] )
    #print( 'cell_conns:\n', el_data['cell_conns'] )
    for i,e_type in enumerate( el_data['types'] ):
        co = el_data['cell_conn_offset']
        num_node = el_props[ e_type ].num_node
        #print( e_type, co[i], el_data['cell_conns'][ co[i]:co[i]+num_node ] )
        print( e_type, el_data['cell_conns'][ co[i]:co[i]+num_node ] )
    print( 'data:\n', el_data['data'] )

