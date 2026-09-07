# Remote Execution Examples

This directory contains examples of running kunene actions on remote compute
resources via gRPC.  See `kunene/remote_actions.py` and `docs/remote.rst` for
full documentation.

---

## `remote_execution.py` — self-contained demo

Runs a minimal server and client in a single script using threads.  No Docker
or external process is needed.  Good starting point for understanding the
protocol.

```bash
python examples/remote/remote_execution.py
```

---

## OpenFOAM in Docker

Runs the lid-driven cavity icoFoam case inside a Docker container.  The client
sends the case directories to the server, which runs `blockMesh` + `icoFoam`
and returns the pressure field as a numpy array.

**Files:**

| File | Description |
|---|---|
| `Dockerfile.openfoam` | Builds an OpenFOAM image with kunene installed |
| `openfoam_remote_server.py` | gRPC server — runs inside the container |
| `openfoam_remote_example.py` | gRPC client — runs on the local machine |

### 1. Build the image

Run from the **project root**:

```bash
docker build -f examples/remote/Dockerfile.openfoam -t kunene-openfoam .
```

> The base image is `opencfd/openfoam-run:2312` (ESI OpenFOAM v2312, which
> ships `icoFoam` and `blockMesh`).  To use a different OpenFOAM release,
> edit the `FROM` line and the bashrc path in the `ENTRYPOINT`
> (`/usr/lib/openfoam/openfoam2312/etc/bashrc`).

### 2. Start the server container

```bash
docker run --rm -p 50051:50051 kunene-openfoam
```

The container sources the OpenFOAM environment and starts the gRPC server on
port 50051.

### 3. Run the client locally

In a separate terminal:

```bash
python examples/remote/openfoam_remote_example.py
```

The client:
1. Uploads `tests/openfoam_exa/system/`, `constant/`, and `0/` to the server.
2. Triggers the registered `openfoam_sim` action with `lidVelocity=1.2, nCells=6`.
3. Prints the shape and pressure range of the returned field.

### Customising the server address

The client defaults to `localhost:50051`.  Set `SERVER` at the top of
`openfoam_remote_example.py` to point at a remote host:

```python
SERVER = 'hpc-node-01:50051'
```

> **Security notice** — data is serialised as restricted JSON (plain data
> types and numeric numpy arrays only, so decoding cannot execute code) but
> transmitted over an unencrypted, unauthenticated gRPC channel.  Use only
> within trusted networks (VPN, HPC cluster internal network, etc.).
