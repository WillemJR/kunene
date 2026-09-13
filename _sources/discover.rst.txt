
Discovering graph usage
--------------------------
The usage of a graph is described using the ``parameters()`` and ``outputs()`` methods.
The methods will return the variables and outputs for all the actions in the graph.

Retrieving the graph variables
==============================
The ``parameters()`` method returns the set of ``Variable`` objects that a graph
or action expects as inputs.

For actions that read parameterised input files (``DynaAnalysis``,
``RadiossAnalysis``, ``OpenFOAMAnalysis``, ``JinjaReplace``), the input file
must be present in the current working directory when ``parameters()`` is called.
``WorkArea`` and ``SimulationIterator`` both handle this automatically: they
copy all required files to a work directory before delegating to the graph.

Using ``WorkArea``:

.. code-block:: python

    from kunene.graph_actions import WorkFlow, WorkArea
    from kunene.dyna_actions import DynaAnalysis
    from kunene.d3plot_actions import d3plot_File

    wf = WorkFlow(name='SpringWorkFlow')
    wf.add_action(DynaAnalysis(name='RunSpring', input_path='spring.k'))
    wf.add_action(d3plot_File(name='field'))

    wa = WorkArea(wf, copy_paths=['path/to/spring.k'])
    for v in wa.parameters():
        print(v)

Using ``SimulationIterator``:

.. code-block:: python

    from kunene.graph_actions import WorkFlow, SimulationIterator
    from kunene.dyna_actions import DynaAnalysis

    wf = WorkFlow(name='SpringWorkFlow')
    wf.add_action(DynaAnalysis(name='RunSpring', input_path='spring.k'))

    itr = SimulationIterator(wf, copy_paths=['path/to/spring.k'])
    for v in itr.parameters():
        print(v)

Neither reads the deck in place, so the source files are never modified:
``WorkArea`` copies them into its work directory, and ``SimulationIterator``
into a ``variables_discovery`` subdirectory of the results root.  Each
variable carries its name, type, default value and a description:

.. code-block:: text

    Variable Name: floatpar1, Data Type: float, Value: 1.23, Description: 'From 'spring.k''
    Variable Name: intpar2, Data Type: int, Value: 789, Description: 'From 'spring.k''

The value is the default read from the deck.  The description is generated
by the action, not taken from the deck: every solver action records the
file the variable was found in, so it identifies the source rather than
describing the quantity.


Retrieving the graph outputs
=============================
The ``outputs()`` method returns information about what an action produces.
For a single action it returns a ``(eval_type, description)`` tuple.
For a ``WorkFlow`` or ``DirectedGraph`` it returns a dictionary
``{action_name: (eval_type, description)}`` covering all child actions.

.. code-block:: python

    from kunene.graph_actions import WorkFlow, WorkArea
    from kunene.dyna_actions import DynaAnalysis
    from kunene.d3plot_actions import d3plot_File

    wf = WorkFlow(name='SpringWorkFlow')
    wf.add_action(DynaAnalysis(name='RunSpring', input_path='spring.k'))
    wf.add_action(d3plot_File(name='field'))

    for name, (eval_type, description) in wf.outputs().items():
        print(f'{name}: {eval_type} — {description}')



Visualising the action graph
============================
Any action can print itself as a tree, rooted at that action. Call it on a
top-level action (such as a ``SimulationIterator`` or ``WorkArea``) to see the
whole workflow. The tree shows each wrapper, graph, and leaf action.

.. code-block:: python

    from kunene.graph_actions import WorkFlow, SimulationIterator
    from kunene.dyna_actions import DynaAnalysis
    from kunene.d3plot_actions import d3plot_File

    wf = WorkFlow(name='SpringWorkFlow')
    wf.add_action(DynaAnalysis(name='RunSpring', input_path='spring.k'))
    wf.add_action(d3plot_File(name='field'))

    itr = SimulationIterator(wf, work_area_path='results', copy_paths=['spring.k'])
    itr.print_tree()

.. code-block:: text

    SimulationIterator 'SpringWorkFlow_Iter'
    └── WorkFlow 'SpringWorkFlow'
        ├── DynaAnalysis 'RunSpring'
        └── d3plot_File 'field'

Pass ``describe=True`` to append each action's description to its node.


Visualising the work directory
==============================
The ``print_work_dir()`` method shows the directory layout that running the
workflow creates on disk. It is a *predicted* layout built from the actions'
metadata, so it works before anything is run and does not require the solvers
to be installed. A ``SimulationIterator`` shows a representative ``job_0/``
directory (one is created per design evaluation), while a ``WorkArea`` shows the
single directory it reuses.

.. code-block:: python

    itr.print_work_dir()

.. code-block:: text

    results/   (results root)
    ├── status.json   (run progress: current job, jobs done; see kunene.progress)
    ├── jobs_index.json   (job -> variable values and group labels; ...)
    ├── job_0/   (one directory per design evaluation)
    │   ├── iter_variables.json   (this design's variable values)
    │   ├── actions_output.pkl   (this design's action outputs)
    │   ├── spring.k   (copied in)
    │   ├── status.json   (live action states; see kunene.progress)
    │   ├── dyna_variables.json
    │   ├── dyna_action_inp.k
    │   ├── run_file.stdout
    │   ├── run_file.stderr
    │   ├── d3plot*
    │   └── d3hsp
    └── job_1/ … job_N/

When several sub-workflows run in their own directories — for example a
``DirectedGraph`` whose children are ``WorkArea`` or ``SimulationIterator``
wrappers — each child's directory appears as a nested subtree:

.. code-block:: python

    from kunene.graph_actions import DirectedGraph, WorkFlow, WorkArea
    from kunene.dyna_actions import DynaAnalysis

    def make_wa(i):
        wf = WorkFlow(name=f'WF{i}')
        wf.add_action(DynaAnalysis(name=f'run{i}', input_path='spring.k'))
        return WorkArea(wf, work_area_path=f'WF{i}', copy_paths=['spring.k'])

    graph = DirectedGraph('aGraph')
    graph.add_action(make_wa(1))
    graph.add_action(make_wa(2))
    graph.print_work_dir()

.. code-block:: text

    ./   (current working directory)
    ├── status.json   (live action states; see kunene.progress)
    ├── WF1/   (work area, overwritten each run)
    │   ├── spring.k   (copied in)
    │   ├── status.json   (live action states; see kunene.progress)
    │   ├── dyna_variables.json
    │   ├── dyna_action_inp.k
    │   ├── run_file.stdout
    │   ├── run_file.stderr
    │   ├── d3plot*
    │   └── d3hsp
    └── WF2/   (work area, overwritten each run)
        ├── spring.k   (copied in)
        └── ...   (the same files, written by run2)

Call ``describe_workflow()`` to print both the action tree and the work
directory structure together. The formatting helpers ``format_tree()`` and
``format_work_dir()`` return the same output as strings instead of printing it.

