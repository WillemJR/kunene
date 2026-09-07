
from pathlib import Path

import numpy as np

from kunene.actions import WorkAction
from kunene.args import RADIOSS_ROOT_NAME

import kunene.VTK.read_vtk as read_vtk # pulls in pyvista

import logging
logger = logging.getLogger(__name__)

#
# utilities
#

def get_last_run_idx(root_name=RADIOSS_ROOT_NAME):
    p = Path( '.' )
    max_idx = -1
    for g in p.glob(root_name+'_*.vtk') :
        idx = int(str(g).split('.')[0].split('_')[-1])
        if idx > max_idx: max_idx = idx
    return max_idx

def get_last_run_name(root_name=RADIOSS_ROOT_NAME):
    max_idx = get_last_run_idx(root_name)
    return root_name+'_%03d.vtk'%(max_idx)

#
# end utilities
#


class VTK_FieldData(WorkAction):
    """
    Deprecated for now.
    More complex than VTK_NodalFieldData, VTK_ElementNodalFieldData, VTK_ElementFieldData

    Args:
        state (int): -1 is last
        args, kwargs: required_part_id, node_data_names=None, el_data_names=None, el_nodal_data_names
    Returns:
        ndict, edict # ndict={coords,data}, edict={..., data}
    """
    def __init__( self, name, state=-1, *args, root_name=RADIOSS_ROOT_NAME, **kwargs ):

        super().__init__(name, None )
        self.state= state
        self.args= args
        self.kwargs= kwargs
        self.root_name = root_name
        self.description = f'VTK field data at state {state}'


    def solve( self,  val_dict=None ):
        assert( 0 ), 'Deprecated use VTK_NodalFieldData etc'

    def eval_old( self,  val_dict=None ):
        if self.state > -1:
            vtk_file_name = self.root_name+'_%03d.vtk'%(self.state)
        else:
            vtk_file_name = get_last_run_name(self.root_name)
        if not Path(vtk_file_name).exists():
            logger.error(f"Cannot open VTK file. Did you set \'create_vtk=True\' ")
            raise FileNotFoundError(f'VTK file not found: {vtk_file_name}')
        v = read_vtk.read_part_mesh( vtk_file_name, *self.args, **self.kwargs )
        return v

    def _dump(self,  val_dict=None ):
        pass

class VTK_MetaData(VTK_FieldData):
    """
    Metadata

    Arguments:
        required_part_id (int):
    Returns:
        node ids
        cells
        coords
    """
    def solve( self,  val_dict=None ):
        data = super().eval_old( val_dict )
        ndata = data[0]
        edata = data[1]
        node_ids = ndata['data']['node_ids']
        coords = ndata['coords']
        cells = edata['cells']
        return {'node_ids':node_ids, 'cells':cells, 'coords':coords}


class VTK_NodalFieldData(VTK_FieldData):
    """

    Arguments:
        state (int): -1 is last
        required_part_id (int):
        node_data_names (list):
    Returns:
        node data
    """
    def solve( self,  val_dict=None ):
        data = super().eval_old( val_dict )[0]['data']
        return data



class VTK_ElementNodalFieldData(VTK_FieldData):
    """

    Arguments:
        state (int): -1 is last
        required_part_id (int):
        element_nodal_data_names (list):
    Returns:
        node data
    """
    def solve( self,  val_dict=None ):
        data = super().eval_old( val_dict )[0]['data']
        return data



class VTK_ElementFieldData(VTK_FieldData):
    """
    NYI: TEST. ELEMENT_IDs match?

    Arguments:
        state (int): -1 is last
        required_part_id (int):
        element_nodal_data_names (list):
    Returns:
        node data
    """
    def solve( self,  val_dict=None ):
        assert 0, 'not tested'
        data = super().eval_old( val_dict )[1]['data']
        return data


class VTK_FieldDataHist(WorkAction):

    def __init__( self, name, *args, root_name=RADIOSS_ROOT_NAME, **kwargs ):

        super().__init__(name, None )
        self.args= args
        self.kwargs= kwargs
        self.root_name = root_name
        self.description = f'VTK field data history'


    def solve( self,  val_dict=None ):
        coords = []
        #data =  # node_data_names
        # el_nodal_data_names becomes node data
        ndata = {name:[] for name in self.kwargs['node_data_names']+self.kwargs['el_nodal_data_names'] }
        edata = {name:[] for name in self.kwargs['el_data_names'] }
        for run_idx in range( 1, get_last_run_idx(self.root_name)+1 ):
            vtk_file_name = self.root_name+'_%03d.vtk'%(run_idx)
            nv, ev = read_vtk.read_part_mesh( vtk_file_name, *self.args, **self.kwargs )
            coords.append( nv['coords'] )

            for key,value in ndata.items():
                ndata[key].append( nv['data'][key] )
            for key,value in edata.items():
                edata[key].append( ev['data'][key] )
        coords = np.array( coords )
        for key,value in ndata.items():
            ndata[key] = np.stack( value )
        for key,value in edata.items():
            edata[key] = np.stack( value )
        nvl, evl = {'coords':coords, 'data':ndata}, {'data':edata}
        return nvl, evl

    def _dump(self,  val_dict=None ):
        pass
