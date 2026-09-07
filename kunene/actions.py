import os
import keyword
from pathlib import Path
from abc import ABC, abstractmethod
import numpy as np

from kunene.args import EvalType
from kunene.cleanup import marks_removed

import logging
logger = logging.getLogger(__name__)

from abc import ABC, abstractmethod

from kunene.util.observer import Subject, notify_observers
from kunene.util import parallel

from kunene.variables import Variable, UnknownVariable
from kunene.errors import ActionNameError, ParameterError, EvaluationError


# key under which a child process stores its traceback in the shared result
# dict of an asynchronous action
ASYNC_ERROR_KEY = '__kunene_async_error__'


def _async_eval_worker( action, val_dict, result_dict ):
    """
    Body of the child process of an asynchronous action: run the action's
    ``solve`` and leave the result (or the traceback) in the shared dict.

    A module-level function rather than a closure inside
    ``_observed_eval_async`` because under the ``spawn`` start method
    (Windows) the target and its arguments are pickled to reach a fresh
    interpreter, and a local function cannot be pickled. ``action`` is
    passed explicitly for the same reason -- under ``fork`` the child would
    have inherited it, under ``spawn`` it has to travel.
    """
    try:
        e = action.solve( val_dict )
    except BaseException as err:
        import traceback
        result_dict[ASYNC_ERROR_KEY] = (
            f'{type(err).__name__}: {err}\n{traceback.format_exc()}' )
        raise SystemExit( 1 )
    result_dict[action.name] = e


def validate_action_name( name ):
    """
    Ensure an action name can be used safely as a variable in expressions.

    Action names become keys in the ``val_dict`` that is passed to
    ``MathEvaluation``, whose ``solve()`` runs ``eval(cmd, None, val_dict)``.
    For a name to be referenceable there it must be a valid Python identifier
    (letters, digits and underscores, not starting with a digit) and must not
    be a Python keyword. A name such as ``'m__case_1__TE all'`` (embedded
    space) would break the ``eval`` and is rejected here.

    Arguments:
        name (str) : the proposed action name.
    Returns:
        str : the validated name (returned for convenience).
    """
    if not isinstance( name, str ) or not name:
        raise ActionNameError( f"Action name must be a non-empty string, got {name!r}." )
    if not name.isidentifier() or keyword.iskeyword( name ):
        raise ActionNameError( f"Invalid action name {name!r}. Action names must be valid "
              f"Python identifiers (letters, digits and underscores, not starting "
              f"with a digit, not a Python keyword) so they can be used in "
              f"MathEvaluation expressions." )
    return name


def _display_path( path ):
    """Show a work path relative to the current directory when it lives
    underneath it, otherwise as-is. Keeps nested directory trees readable."""
    try:
        return str( Path( path ).relative_to( Path.cwd() ) )
    except ValueError:
        return str( path )


def _copy_path_nodes( copy_paths ):
    """Build work-directory tree nodes for files/dirs that get copied in."""
    nodes = []
    seen = set()
    for cp in copy_paths:
        p = Path( cp )
        name = p.name + ( '/' if p.is_dir() else '' )
        if name in seen:
            continue
        seen.add( name )
        nodes.append( ( f'{name}   (copied in)', [] ) )
    return nodes


def _work_dir_label( name, cleanup, action ):
    """Label for a produced file in the work-directory tree, marked when
    the work area's cleanup deletes it again after the run."""
    if marks_removed( name, cleanup, action ):
        return f'{name}   (removed by cleanup)'
    return name


def render_tree( root ):
    """
    Render a tree as an ASCII string with ``├──``/``└──`` connectors.

    Arguments:
        root (tuple) : A ``(label, children)`` node, where ``label`` is a
            string and ``children`` is a list of the same ``(label, children)``
            node tuples.
    Returns:
        str : The rendered multi-line tree.
    """
    lines = [ root[0] ]
    _render_children( root[1], '', lines )
    return '\n'.join( lines )


def _render_children( children, prefix, lines ):
    for i, child in enumerate( children ):
        last = ( i == len( children ) - 1 )
        connector = '└── ' if last else '├── '
        lines.append( prefix + connector + child[0] )
        extension = '    ' if last else '│   '
        _render_children( child[1], prefix + extension, lines )


