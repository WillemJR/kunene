
import logging
logging.basicConfig(level=logging.WARN)



import numpy as np
from pathlib import Path

import kunene.actions, kunene.radioss_actions
from kunene.variables import FloatVariable
from kunene.jinja_actions import JinjaReplace
from kunene.graph_actions import WorkFlow, WorkArea, SimulationIterator
from kunene.dyna_actions import DynaAnalysis
from kunene.radioss_actions import RadiossAnalysis
from kunene.d3plot_actions import d3plot_File
from kunene.graph_actions import DirectedGraph

#from meta_opt.optimizer import Optimizer
#from kunene.curve_similarity import CurveSimilarity

import logging
logging.basicConfig(filename='eval.log', filemode='w', level=logging.INFO )

from kunene.args import DYNA_BASE_FILE_NAME

def create_wa( wa_id='' ):
    starter_deck = Path(__file__).parent.parent / 'models' / 'cube_TYPE7_0000.rad'
    engine_deck  = Path(__file__).parent.parent / 'models' / 'cube_TYPE7_0001.rad'

    if not starter_deck.exists() or not engine_deck.exists():
        print(f"Error: {starter_deck} or {engine_deck}not found. Run from project root.")
        return

    # 1. Define RadiossAnalysis to run the simulation
    run_rad = RadiossAnalysis( name='rad'+str(wa_id), 
                  starter_cmd='openradioss_starter',
                  starter_input_path=starter_deck,
                  engine_cmd='openradioss_engine',
                  engine_input_path=engine_deck,
                  create_d3plot=True )

    # 2. Create a workflow and add actions
    wf = WorkFlow( 'Radioss_WorkFlow' + str(wa_id) )
    wf.add_action( run_rad )

    d3p = d3plot_File( name='d3plot'+str(wa_id) )
    d3p.NodalValue(name='n5'+str(wa_id), state=1, nid=5, component= 'node_displacement'  )
    wf.add_action( d3p )

    wrk_area = WorkArea( wf, copy_paths=[starter_deck,engine_deck] )

    return wrk_area

def print_structure( action ):
    """Print the action graph and the resulting work directory structure.

    Neither requires the solvers to be installed or the workflow to be run.
    """
    print( f'\n==================== graph {action.name} structure ====================' )
    action.describe_workflow()

def create_graph( wa1, wa2 ):

    # check whether can save and load from disk
    import pickle
    with open("rad_cs_wa.pkl", "wb") as f:
        pickle.dump( wa1,  f )

    with open("rad_cs_wa.pkl", "rb") as f:
        loaded_wa = pickle.load( f )

    # 
    graph = DirectedGraph( 'aGraph' )
    graph.add_action( loaded_wa )
    graph.add_action( wa2 )

    return graph

def reset_vars( wa1, wa2, graph ):

    # **Mutating a stored object can break the set

    print( '------------------------ GRAPH' )

    print( '------------------------ CHANGED GRAPH' )
    i = 0.
    for v in graph.parameters():
        v.lower_bound = 1.03 + i
        print(f"Graph:  {v}")
        i += 1.

    for v in graph.parameters():
        print(f"Graph:  {v.name} {v.lower_bound}")
    print( '------------------------ WA1' )
    for v in wa1.parameters():
        print(f"wa1:  {v.name} {v.lower_bound}")
    print( '------------------------ WA2' )
    for v in wa2.parameters():
        print(f"wa2:  {v.name} {v.lower_bound}")

    print( '------------------------ CHANGED WA1' )
    i = 10.
    for v in wa1.parameters():
        v.lower_bound = 1.13 + i
        print(f"Graph:  {v}")
        i += 10.

    for v in graph.parameters():
        print(f"Graph:  {v.name} {v.lower_bound}")
    print( '------------------------ WA1' )
    for v in wa1.parameters():
        print(f"wa1:  {v.name} {v.lower_bound}")
    print( '------------------------ WA2' )
    for v in wa2.parameters():
        print(f"wa2:  {v.name} {v.lower_bound}")

def run_graph( graph ):
    # Execute the workflow. Provide values for the variables.
    val_dict = { 'E': 210000.0, }

    
    ret = graph.solve( val_dict )


def create_iterator( graph ):
    """Build a SimulationIterator wrapping a Radioss workflow.

    """

    itr = SimulationIterator( graph, work_area_path='Radioss_Iter' )

    return itr

if __name__ == '__main__':

    # Print the action graph and predicted work directory structure.
    # This works without the solvers installed and without running anything.
    wa = create_wa ( 1 )
    if wa is not None:
        print_structure( wa )

    wa2 = create_wa ( 2 )
    g = create_graph( wa, wa2 )
    #reset_vars( wa, wa2, g )
    print_structure( g )
    run_graph( g )


    itr = create_iterator( g )
    print_structure( itr )
    ret = itr.solve( { 'E': 210000.0, } )

