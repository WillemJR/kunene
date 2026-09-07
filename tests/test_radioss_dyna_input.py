
import sys
from pathlib import Path

from kunene.graph_actions import WorkFlow, WorkArea
from kunene.radioss_using_dyna_inp import RadiossUsingDynaInput
from kunene.d3plot_actions import d3plot_File


def test_radioss_using_dyna_input():

    fe_path = Path(__file__).parent / "spring.k"
    if not fe_path.exists():
        exit(f"Error: {fe_path} does not exist.")

    wf = WorkFlow("SpringRadiossWorkFlow")

    run_rad = RadiossUsingDynaInput("RunSpring", cmd="rad_dyna_inp", input_path=str(fe_path),
                                    create_d3plot=True )
    wf.add_action(run_rad)

    d3p = d3plot_File("field")
    d3p.NodalValue("n5_disp",  state=1, nid=5, component="node_displacement")
    d3p.NodalValue("n5_coord", state=1, nid=5, component="node_coordinates")
    wf.add_action(d3p)

    wrk_area = WorkArea(wf, copy_paths=[str(fe_path)])

    params = {"floatpar1": 1.5, "intpar2": 800}
    print(f"Running RadiossUsingDynaInput with params: {params}")

    try:
        results = wrk_area.solve(params)
        print("Results:", results)
        assert results is not None
        assert "n5_disp" in results
        assert "n5_coord" in results
        print("n5 displacement:", results["n5_disp"])
        print("n5 coordinates: ", results["n5_coord"])
    except Exception as e:
        print(f"Simulation execution failed: {e}")
    wrk_area.rm_rundir()


if __name__ == "__main__":
    test_radioss_using_dyna_input()