class WorkAction(Subject):
    """
    Base class for the nodes in the graph.
    Each action encapsulate an operations on the data stream.

    args:
        name (str) :
        cmd (str) :
        copy_paths (list) : List of file and directories to be copied to work area.
        lower_bound (float) : Lower bound on output value during design
        upper_bound (float) : Lower bound on output value during design
        keep (list) : Glob patterns of files this action produces that a
            work area's ``cleanup`` must never delete (see
            :class:`kunene.args.Cleanup`). E.g. ``keep=['d3plot']`` on a
            solver action keeps the first plot when the state files go.
    Returns:
        Any: outcome of operation
    """

    # A SimulationIterator numbers and cleans its own job directories; an
    # enclosing cleanup plan does not reach into them. See kunene.cleanup.
    _cleans_own_dirs = False

    # A container that only groups other actions holds no entry of its own
    # in a status file; the actions inside it are reported instead. See
    # _progress_names() below.
    _progress_passthrough = False

    def __init__( self, name, cmd=None, copy_paths=None, lower_bound=None, upper_bound=None,
                  description=None, data_type = EvalType.NOT_SPECIFIED, keep=None ):
        """
        """
        super().__init__()
        self.name = validate_action_name( name )
        self.cmd = cmd          # backward compatible with simulation
        # copy into a fresh list: graphs extend copy_paths in add_action, so a
        # shared default (or a caller's list) must never be mutated in place
        self.copy_paths = list( copy_paths ) if copy_paths else []
        # never deleted by a work area's cleanup, whatever it selects
        self.keep_files = [ keep ] if isinstance( keep, str ) else list( keep or [] )
        self.upper_bound = upper_bound # backward compatible with simulation
        self.lower_bound = lower_bound # backward compatible with simulation
        self.parent = None # typically workflow
        self.description = description if description is not None else (
            self.__class__.__doc__.strip() if self.__class__.__doc__ else ""
        )

        self._results = None

        # set by the enclosing graph before solve(); solver actions report
        # percent-complete through it (see kunene.progress)
        self._progress_reporter = None

        self.data_type = data_type
        self._par_dict = {}
        self._parameters_cache = None

    def _collect_arg_pars(self ):
        self._par_dict = {}
        for k,v in self.__dict__.items():
            if isinstance(v, Variable ):
                self._par_dict[k] = v
                self.__dict__[k] = v.value


    def _set_arg_pars(self, val_dict ):

        for k,v in self._par_dict.items():
            #breakpoint()
            if v.name in val_dict:
                self.__dict__[k] = val_dict[v.name]

    def allow_variables_as_arguments( func ):
        """ A decorator for the __init__() method allowing you to
        use variables as arguments constructing this class.

        The  arguments to a class can be declared to be variables,
        e.g. Action( name=, cmd=, arg1=FloatVariable( 'E', 123.4 ) )
        to be used as action.solve( {'E':3.} )
        This requires that the subclass must used the decorators
        allow_variables_as_arguments and
        assign_variables_values_to_members as:

        @WorkAction.allow_variables_as_arguments

        def __init__( self, name, cmd=None, v=None ):
            ...

        @WorkAction.assign_variables_values_to_members

        def solve(self,  val_dict=None ):
            ...
                    
        Child actions are created with the variables.

        You cannot do computations with the variables in
        __init__() because the values are only set at the end.
        """
        def wrapper( self, *args, **kwargs ):
            v = func( self, *args, **kwargs )
            self._collect_arg_pars() 
            return v
        return wrapper

    def assign_variables_values_to_members( func ):
        """ A decorator for the solve() method allowing you to
        use variables as arguments constructing this class."""
        def wrapper( self, val_dict ):
            self._set_arg_pars( val_dict )
            v = func( self, val_dict )
            return v
        return wrapper


    def _reset_class_member_vals(self,  val_dict):
        """
        Reset values of 'action_name.member' in class. val_dict can be {'img_extraction.zoom': 12.3} and
        if the class hass a member 'zoom' then that will be reset to 12.3

        OUTDATED: use allow_variables_as_arguments and assign_variables_values_to_members decorators
        """
        if val_dict is None: return
        needle = self.name+'.'
        for variable_name in val_dict:
            if needle in variable_name and variable_name.rfind( needle ) == 0:
                subkey = variable_name.replace( needle, '' )
                if subkey in self.__dict__:
                    self.__dict__[  subkey] = val_dict[variable_name]
                else:
                    raise ParameterError( f'Key \'{variable_name}\' not found in action \'{self.name}\'' )


    def report_progress( self, fraction=None, message=None ):
        """
        Report how far this action has got, from inside ``solve``.

        The enclosing graph gives every action a reporter before running
        it, so a long action can say where it is: ``fraction`` (0..1) and a
        short ``message`` reach the graph's ``status.json``, and from there
        a GUI, ``watch_run`` or the per-job bars of a parallel study. The
        solver actions do this for you by tailing the solver's output; a
        hand-written action calls this itself. It is a no-op when the
        action runs outside a graph.

        Arguments:
            fraction (float) : work done, 0..1. None leaves it unknown.
            message (str) : short status line, e.g. 'step 3 of 10'.
        """
        if self._progress_reporter is not None:
            self._progress_reporter.action_state( self.name, 'running',
                                                  fraction=fraction, message=message )

    def __getstate__( self ):
        """
        Pickle the action without the handles that only mean something in
        the process that created them.

        An action crosses a process boundary whenever the work runs under
        the ``spawn`` start method -- a job of a parallel
        ``SimulationIterator`` or an ``asynch`` action on Windows -- where
        the child is a fresh interpreter rather than a copy of this one.
        ``_async_proc`` is a live ``multiprocessing.Process`` and cannot be
        pickled at all; it belongs to the parent, which is where it is
        joined and terminated. Everything else travels, including the
        observers (the child re-runs the graph and its actions still notify
        their parent graph inside that child) and ``_progress_reporter``
        (which writes the child's sidecar files).
        """
        state = self.__dict__.copy()
        state.pop( '_async_proc', None )
        return state

    #@notify_observers
    def _observed_eval(self,  val_dict=None ):
        """
        used by the call graph to check if jobs have finished

        returns:
            dict : { self.name:..., .... } # including items in val_dict
        """
        self._results =  val_dict.copy()
        e = self.solve( val_dict )
        self._results[self.name] = e
        self._notify_observers( [self, 'Done'] )
        return self._results 

    def _observed_eval_async(self, val_dict=None):
        """
        Asynchronously run solve in a separate process.
        Notifies observers only when the process finishes: with 'Done' on
        success, with 'Failed' when the child raised, crashed, or produced
        no result. The error text (with traceback) is kept on
        self._async_error for the enclosing graph to raise.

        The start method comes from ``kunene.util.parallel``: ``fork``
        where the platform has it, ``spawn`` on Windows. Under ``spawn``
        this action and the values handed to it are pickled to reach the
        child, so an action holding something unpicklable can only run
        asynchronously on a forking platform.
        """
        import threading

        def watcher(proc, result_dict):
            proc.join()
            error = result_dict.get(ASYNC_ERROR_KEY)
            if error is None and proc.exitcode != 0:
                # hard death: segfault, oom-kill, terminate()
                error = f'child process exited with code {proc.exitcode}'
            if error is None and self.name not in result_dict:
                error = 'child process returned no result'
            results = dict(result_dict)
            results.pop(ASYNC_ERROR_KEY, None)
            # Update self._results in the main process
            self._results = results
            if error is not None:
                self._async_error = error
                logger.error(f"Asynchronous action '{self.name}' failed: {error}")
                self._notify_observers([self, 'Failed'])
            else:
                self._notify_observers([self, 'Done'])

        self._async_error = None
        ctx = parallel.get_context()
        manager = parallel.start_manager(ctx)
        result_dict = manager.dict(val_dict.copy() if val_dict else {})
        p = parallel.start_process(ctx, _async_eval_worker,
                                   args=(self, val_dict, result_dict))
        self._async_proc = p
        # Start watcher thread to notify when done
        t = threading.Thread(target=watcher, args=(p, result_dict), daemon=True)
        t.start()
        # Return immediately, results will be set when done
        return None

    @abstractmethod
    def solve(self,  val_dict: dict = None ) -> dict:
        """
        Solve/compute for action or graph.
        An action will return any computed results.

        A graph will append any computed results to
        val_dict and return that. 
        So A.solve( {'v1':1.2} ) may return {'v1':1.2, 'A':3.4},
        where the 'A':3.4 was added with 'A' the name of the action.

        Arguments:
            val_dict (dict) : variable values and input of any type.
        Returns:
            dict : dict with all results and inputs
        """
        assert 0, 'should not be called'

    @staticmethod
    def _append_unique_parameter( param_list, var ):
        """
        Append a Variable to param_list unless a parameter with the
        same name is already present.

        If an existing parameter has the same name and the same type,
        the duplicate is skipped. If one of them is an UnknownVariable
        and the other has a known type, the known type is kept. If an
        existing parameter has the same name but a different known type,
        an AssertionError is raised.

        Arguments:
            param_list (list) : list of Variable being built.
            var (Variable) : variable to append.
        """
        for i, existing in enumerate( param_list ):
            if existing.name == var.name:
                if type(existing) is type(var):
                    return
                if isinstance(existing, UnknownVariable):
                    # resolve the placeholder to the known type
                    param_list[i] = var
                    return
                if isinstance(var, UnknownVariable):
                    # keep the already known type
                    return
                assert False, (
                    f"Parameter '{var.name}' defined with conflicting types: "
                    f"{type(existing).__name__} and {type(var).__name__}"
                )
        param_list.append( var )

    @staticmethod
    def _merge_parameters( param_list, variables ):
        """
        Merge an iterable of Variable into param_list, skipping
        same-name/same-type duplicates and asserting on same-name/
        different-type conflicts.

        Arguments:
            param_list (list) : list of Variable being built.
            variables (iterable) : variables to merge in.
        Returns:
            list : param_list, for convenience.
        """
        for var in variables:
            WorkAction._append_unique_parameter( param_list, var )
        return param_list

    def parameters( self ):
        """
        These are the parameters defined for the WorkAction
        and used in the solve() method.
        For a graph this would be the parameters used in
        all the children.

        Returns:
            list : List of type Variable.
        """
        if self._parameters_cache is not None:
            return self._parameters_cache
        var_list = []
        for k, v in self._par_dict.items():
            self._append_unique_parameter( var_list, v )
        self._parameters_cache = var_list
        return var_list

    def _reduce_to_self_parameters( self, val_dict ):
        """
        Reduce a dictionary to only the entries whose keys match the
        names of this action's parameters.

        Use this in solve() to keep only the values relevant to this
        specific action, discarding any other values that may be
        passed down the workflow.

        Arguments:
            val_dict (dict) : variable values and input of any type.
        Returns:
            dict : new dict containing only keys that are parameter names.
        """
        if val_dict is None:
            return {}
        param_names = { v.name for v in self.parameters() }
        return { k: v for k, v in val_dict.items() if k in param_names }

    def results(self):
        return self._results

    def _dump(self,  val_dict=None ):
        pass

    def outputs(self):
        """
        Returns the output type and description of this action.

        Returns:
            tuple: (data_type, description)
        """
        return (self.data_type, self.description)

    def _check_names( self, name_list=None ):
        """ Cannot have duplicates -- create a problem with callbacks """
        if name_list is None: name_list = []
        if self.name in name_list: raise ActionNameError( f"Duplicate actions name \'{self.name}\'" )
        name_list.append( self.name )

    # ------------------------------------------------------------------
    # Progress: which names an action occupies in a status file
    # ------------------------------------------------------------------
    def _progress_names( self ):
        """
        The action names this action contributes to the enclosing graph's
        ``status.json``.

        An ordinary action reports itself, so it holds one entry and the
        graph sets its state. A container that only groups other actions --
        a ``DirectedGraph`` or a ``WorkArea`` -- is *pass-through*: it holds
        no entry of its own and reports the actions inside it instead, so a
        work area does not sit at 0% for as long as the solver inside it
        runs. Such containers set ``_progress_passthrough`` as well, which
        is what tells the enclosing graph not to set a state for them.

        A ``SimulationIterator`` and a ``RemoteAction`` are *not*
        pass-through: they keep one entry and report their own fraction
        into it (the jobs they have run, the progress of the remote graph).

        Returns:
            list : the names of the status entries this action fills.
        """
        return [ self.name ]

    # ------------------------------------------------------------------
    # Tree / directory-structure display
    # ------------------------------------------------------------------
    def _tree_children( self ):
        """
        The child actions used when printing the action tree.

        A plain action has no children. Wrappers (``WorkArea``,
        ``SimulationIterator``) and graphs (``DirectedGraph``,
        ``WorkFlow``) override this.

        Returns:
            list : list of child WorkAction instances.
        """
        return []

    def _tree_label( self, describe=False ):
        """
        Label for this action in the action tree.

        Arguments:
            describe (bool) : Append the action's description.
        Returns:
            str : label string.
        """
        label = f"{type(self).__name__} '{self.name}'"
        if describe and self.description:
            # keep the tree on one line per node
            first_line = self.description.strip().splitlines()[0]
            label += f"  — {first_line}"
        return label

    def _action_tree( self, describe=False ):
        """Build the ``(label, children)`` node for this action."""
        return ( self._tree_label( describe ),
                 [ c._action_tree( describe ) for c in self._tree_children() ] )

    def format_tree( self, describe=False ):
        """
        Return the action graph as an ASCII tree, rooted at this action.

        Call this on the top-level action (e.g. a ``SimulationIterator``)
        to see the whole workflow.

        Arguments:
            describe (bool) : Include each action's description.
        Returns:
            str : the rendered tree.
        """
        return render_tree( self._action_tree( describe ) )

    def print_tree( self, describe=False ):
        """Print the action graph as a tree (see :meth:`format_tree`)."""
        print( self.format_tree( describe ) )

    def _produced_files( self ):
        """
        Names of files/directories this action writes into its run
        directory. Best-effort prediction used for the work-directory
        display; solver actions override this. A trailing ``/`` marks a
        directory.

        Returns:
            list : list of names (str).
        """
        return []

    def _disposable_files( self ):
        """
        Names of the bulk output files this action writes that a work
        area's default ``Cleanup`` may delete once the graph has run.

        This is the *field* output -- the plot and animation databases that
        fill a disk during a study -- and never the input decks, the solver
        logs or small time-history files, which are what you need to
        understand a finished run. Solver actions override this; the
        default is to declare nothing disposable.

        Patterns are globs relative to the action's run directory, as in
        :meth:`_produced_files`. See :mod:`kunene.cleanup`.

        Returns:
            list : list of glob patterns (str).
        """
        return []

    def _cleanup_run_dir( self, base_dir ):
        """
        The directory this action's own files and children live in.

        A plain action runs in the directory it is given; a work area moves
        its subtree into its own directory and overrides this.

        Arguments:
            base_dir (str|Path) : directory the parent runs in.
        Returns:
            Path
        """
        return Path( base_dir )

    def _work_dir_entries( self, cleanup=None ):
        """Directory entries this action contributes, as tree nodes."""
        return [ ( _work_dir_label( f, cleanup, self ), [] )
                 for f in self._produced_files() ]

    def _work_dir_tree( self, cleanup=None ):
        """Build the ``(label, children)`` node for the work directory."""
        return ( './   (current working directory)', self._work_dir_entries( cleanup ) )

    def format_work_dir( self ):
        """
        Return the predicted work-directory structure as an ASCII tree.

        Shows the directories and files that running this action (or
        workflow) creates on disk. Files that the work area's ``cleanup``
        removes again once the run has finished are marked as such.

        Returns:
            str : the rendered directory tree.
        """
        return render_tree( self._work_dir_tree() )

    def print_work_dir( self ):
        """Print the predicted work-directory structure."""
        print( self.format_work_dir() )

    def describe_workflow( self, describe=False ):
        """
        Print both the action tree and the resulting work-directory
        structure for this (top-level) action.

        Arguments:
            describe (bool) : Include each action's description in the tree.
        """
        print( 'Action graph:' )
        self.print_tree( describe )
        print( '\nWork directory structure:' )
        self.print_work_dir()

    def __str__(self ):
        r = f'WorkAction: \'{self.name}\' {type(self)}'
        return r


