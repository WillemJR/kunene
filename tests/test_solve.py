


import numpy as np

from lasso.dyna import FilterType

########## ########## ########## ########## ########## ##########
# NEEDS BELOW else BUG in vortex_radioss/animtod3plot
########## ########## ########## ########## ########## ##########

from scipy.spatial.distance import directed_hausdorff
from kunene.variables import FloatVariable
from kunene.graph_actions import WorkFlow, SimulationIterator
from kunene.d3plot_actions import d3plot_File
from kunene.radioss_actions import RadiossAnalysis, RadiossCSVHistory, CSVNodeLocationHistory, CSVNodeLocation
from kunene.vtk_actions import VTK_FieldData, VTK_FieldDataHist
from kunene.vtk_actions import VTK_NodalFieldData, VTK_ElementNodalFieldData, VTK_MetaData
from kunene.jinja_actions import JinjaReplace
from kunene.radioss_using_dyna_inp import RadiossUsingDynaInput

import logging
logging.basicConfig(filename='solve.log', filemode='w', level=logging.INFO )

def test_seq( ):


    chain = WorkFlow( 'RadiossChain' )

    chain.add_action( JinjaReplace( name='SetVars', input_file_path='tests/par_tens.k', output_file_path='edited.k' ) )
    chain.add_action(  RadiossUsingDynaInput("RadiossRun", cmd="rad_dyna_inp", input_path='edited.k', create_vtk=True, create_csv=True ) )
    chain.add_action( RadiossCSVHistory('hist_eval', '{"quantity":"EXTERNAL WORK" }' ) )

    chain.add_action( VTK_MetaData( 'meta', state=2, required_part_id=3 ) )
    chain.add_action( VTK_NodalFieldData('field', state=2,
                                        required_part_id=3, 
                                        node_data_names=[ 'NODE_ID', 'Displacement' ] ) )

    chain.add_action( VTK_FieldDataHist('field_hist', 
                                     required_part_id=3,
                                     node_data_names=[ 'NODE_ID', 'Displacement' ],
                                     el_data_names=['2DELEM_Specific_Energy', 'ELEMENT_ID'],
                                     el_nodal_data_names=['2DELEM_Specific_Energy'] ) )

    # iterator can runs different directories
    simu_iter = SimulationIterator( chain, copy_paths=['tests/par_tens.k'] )

    simu_iter.rm_rundir() # clean_start=True) )

    evals = simu_iter.solve( { 'SIG_Y':300., 'E':123.4 } )
    #print( evals )
    print(  evals.keys() )
    #print( 'E', evals['E'] ) # FIXME, only passed in to solve exists
    print( 'SIG_Y', evals['SIG_Y'] )
    print( 'field.coords', evals['meta']['coords'].shape )
    print( 'field.coords', evals['meta']['coords'] )
    print( 'field.data', evals['field'].keys() )
    print( '---------------------------------------- field_hist' )
    print( 'field_hist.coords', evals['field_hist'][0]['coords'].shape )
    print( 'field_hist.coords', evals['field_hist'][0]['coords'] )
    print( 'field_hist.nodal.data', evals['field_hist'][0]['data'].keys() )
    print( 'field_hist.element.data', evals['field_hist'][1]['data'].keys() )
    print( '---------------------------------------- field_hist' )
    print( evals['field_hist'][0]['coords'].shape )
    assert evals['field_hist'][0]['coords'].shape == (21, 513, 3)
    print( round( evals['field_hist'][0]['coords'][0][0][0],3) )
    assert round( evals['field_hist'][0]['coords'][0][0][0],3) == 43.241
    print( round( evals['field_hist'][0]['coords'][-1][-1][0],3) )
    assert round( evals['field_hist'][0]['coords'][-1][-1][0],3) == 61.222

    simu_iter.rm_rundir()

def test_hist_node( ):

    chain = WorkFlow( 'RadiossChain' )

    chain.add_action( JinjaReplace( name='SetVars', input_file_path='tests/par_tens.k', output_file_path='edited.k'  ) )
    chain.add_action(  RadiossUsingDynaInput("RadiossRun", cmd="rad_dyna_inp", input_path='edited.k', create_csv=True ) )
    chain.add_action( CSVNodeLocationHistory('node_loc_h', 851 ) )
    chain.add_action( CSVNodeLocation('node_loc', 851 ) )

    # iterator can runs different directories
    simu_iter = SimulationIterator( chain, copy_paths=['tests/par_tens.k'] )

    simu_iter.rm_rundir() # clean_start=True) )

    evals = simu_iter.solve( { 'SIG_Y':300., 'E':123.4  } )
    print( evals )

    simu_iter.rm_rundir()

