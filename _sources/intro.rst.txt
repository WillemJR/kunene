
Overview
--------

`Kunene` enables the automation and coordination of
multi-physics simulation workflows — from input preparation and
remote execution to results extraction and post-processing.
The module is particularly suited for simulations that span
multiple domains, such as combined structural and fluid dynamics analyses.

The solvers a design needs can run at the same time, and a simulation
iterator can evaluate the whole graph for several design points at once,
each in its own job directory.

`Kunene` has a native support for solvers like LS-DYNA, OpenRadioss, and OpenFOAM. In addition OpenRadioss using LS-DYNA input is supported as a special case.

Key Features
------------

- **Workflow Management**: Define simulation workflows as directed acyclic graphs (DAGs) where actions are executed based on dependency relationships and completion status of prerequisite tasks
- **Native Solver Support**: Specify input parameter values and the results to extract for a supported solver.  Currently implemented are LS-DYNA and OpenRadioss for structural analysis, and OpenFOAM for computational fluid dynamics
- **Parallel Execution**: Evaluate several design points of a study concurrently, each job in its own directory with its own progress bar
- **Results Extraction**: Read from the solvers' result databases in the graph. Supported are: LS-DYNA d3plot, OpenRadioss VTK and time-history CSV, and OpenFOAM fields and histories.
- **Remote Execution**: Submit computational subgraphs to remote computing resources while maintaining local workflow coordination
- **Custom Actions**: Add an operation of your own by subclassing `WorkAction` and writing `solve(val_dict)`; it then behaves like any built-in action.
- **Discoverability**: Query any graph for its inputs and outputs without running it — solver actions read their parameterised input files to report variable names, types, and default values

Requirements
------------

`Kunene` has so far only been tested on Linux and WSL.
Windows testing is not yet complete; use WSL.


Typical Workflow
----------------

The user usually need to:

1. Parameterize input files for target solvers
2. Define the actions and their dependencies

The typical `kunene` steps are:

1. Update parameter values with the values for the current design
2. Execute simulations on designated compute resources (local or remote), possibly in parallel
3. Extract relevant results from solver outputs
4. Aggregate, summarize, and postprocess findings 




