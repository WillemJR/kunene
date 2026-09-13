
Work areas and design studies
=============================

Solver actions such as ``DynaAnalysis``, ``RadiossAnalysis``, and
``OpenFOAMAnalysis`` expect their input files to be present in the
current working directory when they run, and they leave their output
there.  Two classes decide where that directory is and what is kept from
a run: a ``WorkArea`` evaluates a graph once in one directory; a
``SimulationIterator`` evaluates it for every design point of a study,
each in a numbered job directory of its own, and keeps an index of them.
Both create the directory and copy the required files into it before the
graph runs.

``copy_paths`` — the files and directories to copy in — is always given
to the ``WorkArea`` or ``SimulationIterator``, not to the individual
solver actions.


WorkArea: one run in one directory
----------------------------------

``WorkArea`` evaluates a graph in a dedicated directory.
Each call to ``solve()`` cleans the directory and re-copies the required
files, so previous results are overwritten.  This is the right choice
for a single run or when you want to inspect intermediate files after
the run.

.. code-block:: python

    from kunene.graph_actions import WorkFlow, WorkArea
    from kunene.dyna_actions import DynaAnalysis
    from kunene.d3plot_actions import d3plot_File

    wf = WorkFlow('SpringWorkFlow')
    wf.add_action(DynaAnalysis(name='run', input_path='spring.k'))
    wf.add_action(d3plot_File(name='field'))

    # 'spring.k' is copied from its source location into ./SpringWorkFlow/
    # before the graph runs.
    wa = WorkArea(wf, copy_paths=['path/to/spring.k'])
    results = wa.solve({'K': 200.0})

By default the work directory is ``./{graph.name}`` — created relative to
the current directory at run time (not the directory in which the
``WorkArea`` was constructed). This means a ``WorkArea`` can be nested
inside a ``SimulationIterator``: it is created *inside* the current
``job_N`` directory rather than next to it. A custom path can be supplied
as the second argument::

    wa = WorkArea(wf, work_area_path='~/runs/spring', copy_paths=['path/to/spring.k'])

An explicit relative path is likewise resolved against the current
directory at run time, while an absolute path is used as given. The
directory path may contain ``~`` and environment variables; both are
expanded automatically.


SimulationIterator: a design study
----------------------------------

``SimulationIterator`` evaluates a graph once per design point, placing
each run in its own numbered subdirectory (``job_0``, ``job_1``, …).
The required files are copied into each job directory before the graph
runs.  Use this when you need to keep the results from every run, for
example during a parameter study or optimisation.


Job directories
~~~~~~~~~~~~~~~

.. code-block:: python

    from kunene.graph_actions import WorkFlow, SimulationIterator
    from kunene.dyna_actions import DynaAnalysis
    from kunene.d3plot_actions import d3plot_File

    wf = WorkFlow('SpringWorkFlow')
    wf.add_action(DynaAnalysis(name='run', input_path='spring.k'))
    wf.add_action(d3plot_File(name='field'))

    itr = SimulationIterator(wf, copy_paths=['path/to/spring.k'])

    # Each call to solve() creates a new job_N subdirectory.
    itr.solve({'K': 100.0})   # writes to SpringWorkFlow/job_0/
    itr.solve({'K': 200.0})   # writes to SpringWorkFlow/job_1/

The resulting directory layout is::

    SpringWorkFlow/
    ├── jobs_index.json
    ├── status.json                 # run progress; see kunene.progress
    ├── variables_discovery/        # copies used to read the deck's variables
    ├── job_0/
    │   ├── iter_variables.json
    │   ├── actions_output.pkl
    │   ├── status.json             # per-action progress for this job
    │   └── ...                     # the deck, the solver's output files
    ├── job_1/
    │   ├── iter_variables.json
    │   └── actions_output.pkl

Each job records the variable values it was run with
(``iter_variables.json``) and what its actions computed
(``actions_output.pkl``); ``jobs_index.json`` at the root lists the jobs
and is what the retrieval methods below use.  ``variables_discovery``
appears when the iterator has to discover the variables itself:
``parameters()`` copies ``copy_paths`` there and reads the deck.  Passing
``parameter_list`` explicitly skips it.

