
Variables
=========
Variables are used to parameterize the simulation for design changes,
creating ML training data, and design optimization.

Typically the variable are read from the input decks; for example, \*PARAMETER definitions in LS-DYNA.
You can also define a variable for use.

The following types of variables are available: ``FloatVariable``, ``IntSetVariable``, and ``StrSetVariable``.


Members of a variable
----------------------

Every variable carries four members, from the ``Variable`` base class:

``name``
    The variable's name.  It is the key the value is looked up under in the
    ``val_dict`` passed to ``solve()``, so it must match the name in the
    deck.
``value``
    The current value.  It is a property: assigning to it is checked
    against the variable's bounds or its set of allowable values.
``type``
    The Python type the variable holds — ``float``, ``int`` or ``str``.
``description``
   Each variable has an optional ``description`` argument.
   If not provided, the class docstring is used as the description.

``FloatVariable(name, value, upper_bound=None, lower_bound=None, description=None)``
is the continuous variable.  It adds:

``lower_bound``, ``upper_bound``
    The range the value must stay within.  ``None`` (the default) leaves
    that side unbounded.

A value outside the range is refused at construction with a ``ValueError``,
and on assignment with an ``AssertionError``:

.. code-block:: python

    from kunene.variables import FloatVariable

    t = FloatVariable('T', 75., lower_bound=10., upper_bound=200.)
    t.value = 80.                                 # fine
    t.value = 500.                                # AssertionError
    FloatVariable('T', 500., upper_bound=200.)    # ValueError

``IntSetVariable(name, value, allowable=None, description=None)`` and
``StrSetVariable(name, value, allowable=None, description=None)`` are the
discrete variables — an integer and a string chosen from a fixed set.  Both
add:

``allowable``
    The set of values the variable may take.  A list or tuple is converted
    to a set.  If it is omitted or empty it becomes ``{value}``, which pins
    the variable to its initial value — rarely what is wanted, so pass the
    set explicitly.

.. code-block:: python

    from kunene.variables import IntSetVariable, StrSetVariable

    n = IntSetVariable('N', 2, [1, 2, 3])
    n.allowable                        # {1, 2, 3} — the list became a set
    n.value = 3                        # fine
    n.value = 7                        # AssertionError

    m = StrSetVariable('M', 'foo', ('foo', 'bar'))
    m.allowable                        # {'foo', 'bar'}

    IntSetVariable('N', 5).allowable   # {5}: no set given, so pinned to 5

Only assignment is checked, never the initial value, so
``IntSetVariable('N', 9, [1, 2])`` is built without complaint and holds a
value outside its own set.  The checks on assignment are ``assert``
statements, which Python removes when run with ``-O``.

``parameters()`` returns an ``UnknownVariable`` when the deck gives neither
a type nor a default — a jinja ``{{ }}`` name, or an OpenRadioss expression
parameter.  It has ``name``, ``value`` and ``description`` but its ``type``
is ``None``, and its ``value`` is read-only.


Variable discovery
------------------

Both ``WorkArea`` and ``SimulationIterator`` support variable discovery
via ``parameters()``.  The files are copied first so that solver actions
can read their input files: ``WorkArea`` copies them into its work
directory, ``SimulationIterator`` into a ``variables_discovery``
subdirectory of the results root.  The originals are never modified.

.. code-block:: python

    from kunene.graph_actions import WorkArea, SimulationIterator

    wa = WorkArea(wf, copy_paths=['path/to/spring.k'])
    for v in wa.parameters():
        print(v)

    itr = SimulationIterator(wf, copy_paths=['path/to/spring.k'])
    for v in itr.parameters():
        print(v)

See :doc:`discover` for more details on variable and output discovery.



Using a variable as an argument to an action
--------------------------------------------

First, you have to define the variable, then you can use it to control actions.

.. code-block:: python

        from kunene.args import Location
        from kunene.variables import FloatVariable
        from kunene.openfoam_actions import OpenFOAM_Field

        # define variable
        etime = FloatVariable( 'ET', 40. )
        # The variable can be used to construct an action
        # Below it is used to control an extraction time
        f = OpenFOAM_Field( name='TF', field_variable='T',
                            time=etime, location=Location.NODAL)

        # the value given to solve() replaces the variable
        f.solve( {'ET':50} )

The action reads the OpenFOAM case in the current working directory, which
is the work area the graph runs in, so there is no case-directory argument:
put the case there with the ``copy_paths`` of a ``WorkArea`` or a
``SimulationIterator``.

The two decorators that make this work are
``@WorkAction.allow_variables_as_arguments`` on ``__init__`` and
``@WorkAction.assign_variables_values_to_members`` on ``solve``.  An action
that should accept variables as constructor arguments needs both.


