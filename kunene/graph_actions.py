from pathlib import Path
import os,shutil
import json
import time
from abc import ABC, abstractmethod
from collections import defaultdict

from kunene.actions import WorkAction, _display_path, _copy_path_nodes
from kunene.args import Cleanup
from kunene.cleanup import clean_run_dir
from kunene.errors import ActionNameError, MissingPathError, AsyncActionError
from kunene.progress import MultiReporter, StatusReporter
from kunene.util.observer import Observer
# SimulationIterator lives in its own module (with the job index it relies
# on) but is re-exported here: it is part of this module's public interface
# and 'from kunene.graph_actions import SimulationIterator' is what
# existing workflows use.
from kunene.simulation_iterator import SimulationIterator
import kunene.args

import logging
logger = logging.getLogger(__name__)


class WorkArea(WorkAction):
    """
    Evaluates graph in a seperate directory.
    Previous results in the directory are overwritten.
    Required files may be copied to this area.

    Use SimulationIterator if you wish to have a subdirectory
    for each design evaluated.

    Arguments:
        graph (DirectedGraph) : DirectedGraph or WorkFlow  
        work_area_path (str) : Default is to ./{graph.name}
        copy_paths (list) : List of names of file to be copied to work area.
        cleanup (Cleanup) : remove bulk solver output from the work area
            once the graph has run. See :class:`kunene.args.Cleanup`;
            ``True`` selects the default policy, ``None`` (the default)
            keeps everything. Nothing is removed if the run raises, so a
            failed run can still be debugged. Note that the work area is
            emptied at the *start* of every run in any case: cleanup is
            about what the *last* run leaves behind, and about work areas
            nested inside a ``SimulationIterator``, where it is inherited
            from the iterator unless set here.
    Returns:
        dict: Output from graph (it adds nothing).
    """

    # the graph inside reports the actions; the work area holds no entry
    # of its own (see WorkAction._progress_names)
    _progress_passthrough = True

    def __init__( self, graph, work_area_path=None, copy_paths=None, cleanup=None ):

        assert isinstance( graph, DirectedGraph )
        super().__init__( graph.name+'_WorkArea', "", copy_paths=( copy_paths or [] )+graph.copy_paths )
        self.graph = graph
        self.cleanup = Cleanup.coerce( cleanup )
        if work_area_path is None:
            # Keep the default relative (./{graph.name}) so the work area is
            # created under the *current* directory at run time. This lets a
            # WorkArea nest inside a SimulationIterator's per-design job
            # directory instead of being created next to it (baking in the
            # cwd at construction time put it outside the job directory).
            work_area_path = self.graph.name

        # Expand ~ and environment variables
        expanded_path = os.path.expandvars(os.path.expanduser(str(work_area_path)))
        self.work_area_path = Path(expanded_path)

        self.description = f'Work area for graph {graph.name} at {self.work_area_path}'


    def _prepare_work_area(self):
        """Create the work area directory and copy all required files into it."""
        self.wa_path = self.work_area_path
        self.wa_path.mkdir(mode=0o777, parents=True, exist_ok=True)

        if self.copy_paths:
            logger.debug(f'Copying paths {self.copy_paths}')
            for fname in self.copy_paths:
                src = Path(fname)
                if not src.exists():
                    raise MissingPathError(f'Path "{fname}" not found. Either the path does not exist or you must specify the full path.')
                if src.is_dir():
                    shutil.copytree(src, self.wa_path / src.name, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, self.wa_path)

    def parameters(self):
        if self._parameters_cache is not None:
            return self._parameters_cache
        self._prepare_work_area()
        root_dir = Path.cwd()
        os.chdir(self.wa_path)
        try:
            vrs = self.graph.parameters()
        finally:
            os.chdir(root_dir)
        self._parameters_cache = vrs
        return vrs

    def outputs(self):
        return self.graph.outputs()

    def _progress_names( self ):
        return self.graph._progress_names()


    def rm_rundir( self ):
        sim_path = self.work_area_path
        if sim_path.exists():  shutil.rmtree( sim_path )

    def _clean_inrundir( self ):
        sim_path = self.work_area_path
        if sim_path.exists():
            for item in sim_path.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()


    def solve(self,  val_dict=None ):
        """
        Returns:
            dict: Output from graph (it adds nothing).
        """

        if self.work_area_path.exists():
            self._clean_inrundir()

        self._prepare_work_area()

        root_dir = Path.cwd()
        os.chdir( self.wa_path )
        logger.debug( f'Running in directory {self.wa_path}' )

        # The graph writes its own status.json in here, and reports to the
        # enclosing graph's file as well: a work area is pass-through for
        # progress, so what the job directory shows is the solver inside it
        # rather than one entry that sits at nothing until the area is done.
        self.graph._progress_reporter = self._progress_reporter

        try:
            ret = self.graph.solve( val_dict )
        finally:
            os.chdir( root_dir )

        # only once the whole graph has run (every reader of the solver's
        # output has had it), and only if it ran to the end: the files of a
        # failed run are what you debug it with
        clean_run_dir( self, root_dir, self.cleanup )

        return ret

    def _check_names( self, name_list=None ):
        """ Cannot have duplicates -- create a problem with callbacks """
        if name_list is None: name_list = []
        if self.name in name_list: raise ActionNameError( f"Duplicate actions name \'{self.name}\'" )
        name_list.append( self.name )
        self.graph._check_names( name_list )

    def _tree_children( self ):
        return [ self.graph ]

    def _cleanup_run_dir( self, base_dir ):
        # a relative work area path is resolved against the directory the
        # parent runs in, exactly as it is at run time
        path = Path( self.work_area_path )
        return path if path.is_absolute() else Path( base_dir ) / path

    def _effective_cleanup( self, cleanup ):
        """This work area's own policy, or the one inherited from the
        enclosing work area."""
        return self.cleanup if self.cleanup is not None else cleanup

    def _work_dir_tree( self, cleanup=None ):
        cleanup = self._effective_cleanup( cleanup )
        children = _copy_path_nodes( self.copy_paths ) + self.graph._work_dir_entries( cleanup )
        return ( f'{_display_path(self.work_area_path)}/   (work area, overwritten each run)', children )

    def _work_dir_entries( self, cleanup=None ):
        # A WorkArea creates its own directory: contribute it as a subtree
        # rather than flattening files into the parent directory.
        return [ self._work_dir_tree( cleanup ) ]