An existing results directory is added to.  Job numbers continue after
whatever is already there — taken from the index and the directories on
disk, not from a counter on the iterator — so a study can be extended in
a later session and a finished job is never written over:

.. code-block:: python

    itr = SimulationIterator(wf, copy_paths=['path/to/spring.k'])
    itr.solve({'K': 300.0})    # a later session: writes job_2, not job_0

Pass ``clean_start=True`` to delete the results directory (jobs, index
and all) before starting::

    itr = SimulationIterator(wf, copy_paths=['path/to/spring.k'], clean_start=True)


Running a sweep
~~~~~~~~~~~~~~~

``solve`` evaluates one design point.  A study is normally run as a
sweep, which evaluates a whole set of them and returns the results side
by side.  ``collect_for_varrange`` takes the values to try for each
variable and evaluates every combination:

.. code-block:: python

    pars, out = itr.collect_for_varrange({'K': [100., 200., 300.],
                                          'T': [10., 20.]})       # 6 jobs

    pars['K']      # array([100., 100., 200., 200., 300., 300.])
    pars['T']      # array([ 10.,  20.,  10.,  20.,  10.,  20.])
    out['field']   # [<job_0 result>, <job_1 result>, ...]

Two dictionaries come back: ``pars`` maps each variable name to an array
of the values it took, one entry per job, and ``out`` maps each action
name to a list of what that action computed, in the same order — the
order the design points were generated in.  So ``pars['K'][i]``,
``pars['T'][i]`` and ``out['field'][i]`` all describe the same job, and
the two can be handed to a plotting routine as they are.  ``out`` also
carries the variable values themselves (``out['K']``, as plain lists),
since each job's output starts from the values it was given.  An action
that returns a dictionary (a ``WorkArea`` nested in the graph, say) shows
up as a dictionary of lists.

``collect_for_expdes`` runs an experimental design you build yourself —
a list of value tuples and the variable names they belong to — for a
Latin hypercube, a sample from an optimiser, or any set of points that
is not a full grid:

.. code-block:: python

    points = [(100., 10.), (150., 25.), (300., 15.)]
    pars, out = itr.collect_for_expdes(points, ['K', 'T'])

Both methods take ``dependent_pars``, a dictionary of variables computed
from the swept ones by a Python expression, so a deck variable that must
follow another can be kept in step without being swept itself::

    pars, out = itr.collect_for_varrange({'K': [100., 200., 300.]},
                                         dependent_pars={'T': 'K / 10.'})

``solve_parallel`` is the third sweep method; it takes an explicit list of
``{variable name: value}`` dictionaries and returns one result dictionary
per point, and is what the other two use underneath.


Running jobs in parallel
~~~~~~~~~~~~~~~~~~~~~~~~

The jobs of a sweep run one at a time unless the iterator is given
``max_workers``, which evaluates that many design points at once, each in
a child process with its own job directory:

.. code-block:: python

    itr = SimulationIterator(wf, copy_paths=['path/to/spring.k'], max_workers=4)
    pars, out = itr.collect_for_varrange({'K': [100., 200., 300., 400., 500.]})

The results, the job directories and the index are the same as with the
default ``max_workers=1``, and come back in the same order.  See
:doc:`parallel` for the progress bars, the failure semantics and what a
child process needs on Windows.


Naming the job directories
~~~~~~~~~~~~~~~~~~~~~~~~~~

The job directories are named ``job_0 … job_N``; the prefix comes from
the class attribute ``SimulationIterator.JNAME`` and can be changed if
another name suits better — on the instance, or in a subclass when a
whole family of studies uses it::

    itr = SimulationIterator(wf, copy_paths=['path/to/spring.k'])
    itr.JNAME = 'design_'
    itr.solve({'K': 100.0})    # writes to SpringWorkFlow/design_0/

    class DesignIterator(SimulationIterator):
        JNAME = 'design_'

