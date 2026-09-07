# Overview
The 'kunene' python module is for modelling of complex simulations worklows.
The workflow consists of of actions assembled into a directed graph.
The actions can be varied; e.g. a strutural evaluations, a mathematical operation, or a file edit.
Part of the graph can be performed on remote computers. 
Some actions depends on the outcomes of other actions and workflow delays
the execution of these actions till the required prior actions have completed.



# Project Directory Structure

The project directory and core classes are given below.

```
kunene/
├── GEMINI.md
├── LICENSE
├── pyproject.toml
├── README.md
├── requirements.txt
├── docs/                   # documentation maintained using sphinx
├── kunene/                # python code directory
│   ├── GEMINI.md           # implementaion details of the classes
│   ├── __init__.py
│   ├── actions.py          # base class for all actions in the graph
│   ├── args.py             # enums, constaints and named tuples used in input arguments
│   ├── graph_actions.py    # graph containing sequence of actions.
│   ├── simulation_iterator.py # design studies: a job directory per design, and the index of those jobs
│   ├── dyna_actions.py     # execution of ls-dyna
│   ├── d3plot_actions.py   # read ls-dyna data from a d3plot file
│   ├── jinja_actions.py    # substition of variables in a file with jinja markup
│   ├── radioss_actions.py  #  execution of Radioss and openRadioss
│   ├── vtk_actions.py      #  read solver field/history data from VTK files
│   ├── openfoam_actions.py #  execution of OpenFOAM
│   ├── remote_actions.py   # remote execution
│   ├── cleanup.py          # removing bulk solver output from run directories
│   ├── variables.py        # variable definition
│   └── ...
└── tests/                  # unit tests
```



# The Action base class

The `WorkAction` base class (defined in `kunene/actions.py`) is the base building block for operations in a workflow. It inherits from `Subject` to support the observer pattern, allowing the workflow manager (like `DirectedGraph` or `WorkFlow`) to track execution status.

Key features:
- **`solve(self, val_dict)`**: The abstract method that performs the action's logic. It receives a dictionary `val_dict` containing the current values of variables and results from prior actions. This method returns the data computed by the class.
- **`parameters(self)`**: Returns a set of `Variable` objects that the action requires as inputs. For a `DirectedGraph` or `WorkFlow` the variables of all children are returned (duplicates are eliminated automatically). Solver actions (`DynaAnalysis`, `RadiossAnalysis`, `OpenFOAMAnalysis`, `JinjaReplace`) read their parameterised input file to discover variables; the file must be present in the current working directory. Call `parameters()` on a `WorkArea` or `SimulationIterator` to ensure files are copied first — both copy `copy_paths` into a temporary subdirectory before delegating to the graph.
- **`outputs(self)`**: Returns a `(data_type, description)` tuple describing what the action produces. For a `DirectedGraph` or `WorkFlow` it returns a dictionary `{action_name: (data_type, description)}` covering all child actions. Used to inspect graph outputs without running the graph.
- **`print_tree(self, describe=False)` / `format_tree(...)`**: Prints (or returns) the action graph as an ASCII tree, rooted at any action. Call it on a top-level action (e.g. a `SimulationIterator` or `WorkArea`) to see the whole workflow, including wrappers, graphs and leaf actions. Pass `describe=True` to append each action's description.
- **`print_work_dir(self)` / `format_work_dir(...)`**: Prints (or returns) the *predicted* work-directory structure that running the workflow creates on disk. It is built from the actions' metadata, so it works before anything is run and without the solvers installed. A `SimulationIterator` shows a representative `job_0/` directory (one per design evaluation); a `WorkArea` shows the single directory it reuses; a `DirectedGraph` whose children are wrappers shows each child's subdirectory as a nested subtree. Solver actions (`DynaAnalysis`, `RadiossAnalysis`, `OpenFOAMAnalysis`, `JinjaReplace`) contribute the deck/log/result files they write via a `_produced_files()` hook.
- **`describe_workflow(self, describe=False)`**: Convenience method that prints both the action tree and the work-directory structure together.
- **`report_progress(self, fraction=None, message=None)`**: called from inside `solve` by a long-running action to say where it is. The value reaches the graph's `status.json` (and from there a GUI, `watch_run`, or the per-job bars of a parallel study). Solver actions do this for you by tailing the solver output; a hand-written action calls it itself. A no-op outside a graph.
- **`_disposable_files(self)`**: Companion to `_produced_files()` used by cleanup. It names the *bulk* output (plot/animation databases, VTK) that may be deleted once the graph has run; decks, logs and small history files are never in it. Solver actions override it; the default declares nothing disposable.

