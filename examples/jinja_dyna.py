import logging
logging.basicConfig(level=logging.WARNING)

import os
import sys
from pathlib import Path

# Add project root to path to ensure kunene is found
sys.path.append( str(Path(__file__).parent.parent) )

from kunene.graph_actions import WorkFlow, WorkArea
from kunene.jinja_actions import JinjaReplace
from kunene.radioss_using_dyna_inp import RadiossUsingDynaInput

def main():
    # Paths
    input_deck = Path('tests/par_tens.k')

    if not input_deck.exists():
        print(f"Error: {input_deck} not found. Run from project root.")
        return

    # 1. Define JinjaReplace to substitute parameters
    # This reads 'tests/par_tens.k', substitutes {{E}} and {{SIG_Y}}, 
    # and writes to 'par_tens_ready.k'
    jinja_act = JinjaReplace( 
        name='prepare_deck', 
        input_file_path=str(input_deck), output_file_path='edited.k',
        val_format="%10.3g"
    )

    # 2. Use RadiossUsingDynaInput to run the LS-DYNA deck with OpenRadioss.
    # It takes the substituted file 'edited.k' as input; 'rad_dyna_inp' is the
    # script that runs an LS-DYNA keyword file through OpenRadioss.
    run_radioss = RadiossUsingDynaInput("RADIOSS", cmd='rad_dyna_inp', input_path='edited.k')


    # 3. Create a workflow and add actions
    wf = WorkFlow( 'JR_WorkFlow' )
    wf.add_action( jinja_act )
    wf.add_action( run_radioss )
    wrk_area = WorkArea( wf )

    # Discover variables — WorkArea copies files first so the solver action can read them.
    discovered_vars = wrk_area.parameters()
    print("Discovered variables:")
    for v in discovered_vars:
        print(f"  {v}")

    # 4. Execute the workflow
    # Provide values for the variables defined in the Jinja template
    val_dict = { 'E': 210.0, 'SIG_Y': 310.0 }

    print("Starting workflow...")
    print(f"Parameters: {val_dict}")
    
    try:
        wrk_area.solve( val_dict )
        print("Workflow completed.")
    except Exception as e:
        print(f"Workflow execution stopped (expected if solver is missing): {e}")

if __name__ == "__main__":
    main()