Change the prefix on an empty results directory.  Jobs already written
under the old prefix stay in the index and can still be read back by
variable value, but they no longer take part in the numbering, so the new
prefix starts its own series at ``0``.

Variable values are *not* encoded in the directory name — they are
recorded in ``jobs_index.json``, which is what the retrieval methods
below use.


Retrieving past runs
--------------------

``jobs_index.json`` at the results root maps each job directory to the
variable values it was run with, its state, and any group labels::

    { "jobs": [ { "job": "job_0",
                  "groups": [ "baseline" ],
                  "variables": { "K": 100.0 },
                  "state": "done",
                  "created_at": 1753.0, "updated_at": 1755.0 } ] }

Point a new ``SimulationIterator`` at an existing results directory and
ask it for results by variable value.  Nothing is executed:

.. code-block:: python

    itr = SimulationIterator(wf, work_area_path='SpringWorkFlow')

    out  = itr.results_for({'K': 100.0})    # the stored actions_output.pkl
    path = itr.find_job(where={'K': 100.0}) # SpringWorkFlow/job_0

``results_for`` prefers the job whose variables are exactly the ones
given, and otherwise accepts an unambiguous partial match, so
``results_for({'K': 100.0})`` works in a study that also varied ``T``
as long as only one job has that ``K``.  It raises ``DataNotFoundError``
when no job matches and when several do.

The index is a cache, never the authority.  ``itr.job_index(rebuild=True)``
re-derives it by reading the job directories, so result trees created
before the index existed keep working; only the group labels — which
exist nowhere else — are lost if the file is deleted.


Grouping runs
-------------

A job may carry any number of group labels.  Set a default for the
iterator, per sweep, or per job — the most specific wins:

.. code-block:: python

    itr = SimulationIterator(wf, groups='baseline')       # default for all jobs
    itr.collect_for_varrange({'K': [100., 200.]})         # -> 'baseline'
    itr.collect_for_varrange({'K': [300.]}, groups='stiff')
    itr.solve({'K': 400.}, groups=['stiff', 'hand_check'])
    itr.groups = 'later_runs'                             # from here on

Because the labels are metadata rather than directories, runs can be
grouped long after they finished, and one job can belong to several
groups:

.. code-block:: python

    itr.add_groups('converged', where={'K': 100.0})
    itr.add_groups('report', jobs=['job_0', 'job_3'])
    itr.remove_groups('stiff', jobs=['job_2'])
    itr.group_names()                    # ['baseline', 'converged', ...]

Read a group back in the same form a sweep returns — so grouped runs can
be plotted or post-processed like a fresh study:

.. code-block:: python

    pars, out = itr.collect(groups='baseline')
    # pars: {'K': array([100., 200.])}, out: {'disp': [...], ...}

    pars, out = itr.collect(groups=['baseline', 'converged'],
                            match_all_groups=True)   # in both groups
    jobs = itr.find_jobs(groups='report', state=None)


Reusing completed runs
----------------------

With ``reuse_existing=True`` a design point that already has a completed
job is not run again — its stored outputs are returned and its labels
extended, while new design points run as usual:

.. code-block:: python

    itr = SimulationIterator(wf, work_area_path='SpringWorkFlow',
                             reuse_existing=True, groups='study_2')
    pars, out = itr.collect_for_varrange({'K': [100., 500.]})
    itr.reused_jobs        # ['job_0']  — K=100 came off disk

The flag only decides whether computed design points are skipped; adding
to an existing results directory needs no flag.  Without it every design
point given is run, appended after the jobs already there — so the same
values can appear in several jobs, which is what you want when a deck or
a solver version changed.  Note that reuse matches on the *complete* set
of variable values, so a job that differs in any variable is treated as a
different design point.

A sweep that was aborted — by a failing job, or by you — is resumed the
same way: run it again with ``reuse_existing=True`` and only the design
points without a completed job are evaluated.


Cleaning up bulk solver output
------------------------------

