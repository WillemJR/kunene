Running in parallel
===================

The solvers are where the time goes, and a workflow can run more than one
of them at a time at three levels, which combine:

* **The solvers used in a graph** — a graph that runs several solvers
  (a structural and a fluid analysis, the same model in two solvers,
  several load cases) runs them at the same time when the
  ``DirectedGraph`` that holds them is given ``asynch=True``.
* **The jobs of a design study** — a ``SimulationIterator`` with
  ``max_workers`` > 1 evaluates several design points at once, each in its
  own job directory.
* **Other machines** — a sub-graph wrapped in a ``RemoteAction`` runs on
  a server and its results come back into the local graph; see
  :doc:`remote`.

The first two are the subject of this chapter.  Both start ordinary
child processes on the local machine, and the section at the end,
:ref:`start-methods`, describes what those processes need from your
script — on Linux nothing; on Windows that the graph be picklable.

Which one to reach for follows from what is independent.  Solvers used
in the same graph belong in an ``asynch`` graph.  The jobs of a design
study belong to ``max_workers``.  A study whose graph uses several solvers
can use both, at the price of the cores it then needs: every job is a
whole graph, an ``asynch`` graph adds a process per solver inside each
job, and a solver may itself use several cores.


Solvers used in a graph: ``asynch``
-----------------------------------

A design is often evaluated by more than one solver run — OpenRadioss and
LS-DYNA on the same model, a structural analysis alongside a CFD one, the
same deck under several load cases — and those runs do not depend on one
another, only the step that combines their results does.  Run sequentially
they take the sum of their times; ``DirectedGraph(name, asynch=True)``
starts each of them in a child process as soon as its inputs are ready, so
the design takes about as long as its slowest solver.  The dependencies
are unchanged: an action still waits for the actions it was added with as
``parents``, so the solvers overlap and the step that merges their results
runs when all of them have finished.

.. code-block:: python

    from kunene.graph_actions import DirectedGraph, WorkFlow, WorkArea
    from kunene.actions import MathEvaluation

    dg = DirectedGraph('TwoSolvers', asynch=True)

    # The two solvers, each running in a directory of its own. radioss_flow
    # and dyna_flow are WorkFlows: the solver action followed by the
    # action that reads its results.
    rr = dg.add_action(WorkArea(radioss_flow, copy_paths=['spring.rad']))
    rs = dg.add_action(WorkArea(dyna_flow,    copy_paths=['spring.k']))

    # Combines their results, so it runs when both solvers have finished.
    dg.add_action(MathEvaluation('solver_diff', 'rad_n5 - dyna_n5'),
                  parents=[rr, rs])

    out = dg.solve({'K': 100.0})

Leave ``asynch`` out and the same graph runs the solvers one after the
other, with the same results.  Four things follow from the solvers running
in separate processes:

**Each solver needs its own directory.**  The children inherit the
graph's working directory and would all run in it, so each solver — with
the actions that read its output — is wrapped in a ``WorkArea``, as above,
or the solvers overwrite one another's decks and results.  An action that
only computes (a ``MathEvaluation``) needs none.

**The flag is not inherited.**  ``asynch`` governs the immediate children
of the graph it is given to.  A graph nested inside an ``asynch`` graph —
the ``radioss_flow`` inside its work area, say — runs its own actions one
after the other, which is what you want there: the reader of the results
has to wait for the solver anyway.  And a ``SimulationIterator`` around an
``asynch`` graph still evaluates its designs one at a time unless it is
given ``max_workers`` (next section).

**Results cross a process boundary.**  What each solver's work area
produces comes back through a ``multiprocessing.Manager`` dict, so it must
be picklable — numbers, arrays, lists and dicts of those are; an open file
handle is not.  The results are structured exactly as in a sequential run:
a ``WorkArea`` contributes its outputs as a nested dictionary under its
own name, which is what keeps two solvers with similar action names apart.

