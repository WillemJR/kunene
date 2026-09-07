import logging
logging.basicConfig(level=logging.WARNING)

import sys, os
from pathlib import Path

from kunene.dyna_actions import DynaAnalysis
from kunene.d3plot_actions import d3plot_File
from kunene.graph_actions import WorkFlow, WorkArea

def run_example():

    # ls-dyna input deck 'spring.k' contains *PARAMETER floatpar1 and intpar2
    fe_name =  "spring.k"
    fe_path = Path(__file__).parent.parent / "tests" / fe_name
    if not fe_path.exists(): exit(f"Error: {fe_path} does not exist.")

    wf = WorkFlow( "Dyna_WorkFlow" )
    
    run_dyna = DynaAnalysis("Spring", cmd='ls-dyna', input_path=fe_name)
    wf.add_action(run_dyna)

    # Results are extracted from the d3plot file.
    d3p = d3plot_File( 'field' )
    d3p.NodalValue('n5', state=1, nid=5, component= 'node_displacement'  )
    d3p.NodalValue('c5', state=1, nid=5, component= 'node_coordinates'  )
    wf.add_action( d3p )

    # Create WorkArea. Copy the spring.k file to the run directory.
    wrk_area = WorkArea( wf, copy_paths=[str(fe_path)] )

    # Discover variables — WorkArea copies files first so DynaAnalysis can read them.
    discovered_vars = wrk_area.parameters()
    print("Discovered variables:")
    for v in discovered_vars:
        print(f"  {v}")

    params = {'floatpar1': 1.5, 'intpar2': 800}
    print(f"Running simulation with params: {params}")
    
    # Edit parameters values and run
    try:
        #results = wrk_area.solve(params)
        results = wrk_area.solve(params)
        print("Results:", results.keys() )
        print("Results:", results)
    except Exception as e:
        print(f"Simulation execution failed: {e}")

if __name__ == "__main__":
    run_example()
