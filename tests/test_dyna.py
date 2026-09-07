
import sys, os
from pathlib import Path

from kunene.graph_actions import WorkFlow, WorkArea
from kunene.dyna_actions import DynaAnalysis
from kunene.d3plot_actions import d3plot_File

def test_dyna_run_extract():

    fe_path = Path(__file__).parent.parent / "tests" / "spring.k"
    if not fe_path.exists(): exit(f"Error: {fe_path} does not exist.")

    wf = WorkFlow("SpringWorkFlow")
    
    run_dyna = DynaAnalysis("RunSpring", input_path=str(fe_path))
    wf.add_action(run_dyna)
    
    # Results are extracted from the d3plot file.
    d3p = d3plot_File( 'field' )
    d3p.NodalValue('n5', state=1, nid=5, component= 'node_displacement'  )
    d3p.NodalValue('c5', state=1, nid=5, component= 'node_coordinates'  )
    wf.add_action( d3p )

    # Create WorkArea. We copy the spring.k file to the run directory
    wrk_area = WorkArea( wf )
    
    # parameters values will be edited`
    params = {'floatpar1': 1.5, 'intpar2': 800}
    print(f"Running simulation with params: {params}")
    
    # Run
    try:
        results = wrk_area.solve(params)
        print("Results:", results)
    except Exception as e:
        print(f"Simulation execution failed: {e}")
    wrk_area.rm_rundir() 

if __name__ == "__main__":
    test_dyna_run_extract()