It is easy to fill up a disk by keeping all the solver output in every job directory.
The solver output being the plot databases, the OpenRadioss animation files, the converted VTK, etc.
Pass a ``Cleanup`` policy to have them removed once a run has finished:

.. code-block:: python

    from kunene import Cleanup

    itr = SimulationIterator(wf, copy_paths=['path/to/spring.k'],
                             cleanup=Cleanup(keep=['d3plot']))

Each job is cleaned as soon as its graph has run to the end — not
before, so a ``d3plot_File`` action downstream of the solver has already
read what it needed. A job that failed is not cleaned, leaving you the deck and
solver log to you debug it with.  ``cleanup=True`` selects the
default policy; the default, ``None``, keeps everything as before.

**What may be removed.** The actions declare their own bulk output, so a
policy normally names no files at all:

``Cleanup(remove='bulk')``
    The default.  The field output the actions declare as disposable:
    ``d3plot*``, ``d3thdt*``, ``d3dump*``, ``runrsf*``, ``binout*`` for
    LS-DYNA; the animation and restart files (plus the d3plot and VTK
    conversions, when those are enabled) for OpenRadioss; ``VTK/`` and
    ``processor*`` for OpenFOAM.  Input decks, solver logs and small
    time-history files are never in this set — they are what a finished
    run is read back with.

``Cleanup(remove=['*.vtk', 'd3plot0*'])``
    Exactly those patterns, in every run directory, instead of the
    declared set.

``Cleanup(remove=Cleanup.ALL)``
    Everything except the protected files and ``keep``.  A job directory
    also holds files nothing declared — copied-in inputs, solver scratch —
    so this deletes more than kunene knows about.  Use it deliberately,
    and once with ``dry_run=True`` first.

**What you keep.** ``keep`` is a list of glob patterns that always wins
over ``remove``, so you subtract from the declared set rather than
enumerating what to delete::

    Cleanup(keep=['d3plot'])      # drop the state files, keep the first plot

The same argument exists on the solver actions themselves, next to the
knowledge of what those files are.  The action only *declares* it; the
work area still decides when to delete::

    DynaAnalysis(name='run', input_path='spring.k', keep=['d3plot', 'binout*'])

**What is never deleted.** ``actions_output.pkl``, ``iter_variables.json``,
``jobs_index.json`` and ``status.json``, whatever the patterns match.  A
cleaned study is still a study: ``results_for``, ``collect``,
``find_jobs`` and ``reuse_existing`` all keep working, since they read
the stored outputs and the index rather than the solver files.

**WorkArea.** The same argument works there, and is inherited by a work
area nested inside a ``SimulationIterator`` unless that work area sets its
own policy::

    inner = WorkArea(wf, cleanup=Cleanup(keep=['d3plot*']))   # overrides
    itr   = SimulationIterator(outer_graph, cleanup=True)

Note that a ``WorkArea`` empties its directory at the *start* of every
run in any case; ``cleanup`` is about what the last run leaves behind,
and about work areas nested in a study.

**Checking first.** ``Cleanup(dry_run=True)`` logs what it would remove
and removes nothing.  ``print_work_dir()`` marks the affected files, so
the predicted directory structure stays honest about what survives::

    itr.print_work_dir()

    SpringWorkFlow/   (results root)
    ├── status.json   (run progress: current job, jobs done; ...)
    ├── jobs_index.json   (job -> variable values and group labels; ...)
    ├── job_0/   (one directory per design evaluation)
    │   ├── iter_variables.json   (this design's variable values)
    │   ├── actions_output.pkl   (this design's action outputs)
    │   ├── dyna_variables.json
    │   ├── dyna_action_inp.k
    │   ├── run_file.stdout
    │   ├── run_file.stderr
    │   ├── d3plot*   (removed by cleanup)
    │   └── d3hsp
    └── job_1/ … job_N/

The mark is per pattern, not per file: with ``keep=['d3plot']`` the
``d3plot*`` line is marked even though the first plot survives.