# -------------------

class DirectedGraph(WorkAction, Observer):
    
    """
    A directed graph used to determine the order of evaluation and
    dependencies.

    Used e.g. for MDO for solvers run in parallel.

    A graph will append any computed results to
    val_dict and return that. 
    So A.solve( {'v1':1.2} ) may return {'v1':1.2, 'A':3.4},
    where the 'A':3.4 was added with 'A' the name of the action.

    Arguments:
        name (str) : name of the graph; must be a valid Python identifier.
        asynch (bool) : run the children of *this* graph concurrently.
            Every child whose parents have finished is started at once, in
            a child process (there is no limit on how many run together);
            the default False evaluates them one after the other. The flag
            belongs to this graph only: it is not inherited, so a graph
            nested in an ``asynch`` graph runs its own children serially
            unless it also sets ``asynch=True``, and it parallelises the
            actions of one design point, never the jobs of a
            ``SimulationIterator``. Three consequences of running in
            another process: the children's results travel back through a
            ``multiprocessing.Manager`` dict and so must be picklable; the
            children inherit this graph's working directory and therefore
            all run in it, so branches that write files need a ``WorkArea``
            each (or distinct file names) to avoid overwriting one another;
            and a child that raises, dies or returns nothing terminates its
            running siblings and raises ``AsyncActionError``. The child is
            forked where the platform has fork and spawned otherwise
            (Windows, see ``kunene.util.parallel``); spawning also
            requires the *action itself* to be picklable, with its class
            in an importable module rather than in the calling script,
            which the child does not re-import.
        work_area_path (str) : run the graph in this directory instead of
            the current one, by wrapping it in a ``WorkArea``.
        cleanup (Cleanup) : only meaningful together with
            ``work_area_path``; passed to the work area it creates.
    Returns:
        dict: Dictionary containing action_name:action_result pairs
    """ 

    # a graph groups actions; those are what a status file shows, not the
    # graph itself (see WorkAction._progress_names)
    _progress_passthrough = True

    def __init__(self, name, asynch=False, work_area_path=None, cleanup=None):
        #self.adjacency_list = defaultdict(list)
        super().__init__( name, copy_paths=[] )
        self.parent_list = defaultdict(list)
        self.child_actions = {}
        self.finished, self.started, self.failed = set(), set(), set()
        self.asynch = asynch
        self.work_area_path = work_area_path
        self.cleanup = Cleanup.coerce( cleanup )
        if self.work_area_path:
            self.work_area = WorkArea( self, work_area_path=self.work_area_path,
                                       cleanup=self.cleanup )
        else:
            self.work_area = None
        self.description = f'Directed graph {name}'

    def add_action(self, action, parents=None):
        """
        Add a node to the graph.

        args:
            action (action) :
            parents (list) : Creates an graph edge from the parent actions to this node. The action will wait for the parent actions to finish.
        """
        if not isinstance(action, WorkAction):
            raise TypeError("Node must be an instance of WorkAction")
        if action.name in self.child_actions.keys():
            raise NameError(f'Cannot re-use an action name \'{action.name}\'')
        self.child_actions[action.name] = action
        if parents is not None: 
            for p in parents: self.add_edge( p, action )
        action.attach( self )
        self.copy_paths.extend( action.copy_paths )
        return action

    def add_edge(self, from_node, to_node):
        if from_node.name not in self.child_actions or to_node.name not in self.child_actions:
            raise ValueError("Both nodes must be added to the graph before creating an edge")
        self.parent_list[to_node.name].append(from_node.name)


    def _parent_results(self, nname, val_dict ):
        # Results are kept structured: a WorkArea or sub-graph contributes
        # its outputs as a nested dict under its own name. Downstream actions
        # receive them as-is; a MathEvaluation flattens this namespace itself
        # so its expression can reference nested action outputs by name.
        if self.parent_list[nname] == []:
            return val_dict
        p_list = [ self.child_actions[p] for p in self.parent_list[nname] ]
        in_dict = { k:v for p in p_list for k,v in p.results().items() }
        return in_dict

    def solve(self, val_dict=None):
        """
        A graph will append any computed results to
        val_dict and return that.
        So A.solve( {'v1':1.2} ) may return {'v1':1.2, 'A':3.4},
        where the 'A':3.4 was added with 'A' the name of the action.

        Returns:
            dict: Dictionary containing action_name:action_result pairs
                  appended to val_dict.
        """

        if val_dict is None: val_dict = {}

        if self.work_area:
            if not hasattr(self, '_in_work_area') or not self._in_work_area:
                self._in_work_area = True
                try:
                    self.work_area._progress_reporter = self._progress_reporter
                    return self.work_area.solve(val_dict)
                finally:
                    self._in_work_area = False

        source_names = []
        parent_names = set()
        for nname, node in self.child_actions.items():
            if self.parent_list[nname] == []:
                source_names.append( node.name )
            else:
                for p in self.parent_list[nname]:
                    parent_names.add(p)
            if nname in val_dict: raise ActionNameError( f'Name \'{nname}\' used for both variables and actions.' )
        drain_names = []
        for nname, node in self.child_actions.items():
            if nname not in parent_names:
                drain_names.append(nname)

        self.finished, self.started, self.failed = set(), set(), set()

        # progress for external consumers (e.g. a GUI process): a status.json
        # in the run directory, updated on every action state change. The
        # entries are the actions, not the containers holding them: a child
        # that is itself a graph or a work area is pass-through and registers
        # what is inside it instead (see WorkAction._progress_names).
        reporter = StatusReporter( self.name )
        reporter.start( actions=self._progress_names() )

        report_to = self._report_to( reporter )

        reported_done = set()

        def _sweep_done():
            """Report actions that finished since the last sweep.
            Copies self.finished: async watcher threads mutate it."""
            newly_done = set( self.finished ) - reported_done
            for done_name in newly_done:
                self._report_action( report_to, done_name, 'done' )
                reported_done.add( done_name )
            return bool( newly_done )

        current_name = None
        try:
            # start as parents are ready, and wait till all done
            while len(self.finished) < len(self.child_actions):
                if self.failed:
                    # an asynchronous child failed: report, stop the other
                    # running children, and abort the graph
                    current_name = None
                    self._abort_on_async_failure( report_to, _sweep_done )
                progressed = False
                for nname, node in self.child_actions.items():
                    if nname in self.finished:
                        continue
                    if all(parent in self.finished for parent in self.parent_list[nname]):
                        in_dict = self._parent_results( nname, val_dict )
                        if node.name not in self.started:
                            current_name = nname
                            node._progress_reporter = report_to
                            self._report_action( report_to, nname, 'running' )
                            if self.asynch:
                                node._observed_eval_async(in_dict.copy())
                            else:
                                node._observed_eval(in_dict.copy())
                            self.started.add(node.name)
                            progressed = True
                            # a sync eval finished just now: report it done
                            # before the next action starts
                            _sweep_done()
                if _sweep_done():
                    progressed = True
                if not progressed:
                    # every startable action is already running (asynch mode):
                    # pace the wait instead of spinning a core
                    time.sleep( 0.05 )

            _sweep_done()

            for nname in drain_names:
                node = self.child_actions[nname]
                # Merge each terminal node's results as-is. Sub-graphs and
                # WorkAreas stay nested under their own name, preserving structure
                # and provenance (and avoiding name collisions between branches).
                val_dict.update( node.results() )

            for n,e in self.child_actions.items():
                e._dump( val_dict )
        except BaseException:
            # actions that did complete before the failure still show as done
            _sweep_done()
            if current_name is not None and current_name not in self.finished:
                self._report_action( report_to, current_name, 'failed' )
            reporter.finish( 'failed' )
            raise

        reporter.finish( 'done' )
        return val_dict


    def _report_to( self, reporter ):
        """
        Where this graph's action states go.

        Its own reporter when it owns the run directory's ``status.json``,
        and the enclosing graph's when it does not -- a graph nested in the
        same directory has an inactive reporter of its own, so its actions
        and solver fractions still reach the owner's file. A graph inside a
        ``WorkArea`` has both: its own file in the work area, and the file
        of the graph enclosing the work area, which is the one a job's
        progress bar reads and which the work area itself no longer
        occupies an entry in.
        """
        inherited = self._progress_reporter
        if not reporter.active:
            return inherited if inherited is not None else reporter
        if inherited is None or inherited is reporter:
            return reporter
        return MultiReporter( [ reporter, inherited ] )

    def _report_action( self, report_to, nname, state, message=None ):
        """
        Set one child's state in the status file.

        A pass-through child -- a sub-graph, a work area -- has no entry of
        its own to set: the actions inside it report their own states,
        through this graph's reporter or through both files. Failure is the
        exception. The child may have died in a child process (an asynch
        work area that crashed, or one terminated because a sibling
        failed), and then nothing in there could report itself, so whatever
        it left running is marked failed here.
        """
        node = self.child_actions[nname]
        if not node._progress_passthrough:
            report_to.action_state( nname, state, message=message )
        elif state == 'failed':
            report_to.fail_running( node._progress_names(), message=message )

    def _progress_names( self ):
        """The actions of this graph, each container among them replaced
        by what is inside it."""
        names = []
        for ch in self.child_actions.values():
            names.extend( ch._progress_names() )
        return names

    def _abort_on_async_failure( self, report_to, sweep_done ):
        """
        An asynchronous child process failed (raised, crashed, or returned
        no result). Report the failure, terminate the children still
        running -- their results could not be used anyway -- and raise
        AsyncActionError with the child's error (traceback included).
        """
        sweep_done()
        failed_names = set( self.failed )   # copy: watcher threads mutate it
        for fname in failed_names:
            err = getattr( self.child_actions[fname], '_async_error', None )
            first_line = str( err ).splitlines()[0] if err else None
            self._report_action( report_to, fname, 'failed', message=first_line )

        for sname in self.started - set( self.finished ) - failed_names:
            proc = getattr( self.child_actions[sname], '_async_proc', None )
            if proc is not None and proc.is_alive():
                proc.terminate()
                proc.join( timeout=2.0 )
            self._report_action( report_to, sname, 'failed',
                                 message='terminated: a sibling action failed' )

        first = sorted( failed_names )[0]
        err = getattr( self.child_actions[first], '_async_error', None )
        raise AsyncActionError(
            f"Asynchronous action '{first}' failed: {err or 'unknown error'}" )

    def update(self, message):
        """
        From observer pattern. Called by actions that have finished
        (``[action, 'Done']``) or failed asynchronously
        (``[action, 'Failed']``).

        Arguments:
            message (any) :
        """
        nname = message[0].name
        if len(message) > 1 and message[1] == 'Failed':
            logger.debug( f'Observed action \'{nname}\' failed.' )
            self.failed.add(nname)
            return
        logger.debug( f'Observed action \'{nname}\' finished.' )
        self.finished.add(nname)


    def outputs(self):
        """
        Collects outputs of all child actions.

        Returns:
            dict: {action_name: (data_type, description)}
        """
        result = {}
        for n, e in self.child_actions.items():
            out = e.outputs()
            if isinstance(out, dict):
                result.update(out)
            else:
                result[e.name] = out
        return result

    def _check_names( self, name_list=None ):
        """ Cannot have duplicates -- create a problem with callbacks """
        if name_list is None: name_list = []
        if self.name in name_list: raise ActionNameError( f"Duplicate actions name \'{self.name}\'" )
        name_list.append( self.name )
        for n,e in self.child_actions.items():
            e._check_names( name_list )

    def get_action(self, name ):
        return self.child_actions[name]

    def _tree_children( self ):
        return list( self.child_actions.values() )

    def _cleanup_run_dir( self, base_dir ):
        # a graph given its own work area runs its children in there
        if self.work_area is not None:
            return self.work_area._cleanup_run_dir( base_dir )
        return Path( base_dir )

    def _work_dir_entries( self, cleanup=None ):
        """Child actions of a graph run in the same directory, so their
        produced files are aggregated here. Children that create their own
        directory (a WorkArea, SimulationIterator, or a graph with its own
        work area) contribute that directory as a subtree instead."""
        if self.work_area is not None:
            return [ self.work_area._work_dir_tree( cleanup ) ]
        entries = [ ( 'status.json   (live action states; see kunene.progress)', [] ) ]
        seen = { entries[0][0] }
        for ch in self.child_actions.values():
            for node in ch._work_dir_entries( cleanup ):
                if node[0] in seen:
                    continue
                seen.add( node[0] )
                entries.append( node )
        return entries

    def _work_dir_tree( self, cleanup=None ):
        if self.work_area is not None:
            return self.work_area._work_dir_tree( cleanup )
        return ( './   (current working directory)', self._work_dir_entries( cleanup ) )

    def parameters(self ):
        if self._parameters_cache is not None:
            return self._parameters_cache
        vrs = []
        for ch in self.child_actions.values():
            self._merge_parameters( vrs, ch.parameters() )
        self._parameters_cache = vrs
        return vrs

    def __str__(self ):
        r = f'DirectedGraph: \'{self.name}\' {type(self)}\n' 
        for a in self.child_actions.values():
            r = r + f'\tChild: {a}\n' 
        return r



class WorkFlow(DirectedGraph):
    """
    Calls a chain of evaluations. Results get passed down the chain.
    Used when everything is sequential.

    Arguments:
        name (str) : 
        actions (list) : 
        work_area_path (str) : 
        cleanup (Cleanup) : see ``DirectedGraph``.
    Returns:
        dict: Dictionary containing action_name:action_result pairs
    """

    def __init__( self, name, actions=None, work_area_path=None, cleanup=None ):
        super().__init__( name, work_area_path=work_area_path, cleanup=cleanup )
        self.sequence = []
        if actions is not None:
            for e in actions: self.add_action(e)
        self.description = f'Workflow {name}'

    def add_action( self, action ):
        """
        Adds action to workflow.

        Arguments:
            name (action) : Action to add
        """
        assert action.name != 'objective',  'objective is set using the \'set_call_protocol\' method.'
        action.parent = self

        super().add_action(action)
        if len(self.sequence) > 0 :
            self.add_edge(self.sequence[-1], action)

        self.sequence.append( action )

        return action


