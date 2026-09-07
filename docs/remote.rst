Remote Execution
================

Kunene supports executing actions on remote compute resources using gRPC.
This allows you to offload computations to dedicated servers or containers while orchestrating the workflow locally.

Architecture
------------

The remote execution system consists of two main components:

1.  **Server (`ServerAction` or `NamedServerAction`)**: A process running on the remote machine that listens for incoming tasks. It executes each task in an isolated temporary directory. It holds pre-registered "named" graphs.
2.  **Client (`RemoteAction`)**: A special action type in your local workflow that refers to a pre-registered action on the server by name.

Setting up the Server
---------------------

On the remote machine (or container), you need to start the `ServerAction`. You can also register specific graphs that clients can then trigger by name.

.. code-block:: python

    from kunene.remote_actions import NamedServerAction
    from my_project import MyHeavyWorkflow

    # Start the server on port 50051
    server = NamedServerAction(port=50051)
    
    # Pre-register a workflow
    workflow = MyHeavyWorkflow("simulation_v1")
    server.add_graph("heavy_sim", workflow, "Runs the standard heavy simulation v1")
    
    print("Server is running...")
    server.start()
    
    # Keep the main thread alive
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop()

Defining Remote Actions
-----------------------

To run an action remotely, you use a `RemoteAction`. You must provide a `target_action_name` which corresponds to an action registered on the server.

Arguments:
    - ``name``: The name of the remote action wrapper.
    - ``target_action_name``: The name of a pre-registered action on the server.
    - ``server_address``: The address (`host:port`) of the remote server.
    - ``copy_paths``: A list of local file or directory paths to send to the remote server. Files are sent by basename; directories are walked recursively with their internal structure preserved.
    - ``output_patterns``: (Optional) A list of glob patterns for files to retrieve from the remote run directory.
    - ``progress_interval``: (Optional) Seconds between progress polls while the remote job runs. Default 2.


Discovering Available Remote Graphs
------------------------------------

If a server has pre-registered actions, you can query them from the client:

.. code-block:: python

    from kunene.remote_actions import RemoteAction

    # no target_action_name is needed just to ask what the server offers
    remote = RemoteAction(name="query", server_address='remote-host:50051')
    actions = remote.available_actions()
    for name, desc in actions.items():
        print(f"Action: {name} - {desc}")

Example: Using a Named Graph
-----------------------------

Using a named action reduces network overhead as the graph structure itself is already on the server.

.. code-block:: python

    from kunene.remote_actions import RemoteAction

    # Configure the remote wrapper using a named action
    remote_task = RemoteAction(
        name="remote_wrapper",
        target_action_name="heavy_sim",
        server_address='remote-host:50051',
        output_patterns=['results.csv']
    )

    # Execute
    result = remote_task.solve({'param1': 100})

Progress of remote jobs
-----------------------

A remote job can take hours; the blocking ``solve()`` call gives no feedback
by itself. While it runs, the ``RemoteAction`` polls the server's
``GetProgress`` RPC (every ``progress_interval`` seconds, default 2) from a
background thread and mirrors the remote job's status into the **local**
``status.json``: the ``RemoteAction``'s entry shows the fraction of the
remote action currently running and a message such as
``remote rad_solver: time 12.9 of 40``. A GUI watching the local results
tree (see :mod:`kunene.progress`) therefore shows remote progress without
knowing about gRPC. Polling failures are silently ignored; hard failures
are reported by ``solve()`` itself.

Implementation Details
----------------------

- **Isolation**: Each action runs in a unique temporary directory on the server. This prevents file conflicts between concurrent jobs.
- **Dependencies**: The remote environment must have `kunene` and all necessary dependencies installed.
- **File Transfer**: Large files (up to ~50MB) are supported by default. For very large datasets, consider using a shared file system or external storage service, passing only the paths in the `val_dict`.

Site security
--------------
A standard solution for security is not doable. It can be added however.
The main issue is that it will be
specified by the system adminitrator at every site.

.. warning::
    **Security Notice**: The provided feature should only be used within trusted networks (e.g., internal HPC clusters, VPNs). Variable values and results are exchanged as restricted JSON (see ``kunene/serialization.py``): only plain data types (dict, list, str, int, float, bool, None) and numeric numpy arrays are accepted, so decoding a payload cannot execute code. The channel itself is however unencrypted and unauthenticated: anyone who can reach the port can run the registered graphs and retrieve files matching the output patterns.