Subclasses of `WorkAction` implement specific tasks, such as `MathEvaluation` (performing calculations), or `CurveSimilarity` (comparing simulation results to experimental data).

**Action names** must be valid Python identifiers (letters, digits and underscores, not starting with a digit, and not a Python keyword). Names become keys in `val_dict`, and `MathEvaluation` evaluates its expression against those names, so a name containing a space or other punctuation (e.g. `'m__case_1__TE all'`) would break the expression. Names are validated in `WorkAction.__init__` via `validate_action_name()`; an invalid name raises `ActionNameError`.

**Error handling.** All errors raised by kunene derive from `KuneneError` (defined in `kunene/errors.py`, re-exported from `kunene`): `ActionNameError` (invalid/duplicate names), `ParameterError` (missing/unresolvable parameter values), `EvaluationError` (a `MathEvaluation` expression failed), `SolverError` (an external solver run failed), `MissingPathError` (required file/directory not found; also a `FileNotFoundError`), and `DataNotFoundError` (requested result data not available, e.g. a d3plot component). Catch `KuneneError` to handle any workflow failure.

**Result structure and flattening.** A graph appends each action's output to `val_dict` under the action's name and returns it. Results are kept *structured*: a `WorkArea` (or a sub-graph) contributes its outputs as a nested dict stored under its own name, rather than flattening them into the parent. This preserves provenance and avoids name collisions between parallel branches (e.g. two solvers with same-named actions). Flattening is applied only where names must resolve textually: `MathEvaluation.solve` builds a flattened view of `val_dict` (via `_flatten_namespace`) for its `eval`, so an expression can reference an action nested inside a `WorkArea` directly by its name. Shallower names take precedence over deeper ones on a clash.

# Variables 
Actions can accept `Variable` objects as arguments during initialization.

The decorators `@WorkAction.allow_variables_as_arguments` and `@WorkAction.assign_variables_values_to_members` of an action are used to automatically resolve these variables to their numeric values from `val_dict` before `solve` is executed.


# Setting up a workflow
Actions are organized into a `DirectedGraph` or the simpler linear`WorkFlow` to define dependencies and execution order.

Asynchronous Execution: `_observed_eval_async` allows running the action in a separate process, which is useful for parallelizing independent tasks in a `DirectedGraph`. If an asynchronous action raises or its process dies, the graph marks it `failed` in the status file, terminates the other running children, and raises `AsyncActionError` with the child's traceback. `asynch` is a per-graph flag governing only that graph's immediate children: it is not inherited, so a nested graph runs serially unless it too sets `asynch=True`, and it parallelises the actions of one design point, never the jobs of a `SimulationIterator` (whose sweep loop is sequential). Children inherit the graph's working directory, so branches that write files need a `WorkArea` each; their results cross the process boundary through a `multiprocessing.Manager` dict and must be picklable. See "Child processes and start methods" below for what changes on Windows.


# Child processes and start methods

Two things run work in another process: an `asynch` `DirectedGraph` (a process per action) and a `SimulationIterator` with `max_workers` > 1 (a process per job). Both get their start method from `kunene/util/parallel.py`: `fork` where the platform has it, `spawn` where it does not (Windows always; Python 3.14 no longer defaults to fork on Linux either). `KUNENE_START_METHOD=spawn` forces the Windows path anywhere, which is how the test suite exercises it on Linux.

`fork` gives the child a copy of the calling process, so nothing has to be picklable. `spawn` starts a fresh interpreter and pickles the target and its arguments across, so the graph and its actions must be picklable: action classes belong in an importable module (a `.py` file on `sys.path`), not in the calling script or inside a function, and open files, sockets and handles belong in `solve` rather than in an action's attributes. kunene starts its spawned children (`parallel.start_process`, `parallel.start_manager`) without letting them re-import the calling script, so the script runs once, as written; a class or function defined in the script itself therefore cannot be found by the child, and `start_process` detects that before starting anything and raises `SpawnError` naming it. `KUNENE_SPAWN_IMPORTS_MAIN=1` (or `parallel.IMPORT_MAIN = True`) makes the child re-import the script instead, as stock `multiprocessing` does. kunene' own unpicklable state is handled for you: `WorkAction.__getstate__` drops the live `_async_proc`, `SimulationIterator.__getstate__` drops the results-root `StatusReporter` (the parent alone writes that file), and `StatusReporter.__getstate__`/`__setstate__` drop and rebuild the lock, event and heartbeat thread. Everything else — job directories, `status.json`, `job.log`, the progress bars, the index, the failure semantics — is identical on both start methods.


