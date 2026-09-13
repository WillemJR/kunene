
Examples 
========
In addition to the examples below, see the ``examples`` directory.

LS-DYNA 
----------------------------------

The example does an analysis, substitute parameter values, and extracts results. 

The study is parameterized using the \*PARAMETER keyword.

.. code-block:: python

    from kunene.graph_actions import WorkFlow, WorkArea
    from kunene.dyna_actions import DynaAnalysis
    from kunene.d3plot_actions import d3plot_File

    # 1. Define a workflow
    wf = WorkFlow("DynaWorkflow")

    # 2. Add an action to run LS-DYNA
    # The 'input_path' file contains *PARAMETER keywords for substitution
    run_dyna = DynaAnalysis("RunDyna", cmd='ls-dyna', input_path="model.k")
    wf.add_action(run_dyna)

    # 3. Add an action to extract results from d3plot
    d3p = d3plot_File('d3p')
    d3p.NodalValue('disp_n5', state=-1, nid=5, component='node_displacement')
    wf.add_action(d3p)

    # 4. Create a WorkArea; supply the source file via copy_paths.
    wa = WorkArea(wf, "./simulation_run", copy_paths=["path/to/model.k"])

    # 5. Discover variables (WorkArea copies files first so DynaAnalysis can read them)
    for v in wa.parameters():
        print(v)

    # 6. Execute
    params = {'VELOCITY': 10.0, 'THICKNESS': 2.5}
    results = wa.solve(params)

    # 'd3p' is itself a graph, so its extractions sit under
    # its name rather than being merged into the workflow's results.
    print(f"Displacement at node 5: {results['d3p']['disp_n5']}")


OpenRadioss 
----------------------------------

The example does an analysis, substitute parameter values, and extracts results. 

The study is parameterized using the /PARAMETER cards in
the OpenRadioss input deck.

.. code-block:: python

        from pathlib import Path

        from kunene.radioss_actions import RadiossAnalysis
        from kunene.d3plot_actions import d3plot_File
        from kunene.graph_actions import WorkFlow, WorkArea

        # Paths
        starter_deck = Path('models/cube_TYPE7_0000.rad')
        engine_deck  = Path('models/cube_TYPE7_0001.rad')

        # 1. Define RadiossAnalysis to run the simulation
        run_rad = RadiossAnalysis( name='rad',
                          starter_cmd='openradioss_starter',
                          starter_input_path=starter_deck,
                          engine_cmd='openradioss_engine',
                          engine_input_path=engine_deck,
                          create_d3plot=True )

        # 2. Create a workflow and add actions
        wf = WorkFlow( 'Radioss_WorkFlow' )
        wf.add_action( run_rad )

        d3p = d3plot_File( name='d3plot' )
        d3p.NodalValue(name='n5', state=1, nid=5, component= 'node_displacement'  )
        wf.add_action( d3p )

        wrk_area = WorkArea( wf, copy_paths=[starter_deck,engine_deck] )

        # Discover variables defined in input deck and other actions
        discovered_vars = wrk_area.parameters()
        print("Discovered variables:")
        for v in discovered_vars:
            print(f"  {v}")

        # 3. Execute the workflow. Provide values for the variables.
        ret = wrk_area.solve( { 'E': 210000.0, } )
        print( 'output', ret )

        # 'd3plot' is a reader graph, so its extractions sit under its name
        print( 'node 5', ret['d3plot']['n5'] )


Parameters with jinja markup
----------------------------------

A deck can be parameterised with jinja ``{{ }}`` markup instead of the
solver's own parameter cards.  ``JinjaReplace`` substitutes the values and
writes the deck the solver then runs; the two are simply chained in the
workflow.  This works for any solver — the use of jinja is never required:

