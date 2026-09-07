import logging
logging.basicConfig(level=logging.WARNING)

from pathlib import Path
from kunene.args import JobType
from kunene.graph_actions import WorkFlow, WorkArea
from kunene.openfoam_actions import OpenFOAMAnalysis
from kunene.openfoam_actions import OpenFOAM_Field, OpenFOAM_History

def create_openfoam_graph():

    case_dir = Path(__file__).parent.parent / "tests" / "openfoam_exa"
    if not case_dir.exists(): exit(f"Error: {case_dir} does not exist.")

    case_paths = [
        str(case_dir / "system"),
        str(case_dir / "constant"),
        str(case_dir / "0"),
    ]

    wf = WorkFlow('OpenFOAM_WorkFlow')

    wf.add_action( OpenFOAMAnalysis(
        name="my_job",
        job_flag=JobType.CREATE_MESH | JobType.RUN_SIMULATION,
        solve_cmd="icoFoam",
        mesh_cmd="blockMesh" ) )

    wf.add_action(  OpenFOAM_Field(  # Extract field of pressure values
        name='pressure',
        field_variable='p',
        time=0.5 ) )

    wa = WorkArea(wf, copy_paths=case_paths)

    return wa


of_graph = create_openfoam_graph()

# List graph variables and outputs
print("\n\nGraph variables:")
for v in of_graph.parameters():
        print(f" - {v}")

print("\n\nGraph outputs:")
for name, (eval_type, description) in of_graph.outputs().items():
    print(f' - {name}: {eval_type} — {description}')


# Run the job with new parameter values
print("\n\nRunning job.solve({'lidVelocity': 1.2, 'nCells': 6})...")
outcomes = of_graph.solve({"lidVelocity": 1.2, "nCells": 6})

# Print the computed field
print(f"Pressure field:", outcomes['pressure'] )