def _flatten_namespace( val_dict ):
    """
    Build a flat ``name -> value`` namespace from a (possibly nested)
    ``val_dict`` for use as the local namespace of an ``eval``.

    Sub-graphs and ``WorkArea`` actions keep their results structured: their
    outputs live in a nested dict stored under their own name. To let a
    ``MathEvaluation`` expression reference those inner action outputs by
    name, every nested key is surfaced at the top level. Names defined at a
    shallower level take precedence over deeper ones on a name clash, so a
    top-level variable is never shadowed by a nested action of the same name.

    Arguments:
        val_dict (dict) : variable values and (possibly nested) results.
    Returns:
        dict : a new flat namespace.
    """
    ns = {}
    def _collect( d ):
        for v in d.values():
            if isinstance( v, dict ):
                _collect( v )
        ns.update( d )
    if val_dict:
        _collect( val_dict )
    return ns


class MathEvaluation(WorkAction):
    """
    Mathematical operation on results.

    The expression is evaluated against a flattened view of ``val_dict``:
    outputs of actions nested inside a ``WorkArea`` or sub-graph can be
    referenced directly by their action name.

    args:
        name (str) :
        cmd (str) :
    Returns:
        Any: outcome of operation
    """

    #def __init__( self, name, cmd ):
    #    super().__init__(name, cmd )

    def solve(self,  val_dict=None ):
        namespace = _flatten_namespace( val_dict )
        try:
            v = eval( self.cmd, None, namespace )
        except NameError as err:
            raise EvaluationError( f'Could not evaluate action \'{self.name}\'. Error is \'{err}\'. Either a named action was not defined or have not finished.' ) from err
        except Exception as err:
            raise EvaluationError( f'Error in MathEvaluation \'{self.name}\'. {err}' ) from err
        return v



