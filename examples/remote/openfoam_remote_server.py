"""
OpenFOAM gRPC server — runs inside the Docker container.

The server registers a WorkFlow containing OpenFOAMAnalysis (blockMesh + icoFoam)
and OpenFOAM_Field (pressure extraction at t=0.5).  The client sends the case
directories (system/, constant/, 0/) via copy_paths, which are written to an
isolated temp directory before solve() is called.

Start via the Dockerfile ENTRYPOINT; see Dockerfile.openfoam.
"""
import logging
logging.basicConfig(level=logging.INFO)

from kunene.args import JobType
from kunene.graph_actions import WorkFlow
from kunene.openfoam_actions import OpenFOAMAnalysis, OpenFOAM_Field
from kunene.remote_actions import NamedServerAction


def build_workflow():
    wf = WorkFlow('OpenFOAM_WorkFlow')
    wf.add_action(OpenFOAMAnalysis(
        name="my_job",
        job_flag=JobType.CREATE_MESH | JobType.RUN_SIMULATION,
        solve_cmd="icoFoam",
        mesh_cmd="blockMesh",
    ))
    wf.add_action(OpenFOAM_Field(
        name='p',
        field_variable='p',
        time=0.5,
    ))
    return wf


if __name__ == "__main__":
    server = NamedServerAction(port=50051)
    server.add_graph(
        "openfoam_sim",
        build_workflow(),
        "Runs blockMesh + icoFoam, returns pressure field (numpy array) at t=0.5",
    )
    print("OpenFOAM server listening on port 50051 ...")
    server.start()
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop()