**A solver that fails stops the others.**  If a child raises, dies or
returns nothing — a solver that terminates with an error raises
``SolverError`` in its child — the graph marks it ``failed`` in
``status.json``, terminates the solvers still running (marked ``failed``
too, with a *terminated: a sibling action failed* message) and raises
``AsyncActionError`` carrying the child's traceback.

The progress of an ``asynch`` graph is reported like any other graph's:
each child writes what it knows about its own action to a sidecar file
that the graph merges into its ``status.json``, so each solver's
percent-complete shows up there while it runs, next to the others. A child
that is a ``WorkArea`` reports the actions *inside* it the same way, so the
graph's file names the solvers rather than the areas wrapping them.


Design study jobs: ``max_workers``
----------------------------------

``SimulationIterator(graph, max_workers=N)`` evaluates up to ``N`` design
points at the same time, each in a child process with its own job
directory:

.. code-block:: python

    itr = SimulationIterator(wf, copy_paths=['path/to/spring.k'], max_workers=4)
    pars, out = itr.collect_for_varrange({'K': [100., 200., 300., 400., 500.]})

The results are the same as with the default ``max_workers=1``, and come
back in the order the design points were given.  Only the sweep methods
(``collect_for_varrange``, ``collect_for_expdes`` and ``solve_parallel``,
which takes an explicit list of design points) fan out; ``solve`` is a
single design point and always runs in the calling process.

Job directories are numbered by the calling process alone, so the jobs
cannot collide over a number, and each job leaves its results in its own
directory as usual — ``results_for``, ``collect`` and ``reuse_existing``
(see :doc:`dirs`) see no difference between a job that ran in a child
and one that ran in the calling process.

A job that fails aborts the sweep, as it does when the jobs run one after
the other: the jobs still running are terminated, marked ``failed`` in the
index, and ``AsyncActionError`` is raised with the failing job's
traceback.  The jobs that completed before it keep their results, so the
sweep can be resumed with ``reuse_existing=True``.

Choose ``max_workers`` for what the machine can actually run: every job is
a full graph, a solver action may use several cores of its own, and an
``asynch`` graph adds processes inside each job.  A sweep of six two-second
jobs with ``max_workers=3`` is what ``examples/parallel_jobs.py`` runs; it
needs no solver installed.


Watching the jobs
~~~~~~~~~~~~~~~~~

In a terminal the batch reports itself as ``tqdm`` bars: one counting the
jobs of the batch, and under it a bar per job running right now::

    Study_Iter:  50%|█████████████            | 3/6 [00:42<00:41, 13.9s/job]
      job_3  rad 1 of 3: time 12.9 of 40 (97%)        32%|███████▎               |
      job_4  rad 1 of 3: time 11.4 of 40 (86%)        28%|██████▍                |
      job_5  rad 1 of 3: time  2.1 of 40 (16%)         5%|█▏                     |

A job's bar is fed from the ``status.json`` that job writes, so it follows
the job through its actions and shows a solver's percent-complete while one
runs (:func:`kunene.progress.job_fraction` is what turns those action
states into the one number the bar needs). An action of your own reports
itself the same way, by calling ``self.report_progress(fraction, message)``
inside ``solve``.

A job's line names the action running now and its place in the graph
(``rad 1 of 3``), then that action's own message and percentage. The bar's
own percentage is something else: the whole job, averaged over its actions
-- a solver 97% through the first of three actions leaves the job at 32%.

When several actions run at once -- the ``asynch`` graph above -- the line
names them all with their own percentages instead, since none of them is
*the* action running now::

      job_3  3 of 5 running: rad_a (80%), rad_b (34%), post   28%|██████▍   |

A solver wrapped in a ``WorkArea`` is followed just as closely. A work
area, like a sub-graph, holds no entry of its own in the enclosing
``status.json``: it reports the actions inside it there, as well as into
the ``status.json`` it writes in its own directory. So a job's bar shows
the solver in the work area rather than sitting at nothing until the whole
area is finished, and it makes no difference to the bars whether the
solvers of a study are wrapped in work areas or not.