def test_exp_des( ):

    chain = WorkFlow( 'RadiossChain' )

    chain.add_action( JinjaReplace( name='SetVars', input_file_path='tests/par_tens_1p.k', output_file_path='edited.k'  ) )
    chain.add_action(  RadiossUsingDynaInput("RadiossRun", cmd="rad_dyna_inp", input_path='edited.k',
                                             create_d3plot=True, create_vtk=True, create_csv=True) )
    chain.add_action( CSVNodeLocationHistory('node_loc_h', 851 ) )
    chain.add_action( CSVNodeLocation('node_loc', 851 ) )


    chain.add_action( VTK_NodalFieldData('field_eval_node',
                                        required_part_id=3, # NYI, bug if multiple parts?
                                        node_data_names=[ 'NODE_ID', 'Displacement' ] ) )

    chain.add_action( VTK_ElementNodalFieldData('field_eval_element_node',
                                        required_part_id=3, # NYI, bug if multiple parts?
                                        el_nodal_data_names=[ 'NODE_ID', '2DELEM_Specific_Energy' ],) )

    simu_iter = SimulationIterator( chain, copy_paths=['tests/par_tens_1p.k'] )

    simu_iter.rm_rundir() # clean_start=True) )

    evals = simu_iter.solve( { 'SIG_Y':300., 'E':123.4, 'TERM': 40.0, 'WRITE_D3P': 10.0 } )
    #print( evals )

    print( '############################ EXP DES ###########################' )
    pars, out = simu_iter.collect_for_varrange( { 'E':np.arange( 100., 102., 1.0 ), 'SIG_Y':[300.], 'TERM': [40.0], 'WRITE_D3P': [10.0]} )

    print( '--- PARS --' )
    print( pars )
    print( '--- OUT --' )
    for k,v in out.items():
        print( '\t', k, type(v) )
        if type(v) == list:
            print( '\t\tlist len:', len(v) )
            try:  print( '\t\tnp shape:', v[0].shape )
            except: pass
        elif type(v) == dict:
            print( '\t\tdict keys:', v.keys() )
    simu_iter.rm_rundir()

def test_d3p( ):

    chain = WorkFlow( 'RadiossChain' )

    chain.add_action( JinjaReplace( name='SetVars', input_file_path='tests/par_tens.k', output_file_path='edited.k' ) )
    chain.add_action(  RadiossUsingDynaInput("RunSpring", cmd="rad_dyna_inp", input_path='edited.k',
                                              create_d3plot=True, create_csv=True) )

    d3p = d3plot_File( 'field' )
    d3p.MultNodalField('nfield', state=2, required_part_id=3,
                              node_data_names=[ 'node_ids', 'node_displacement' ] )
    d3p.MultElementField('efield', state=2, required_part_id=3, element_type=FilterType.SHELL,
                             element_data_names=[ 'element_shell_stress', 'element_shell_internal_energy' ] )
    chain.add_action( d3p )


    simu_iter = SimulationIterator( chain, copy_paths=['tests/par_tens.k'] )
    simu_iter.rm_rundir() # clean_start=True) )

    evals = simu_iter.solve( { 'SIG_Y':300., 'E':123.4 } )
    #print( evals )
    print(  evals.keys() )
    #print( 'E', evals['E'] ) # FIXME, only passed in to solve exists
    print( 'SIG_Y', evals['SIG_Y'] )
    #print( 'field.data', evals['field'].keys() )
    #print( '---------------------------------------- field_hist' )
    #print( 'field_hist.coords', evals['field_hist'][0]['coords'].shape )
    print( evals.keys() )
    # 'nfield'/'efield' are children of the 'field' d3plot_File graph, so their
    # outputs stay nested under 'field' (structured graph output).
    assert round( evals['field']['nfield']['node_displacement'][0][0] , 2 ) == 66.71
    assert round( evals['field']['efield']['element_shell_stress'][0][0][0] , 3 ) == 0.034

    simu_iter.rm_rundir()

if __name__ == '__main__':
    #test_hist_node( )
    #test_seq( )

    #test_exp_des( )
    test_d3p( )


