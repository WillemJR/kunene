import logging
logging.basicConfig(level=logging.WARNING)

import os
import sys
from pathlib import Path

# Add project root to path to ensure kunene is found
sys.path.append( str(Path(__file__).parent.parent) )

from kunene.radioss_actions import RadiossAnalysis 
from kunene.d3plot_actions import d3plot_File
from kunene.graph_actions import WorkFlow, WorkArea

def main():
    # Paths
    starter_deck = Path('models/cube_TYPE7_0000.rad')
    engine_deck  = Path('models/cube_TYPE7_0001.rad')

    if not starter_deck.exists() or not engine_deck.exists():
        print(f"Error: {starter_deck} or {engine_deck}not found. Run from project root.")
        return

    # 1. Define RadiossAnalysis to run the simulation
    run_rad = RadiossAnalysis( name='rad', 
                  starter_cmd='openradioss_starter',
                  starter_input_path=starter_deck,
                  engine_cmd='openradioss_engine',
                  engine_input_path=engine_deck,
                  create_d3plot=True )

    # 2. Create a workflow and add actions
    wf = WorkFlow( 'Radioss_WorkFlow' )
    wf.add_action( run_rad )

    d3p = d3plot_File( name='d3plot' )
    d3p.NodalValue(name='n5', state=1, nid=5, component= 'node_displacement'  )
    wf.add_action( d3p )

    wrk_area = WorkArea( wf, copy_paths=[starter_deck,engine_deck] )

    # Discover variables defined in input deck and other actions
    discovered_vars = wrk_area.parameters()
    print("Discovered variables:")
    for v in discovered_vars:
        print(f"  {v}")

    # 3. Execute the workflow. Provide values for the variables.
    val_dict = { 'E': 210000.0, }

    print("Starting workflow...")
    print(f"Parameters: {val_dict}")
    
    try:
        ret = wrk_area.solve( val_dict )
        print("Workflow completed.")
        print("Available results.", ret.keys() )
    except Exception as e:
        print(f"Workflow execution stopped: {e}")

if __name__ == "__main__":
    main()

