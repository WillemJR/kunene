"""
OpenFOAM remote execution example.

Runs the icoFoam lid-driven cavity case inside a Docker container and retrieves
the pressure field result.  Mirrors openfoam_example.py but delegates execution
to a remote server via RemoteAction.

Usage
-----
1. Build and start the server container (from the project root):

       docker build -f examples/remote/Dockerfile.openfoam -t kunene-openfoam .
       docker run --rm -p 50051:50051 kunene-openfoam

2. Run this script locally:

       python examples/remote/openfoam_remote_example.py
"""
import logging
logging.basicConfig(level=logging.WARNING)

from pathlib import Path
from kunene.remote_actions import RemoteAction

SERVER = 'localhost:50051'

case_dir = Path(__file__).parent.parent.parent / "tests" / "openfoam_exa"
if not case_dir.exists():
    exit(f"Error: case directory not found: {case_dir}")

# The three OpenFOAM case directories are sent to the server as-is.
# RemoteAction walks each directory and preserves the internal structure,
# so the server receives system/, constant/, and 0/ in its temp working dir.
case_paths = [
    str(case_dir / "system"),
    str(case_dir / "constant"),
    str(case_dir / "0"),
]

remote = RemoteAction(
    name="openfoam_remote",
    target_action_name="openfoam_sim",
    server_address=SERVER,
    copy_paths=case_paths,
)

# Discover what the server offers
print("Available server actions:")
for name, desc in remote.available_actions().items():
    print(f"  {name}: {desc}")

# Run the simulation remotely with parameter values
print("\nRunning remote OpenFOAM simulation ...")
result = remote.solve({"lidVelocity": 1.2, "nCells": 6})

# result is a dict {action_name: value} from WorkFlow.solve()
p_field = result.get('p')
print(f"Pressure field shape: {p_field.shape}")
print(f"Pressure range: [{p_field.min():.4f}, {p_field.max():.4f}]")