# Remote execution
The `kunene.remote_actions` module enables executing of actions on remote compute resources. It consists of the following:
- **`ServerAction` / `NamedServerAction` (Remote)**: A gRPC server that accepts tasks, executes them in isolated temporary directories, and returns results. It supports registering named graphs via `add_graph(name, graph, description)` to enforce a secure registry-based execution model.
- **`RemoteAction` (Client)**: A wrapper that specifies a `target_action_name` to execute a pre-registered action on the server. It retrieves the results and generated files. Discoverability of server-side actions is provided via `available_actions()`.
- Variable values and results cross the wire as restricted JSON (`kunene/serialization.py`), not pickle: only plain data types and numeric numpy arrays are accepted, so decoding a payload cannot execute code. Values outside the whitelist raise `SerializationError`.
- Remote progress: while a remote job runs, the client polls the server's `GetProgress` RPC and mirrors the remote status into the local `status.json` under the `RemoteAction`'s entry (fraction and a `remote <action>: ...` message). A GUI watching the local results tree sees remote progress without knowing about gRPC.


# Cleaning up run directories

Solver field output is what fills a disk during a study, so `WorkArea` and
`SimulationIterator` take a `cleanup` argument: a `Cleanup` policy
(`kunene/args.py`, re-exported from `kunene`) applied by
`kunene/cleanup.py` once a run has finished. The split is deliberate — the
*actions* declare which of their files are bulk output (`_disposable_files()`,
with a per-action `keep=[...]` constructor argument to subtract from it), while
the *work areas* decide when deleting is safe: only after the whole graph has
run, so a d3plot/VTK reader downstream of the solver has already read what it
needed, and never for a run that raised, whose deck and log are what you debug
it with. `Cleanup(remove=...)` selects `'bulk'` (the declared set, the default),
an explicit list of globs, or `Cleanup.ALL` (everything except protected files
and `keep`); `keep` always wins over `remove`; `dry_run=True` reports without
deleting. `actions_output.pkl`, `iter_variables.json`, `jobs_index.json` and
`status.json` (`args.PROTECTED_FROM_CLEANUP`) are never deleted, so
`results_for`, `collect` and `reuse_existing` keep working on a cleaned study.
The plan walks the action tree tracking each action's run directory, so a
`WorkArea` nested in a job directory is cleaned inside its own subdirectory (it
inherits the enclosing policy unless it sets its own), and a nested
`SimulationIterator` is left to clean its own jobs. `print_work_dir()` marks the
entries cleanup removes.

# Progress reporting

Long runs report progress through `status.json` files (`kunene/progress.py`), written atomically into the work directories so an external consumer (e.g. a GUI in a separate process) can poll them safely at any moment. A `DirectedGraph`/`WorkFlow` writes per-action states (`pending`/`running`/`done`/`failed`) into its run directory; a `SimulationIterator` writes job counts (`jobs_total`, `jobs_done`, `current_job`, `current_jobs`, state `running`/`idle`/`done`/`failed`) at the results root, where `current_jobs` lists the jobs running at that moment (more than one with `max_workers` > 1) and `current_job` the last one started. The counts belong to the batch being run, not to the iterator's lifetime: `collect_for_expdes`, `collect_for_varrange` and `solve_parallel` each set `jobs_total` and start `jobs_done` from zero, and each ends by writing the final state and releasing the results root, so a re-run of the same study — a new iterator on the same directory — owns the file and is the run a reader sees. A bare `solve()` is one design point and belongs to no batch, so it reports `jobs_total: null`. When a job of a parallel batch fails, the jobs terminated with it are marked failed in their own `status.json` by the parent (`progress.mark_failed`), since their processes are gone, and `current_jobs` is emptied. A heartbeat thread keeps `updated_at` fresh so a reader can tell a slow run from a dead one (`progress.is_alive`). Reader-side helpers: `StatusWatcher` (poll one file), `RunWatcher` (follow a results tree: root plus the job(s) running now; non-blocking `poll()` for GUI timers), `watch_run` (blocking generator for scripts), `format_status` (text rendering). The entries of a status file are the *actions*, never the containers holding them: a `DirectedGraph` and a `WorkArea` are pass-through (`WorkAction._progress_names`), so they hold no entry of their own and the actions inside them are registered instead. A graph nested in the same directory therefore does not write its own file at all -- it reports through the owner's reporter -- while a `WorkArea` writes its own file *and* reports into the enclosing graph's (`progress.MultiReporter`), so a job's progress follows the solver inside the work area rather than waiting for the whole area to finish. A `SimulationIterator` and a `RemoteAction` are not pass-through: they keep one entry and report their own fraction into it (jobs done, remote progress). Where more than one action runs at once (an `asynch` graph), `job_fraction` names them all: `3 of 5 running: rad_a (80%), rad_b (34%)`. Solver actions (`DynaAnalysis`, `RadiossAnalysis`, `RadiossUsingDynaInput`, `OpenFOAMAnalysis`) report percent-complete while running: a background thread (`progress.FileProgressTail`) polls the solver's redirected stdout, extracts the current simulation time (parsers in `kunene/util/solver_progress.py`), and reports `fraction` = time/termination-time plus a `message` like `time 12.9 of 40`; the termination time is read from the input deck (`*CONTROL_TERMINATION`, `/RUN` card, or `controlDict endTime`). If the deck or output cannot be parsed, the fraction simply stays `null`. Progress also works for `asynch` graphs: an action running in a child process writes per-action sidecar files that the owning process merges into `status.json` (see `kunene/GEMINI.md` for details).