.. code-block:: python

    from pathlib import Path

    from kunene.jinja_actions import JinjaReplace
    from kunene.radioss_actions import RadiossAnalysis
    from kunene.graph_actions import WorkFlow, WorkArea

    template    = Path('models/cube_TYPE7_tmpl.rad')   # holds '{{E}}'
    engine_deck = Path('models/cube_TYPE7_0001.rad')

    wf = WorkFlow('Radioss_Jinja')

    # substitutes {{E}} and writes the starter deck
    wf.add_action( JinjaReplace( name='prepare_deck',
                                 input_file_path=str(template),
                                 output_file_path='cube_TYPE7_0000.rad' ) )

    # ... which is what the solver reads
    wf.add_action( RadiossAnalysis( name='rad',
                                    starter_input_path='cube_TYPE7_0000.rad',
                                    engine_input_path=engine_deck ) )

    wa = WorkArea( wf, copy_paths=[template, engine_deck] )
    ret = wa.solve( {'E': 210000.0} )

``parameters()`` finds the jinja names too, but a template says nothing
about types or defaults, so they come back as ``UnknownVariable`` with a
value of ``None`` — unlike the parameter cards of a deck, which carry both.

``examples/jinja_dyna.py`` chains the same two actions for an LS-DYNA
keyword deck, with ``RadiossUsingDynaInput`` as the solver action so the
substituted deck is run by OpenRadioss:

.. code-block:: python

    from kunene.jinja_actions import JinjaReplace
    from kunene.radioss_using_dyna_inp import RadiossUsingDynaInput
    from kunene.graph_actions import WorkFlow, WorkArea

    wf = WorkFlow('JR_WorkFlow')

    # substitutes {{E}} and {{SIG_Y}} and writes the deck the solver reads
    wf.add_action( JinjaReplace( name='prepare_deck',
                                 input_file_path='tests/par_tens.k',
                                 output_file_path='edited.k',
                                 val_format="%10.3g" ) )

    wf.add_action( RadiossUsingDynaInput( name='RADIOSS',
                                          cmd='rad_dyna_inp',
                                          input_path='edited.k' ) )

    wa = WorkArea( wf )
    ret = wa.solve( {'E': 210.0, 'SIG_Y': 310.0} )


OpenFOAM
----------------------------------
The example does an analysis, substitute parameter values, and extracts results. 

The study is parameterized using the ``system/parameters`` file.
Case files (``system/``, ``constant/``, ``0/``) are supplied to ``WorkArea``
via ``copy_paths`` and are copied to the work directory before the solver runs.

.. code-block:: python

    from kunene.openfoam_actions import OpenFOAMAnalysis, OpenFOAM_Field
    from kunene.graph_actions import WorkFlow, WorkArea

    case_paths = ['path/to/case/system', 'path/to/case/constant', 'path/to/case/0']

    wf = WorkFlow('OpenFOAM_WorkFlow')
    wf.add_action(OpenFOAMAnalysis(name='run', solve_cmd='icoFoam'))
    wf.add_action(OpenFOAM_Field(name='p', field_variable='p', time=0.5))

    wa = WorkArea(wf, copy_paths=case_paths)

    # Discover variables from system/parameters (files are copied first)
    for v in wa.parameters():
        print(v)

    results = wa.solve({'lidVelocity': 1.2})

Extraction of field data and histories from OpenFOAM cases.

.. code-block:: python

    from kunene.openfoam_actions import OpenFOAM_Field, OpenFOAM_History
    from kunene.graph_actions import WorkFlow

    wf = WorkFlow('OpenFOAM_Extraction')

    # 1. Extract a field at a specific time
    field_ext = OpenFOAM_Field(
        name='temp_field',
        field_variable='T',
        time=50
    )
    wf.add_action(field_ext)

    # 2. Extract history for a specific point index
    hist_ext = OpenFOAM_History(
        name='temp_history',
        field_variable='T',
        point_idx=10
    )
    wf.add_action(hist_ext)

    # 3. Run the extraction
    results = wf.solve({})
    print(f"Field data: {results['temp_field']}")
    print(f"History data: {results['temp_history']}")