The bars appear when tqdm is installed (``pip install kunene[progress]``)
and stderr is a terminal, so they never litter a log file; pass
``progress_bar=True``/``False`` to ``solve_parallel``,
``collect_for_expdes`` or ``collect_for_varrange`` to decide explicitly.
They are a convenience for watching a run go by — the ``status.json`` files
are written either way.

So that the bars keep their lines, a job running in parallel does not write
to the terminal: its stdout and stderr (the solver wrappers' messages, its
log records) are redirected into ``job_N/job.log``, which cleanup never
removes. A job run serially still writes to the terminal, where there are
no bars to disturb.

Each job writes its own ``status.json``, and the root ``status.json``
lists the jobs running at that moment in ``current_jobs``, so
:func:`kunene.progress.watch_run` — or a GUI polling the results tree
with :class:`kunene.progress.RunWatcher` — shows all of them at once,
from another process if need be.

The root file's counts are the *batch's*: ``jobs_total`` is the number of
design points this sweep was given and ``jobs_done`` counts from zero, so
running a second sweep on the same iterator starts the count again rather
than carrying on from the first.  A bare ``solve()`` is one design point
and belongs to no batch, so it reports ``jobs_total`` as null.  A sweep
ends by writing its final state and letting go of the results root, which
is what lets a re-run of the same study — a new ``SimulationIterator`` on
the same directory — take the file over and be the run a watcher sees.

When a job fails and the others are terminated with it, the terminated
jobs cannot report themselves: the parent marks each of their
``status.json`` files failed (every action that was running with them) and
empties ``current_jobs``, so the status files and ``jobs_index.json``
agree about what happened.


.. _start-methods:

Child processes and start methods
---------------------------------

The two places kunene runs work in another process — a sweep with
``max_workers`` > 1, and an ``asynch`` graph — start their children with
``fork`` where the platform has it and with ``spawn`` where it does not,
which on Windows is always.  ``kunene.util.parallel.get_context`` makes
the choice.

Under ``fork`` the child is a copy of the calling process and inherits
everything: the graph, the imports, the logging configuration, the working
directory.  Under ``spawn`` the child is a *fresh interpreter*, and the
graph and its actions travel to it as a pickle.  That asks one thing of the
caller: **keep the graph picklable, without the script.**  Define your
action classes in a module the child can import — any ``.py`` file on
``sys.path`` — not in the script that starts the run and not inside a
function, and keep open files, sockets and database handles out of an
action's attributes; build them in ``solve`` instead.  kunene' own
unpicklable state (progress locks, heartbeat threads, live child processes)
is dropped and rebuilt for you.

The script itself is not re-imported by the children (they need nothing
from it), so it runs once, as written::

    from kunene.graph_actions import WorkFlow, SimulationIterator
    from my_actions import Mesh, Solve       # an importable module

    wf = WorkFlow('Study', actions=[Mesh('mesh'), Solve('run')])
    itr = SimulationIterator(wf, max_workers=4)
    pars, out = itr.collect_for_varrange({'K': [100., 200., 300.]})

Had ``Mesh`` been defined in this script instead, the child could not find
it, and kunene refuses the start with a ``SpawnError`` naming the class
rather than letting the child die on it.  Move the class into a module;
or set ``KUNENE_SPAWN_IMPORTS_MAIN=1`` to have every child re-import the
script as stock multiprocessing does, in which case the script must not
start the run at top level.

Everything else is the same on both start methods: the job directories, the
``status.json`` files, the ``job.log`` redirect, the progress bars, the
index and the failure semantics.  Spawning is a little slower to start each
job, which matters only for jobs that are themselves quick.

Set ``KUNENE_START_METHOD=spawn`` to use the Windows path on Linux — to
reproduce a Windows problem, or in a process that has already started
threads and must not fork.