# Results directory structure for SimulationIterator

The results directory structure is needed to postprocess and display results.

{NAME}/
├── jobs_index.json
├── job_0/             
│   ├── iter_variables.json
│   ├── actions_output.pkl
│   └── job.log          (only when the job ran in parallel: its stdout/stderr)
├── job_1/              
│   ├── iter_variables.json
│   ├── actions_output.pkl
│   └── job.log
├── job_{n}/              
│   ├── iter_variables.json
│   ├── actions_output.pkl
│   └── job.log


The {NAME} is a name of a directory that is input to the program, typically the name of the graph.
Inside that directory are the job subdirectories named job_0, job_1, ..., job_{n}.
Inside each subdirectory are files named iter_variables.json and actions_output.pkl.
We want to plot the data in these two files.

The iter_variables.json file store the variables values.
An example of iter_variables.json file:
"""
{"K": 0.2, "T": 75}
"""
The keys of the are strings, and the values are floats or integers.

The actions_output.pkl file store the compute values of named actions.
The actions_output.pkl is a binary file written using the pickle module.
The content of the file is a dictionary. 
and the values can be integers, floats, a numpy float, a list of floats,
a numpy 1D array of floats, or an image stored as an numpy array.

The jobs_index.json file at the results root indexes the job directories
(`kunene/simulation_iterator.py`, alongside `SimulationIterator` itself): one
record per job holding the job directory name, the
variable values it was run with, its state (`running`/`done`/`failed`), and any group
labels. It lets past results be retrieved by variable value and lets runs be grouped,
without re-running the graph — `SimulationIterator.results_for(vars)`,
`find_jobs(where=..., groups=...)`, `collect(groups=...)`, `add_groups`/`remove_groups`
— and backs `reuse_existing=True`, which returns a completed job's stored outputs
instead of evaluating the same design point again. Group labels are set with the
`groups` argument of the constructor, `solve` or `collect_for_*` (most specific wins),
or applied afterwards; a job can be in several groups. The index also supplies the next
job number, so an existing results directory is added to (jobs numbered after the ones
already there, never written over) rather than refused; `clean_start=True` deletes the
directory to start over. Because a design point can therefore appear in more than one
job, `results_for` and reuse resolve to the *most recent* matching job. The index is a
cache and is rebuilt from the job directories when missing (`job_index(rebuild=True)`); only the
group labels cannot be recovered that way. A sweep runs one design point at a time unless
`SimulationIterator(max_workers=N)` is given, which evaluates up to N jobs at once,
each in a child process with its own job directory (`solve` is a single design point
and always runs in the calling process). Only the parent allocates job numbers and
writes the index, so the numbering cannot race; in a terminal the batch also reports itself
as optional `tqdm` bars (`progress_bar=...` on `solve_parallel`, `collect_for_expdes` and
`collect_for_varrange`): one counting the jobs and one per running job, fed from
that job's own `status.json`, with each job's stdout/stderr redirected into
`job_N/job.log` so the terminal belongs to the bars; a job that fails terminates the jobs
still running and raises `AsyncActionError`, as a failing design point aborts a serial
sweep. The index recognises and numbers job
directories by their name prefix, so it must agree with `SimulationIterator.JNAME`:
`_index` is a property that syncs the prefix on every access, which is what lets
`JNAME` be changed after construction (`itr.JNAME = 'design_'`) without the index
losing sight of the directories and renumbering every job to 0.
