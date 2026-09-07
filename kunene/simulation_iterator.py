"""
Design studies: evaluating a graph once per design point, and finding
those results again afterwards.

``SimulationIterator`` runs the graph in a numbered job directory per
design point. ``JobIndex`` indexes those directories, which is what makes
past runs retrievable: it maps each job to the variable values it was run
with and to the group labels it carries.

The two belong in one module because they describe the same thing from
two sides - the iterator writes the results directory, the index reads it
- and because the index means nothing without the layout the iterator
creates.

Each design evaluation writes its variable values to
``iter_variables.json`` and its action outputs to ``actions_output.pkl``
inside its own ``job_N`` directory. Those files describe one job but say
nothing about the set: answering "which job used K=0.2?" or "which jobs
belong to the baseline study?" meant walking every directory and reading
every file.

``jobs_index.json`` at the results root records one entry per job::

    { "jobs": [ { "job": "job_0",
                  "groups": [ "baseline" ],
                  "variables": { "K": 0.2, "T": 75 },
                  "state": "done",
                  "created_at": 1753,  "updated_at": 1755 } ] }

The index is a *cache*, never the authority: ``JobIndex.rebuild`` derives
it from the job directories themselves, so result trees produced before
this file existed (and trees whose index was deleted) keep working. Only
the group labels are unique to the index - they are chosen by the caller
and cannot be recovered from the job directories - so a rebuild preserves
the labels of jobs it already knows.

Directory names stay ``job_0 ... job_N``. Encoding variable values in the
name would have to invent a float format, would break when a study adds a
variable, and could not express group membership at all.
"""

from pathlib import Path
import os, shutil, sys
import json
import time
import pickle
import numbers
from collections.abc import Iterable, Mapping
from itertools import product

import numpy as np

from kunene.actions import WorkAction, _display_path, _copy_path_nodes
from kunene.args import ( ACTIONS_OUTPUT_PATH, ITER_VARIABLES_PATH,
                           JOBS_INDEX_PATH, JOB_LOG_PATH, STATUS_PATH, Cleanup )
from kunene.cleanup import clean_run_dir
from kunene.errors import ( ActionNameError, AsyncActionError, ParameterError,
                              MissingPathError, DataNotFoundError )
from kunene.progress import ( StatusReporter, StatusWatcher, job_fraction,
                                mark_failed )
from kunene.util import parallel
import kunene.args

import logging
logger = logging.getLogger(__name__)


# Default tolerance when matching float variable values. Values that made a
# round trip through JSON (or that a user typed as 0.30000000000000004) must
# still match, but distinct design points must not be merged.
MATCH_RTOL = 1.0e-9
MATCH_ATOL = 1.0e-12


# how often the per-job bars are refreshed from the jobs' status files
BAR_POLL_INTERVAL = 0.3

# kept alive for the life of a job's child process, so the file behind the
# redirected descriptors is not closed while they are still in use
_CHILD_LOG = None


def _redirect_child_output( job_dir ):
    """
    Send a job child process's stdout and stderr to a file in its own job
    directory.

    A child inherits the terminal, and everything it writes there -- a
    solver wrapper's conversion messages, the log records of a root logger
    configured before the fork, tqdm's own teardown of the bars the child
    inherited -- lands on top of the batch's progress bars, which hold
    their lines by position and cannot recover from an unexpected write.
    Writing to the job directory instead leaves the terminal to the bars
    and gives each job a log of its own, which is the more useful place
    for it anyway.

    The redirect is done on the file descriptors rather than on
    ``sys.stdout``/``sys.stderr``, so the solvers' subprocesses and the
    logging handlers built before the fork follow it too.

    A spawned child (Windows) inherits neither the bars nor the parent's
    logging configuration, so there is less to divert -- but it does
    inherit the console, and the solvers it starts write to it, so the
    redirect matters there just as much.
    """
    global _CHILD_LOG
    _CHILD_LOG = open( Path( job_dir ) / JOB_LOG_PATH, 'w', buffering=1 )

    for stream, fd in ( ( sys.stdout, 1 ), ( sys.stderr, 2 ) ):
        try:
            stream.flush()
        except ( AttributeError, OSError, ValueError ):
            pass
        try:
            os.dup2( _CHILD_LOG.fileno(), fd )
        except OSError:                     # no such descriptor: nothing to move
            pass

    # sys.stdout/sys.stderr are not always the file behind fd 1/2 -- a
    # notebook, an IDE console or a test harness puts an object of its own
    # there, whose writes would still reach the terminal (and whose fileno()
    # may not even exist). Point those at the log as well.
    if not _wraps_fd( sys.stdout, 1 ):
        sys.stdout = _CHILD_LOG
    if not _wraps_fd( sys.stderr, 2 ):
        sys.stderr = _CHILD_LOG

    _silence_inherited_bars()


def _run_job_in_child( iterator, job_dir, val_dict, job_name, errors ):
    """
    Body of the child process of one job of a parallel sweep: run the
    graph in the job directory handed over, clean up after it, and leave
    any traceback in the shared ``errors`` dict for the parent to raise.

    A module-level function rather than a closure inside
    ``solve_parallel`` because under the ``spawn`` start method (Windows)
    the target and its arguments are pickled to reach a fresh interpreter,
    and a local function cannot be pickled. The iterator is passed
    explicitly for the same reason: under ``fork`` the child would have
    inherited it, under ``spawn`` it has to travel (see
    ``SimulationIterator.__getstate__``).
    """
    try:
        _redirect_child_output( job_dir )
        iterator._run_job( job_dir, val_dict )
        clean_run_dir( iterator.graph, job_dir, iterator.cleanup )
    except BaseException as err:
        import traceback
        errors[ job_name ] = ( f'{type(err).__name__}: {err}\n'
                               f'{traceback.format_exc()}' )
        raise SystemExit( 1 )


def _silence_inherited_bars():
    """Stop the parent's progress bars from repainting in a forked child.

    A fork copies the parent's live tqdm bars along with everything else,
    and tqdm keeps them in a class-level registry: anything written through
    ``tqdm.write`` in the child -- a log record, once the parent has tqdm's
    logging redirect open -- would redraw all of them into the child's log.
    The child has no terminal to draw on, so drop them.

    A spawned child (Windows) starts with an empty registry, so there is
    nothing for this to find there.
    """
    try:
        from tqdm import tqdm
    except ImportError:
        return
    for bar in list( getattr( tqdm, '_instances', () ) ):
        bar.disable = True
        tqdm._instances.discard( bar )


def _wraps_fd( stream, fd ):
    """True when writing to this stream writes to that file descriptor."""
    try:
        return stream.fileno() == fd
    except ( AttributeError, OSError, ValueError ):
        return False        # no descriptor of its own (StringIO, capsys, ...)


class _JobBar:
    """
    The terminal-side progress of a parallel batch: a ``tqdm`` bar counting
    the jobs of the batch, and under it one bar per job running right now.

    The per-job bars are fed from the ``status.json`` each job writes in its
    own directory -- the same file a GUI reads -- so a job's bar advances
    with its actions, and with a solver's percent-complete while one runs.
    Job bars come and go as jobs start and finish; the batch bar stays.

    tqdm is optional (``pip install kunene[progress]``) and bars on a
    redirected stream are just noise in a log file, so they are created only
    when they can be seen; every method is a no-op otherwise. This is the
    *terminal* channel and is independent of ``status.json``, which is
    written either way.
    """

    # tqdm puts ', ' in front of {postfix}, so the job's message goes in
    # the description instead, padded to keep the bars lined up
    JOB_BAR_FORMAT = '  {desc} {percentage:3.0f}%|{bar}|'
    # wide enough for the message of a graph running several actions at
    # once ('3 of 5 running: rad_a (80%), rad_b (34%)'); tqdm shrinks the
    # bar itself to whatever the terminal has left
    DESC_WIDTH = 56

    def __init__( self, total, desc, enabled=None ):
        self._bar = None
        self._tqdm = None
        self._jobs = {}         # job name -> [bar, StatusWatcher, position]
        self._redirect = None   # tqdm's logging redirect, while bars are up
        if enabled is False:
            return
        try:
            from tqdm.auto import tqdm
        except ImportError:
            if enabled:
                logger.warning( 'progress_bar=True but tqdm is not installed; '
                                'install it with "pip install kunene[progress]".' )
            return
        if enabled is None and not sys.stderr.isatty():
            return          # nobody is watching a redirected stream
        self._tqdm = tqdm
        self._bar = tqdm( total=total, desc=desc, unit='job', position=0 )

        # A log record written straight to stderr would land on top of the
        # bars, which hold their lines by position; tqdm's redirect routes
        # the handlers through tqdm.write for as long as the bars are up.
        try:
            from tqdm.contrib.logging import logging_redirect_tqdm
        except ImportError:
            return
        self._redirect = logging_redirect_tqdm()
        self._redirect.__enter__()

    def running( self, job_paths ):
        """Give every job running now a bar of its own, and take away the
        bars of the jobs that have finished.

        Arguments:
            job_paths (dict) : job name -> its directory, where the job's
                own ``status.json`` is.
        """
        if self._bar is None:
            return
        for stale in [ n for n in self._jobs if n not in job_paths ]:
            self._jobs.pop( stale )[0].close()
        for job_name, job_dir in job_paths.items():
            if job_name in self._jobs:
                continue
            position = self._free_position()
            bar = self._tqdm( total=100, desc=self._job_desc( job_name ),
                              position=position, leave=False,
                              bar_format=self.JOB_BAR_FORMAT )
            self._jobs[ job_name ] = [ bar,
                                       StatusWatcher( Path( job_dir ) / STATUS_PATH ),
                                       position ]
        self.poll()

    def poll( self ):
        """Refresh the job bars from the status files their jobs write."""
        for job_name, ( bar, watcher, _ ) in self._jobs.items():
            if watcher.poll() is None:
                continue        # file unchanged (or not there yet)
            fraction, message = job_fraction( watcher.last )
            if fraction is None:
                continue
            bar.n = min( bar.total, int( round( fraction * bar.total ) ) )
            bar.set_description_str( self._job_desc( job_name, message ),
                                     refresh=False )
            bar.refresh()

    @classmethod
    def _job_desc( cls, job_name, message=None ):
        """'job_3  solver: time 12.9 of 40', padded to a fixed width so the
        bars stay lined up as the message changes."""
        text = f'{job_name}  {message}' if message else job_name
        return f'{text:<{cls.DESC_WIDTH}.{cls.DESC_WIDTH}}'

    def step( self, n=1 ):
        """One more job finished."""
        if self._bar is not None:
            self._bar.update( n )

    def failed( self, job_name ):
        if self._bar is not None:
            self._bar.set_postfix_str( f'{job_name} failed' )

    def close( self ):
        if self._redirect is not None:
            self._redirect.__exit__( None, None, None )
            self._redirect = None
        for bar, _, _ in self._jobs.values():
            bar.close()
        self._jobs.clear()
        if self._bar is not None:
            self._bar.close()
            self._bar = None

    def _free_position( self ):
        """The first free line under the batch bar, so a job that finishes
        leaves its line to the job that starts next."""
        used = { position for _, _, position in self._jobs.values() }
        position = 1
        while position in used:
            position += 1
        return position


def as_jsonable( value ):
    """Convert numpy scalars/arrays to plain Python so a value can be
    written to JSON. Variable values commonly come from ``np.arange``,
    whose items are ``np.float64`` and are not JSON serializable."""
    if isinstance( value, np.generic ):
        return value.item()
    if isinstance( value, np.ndarray ):
        return [ as_jsonable( v ) for v in value.tolist() ]
    if isinstance( value, dict ):
        return { k: as_jsonable( v ) for k, v in value.items() }
    if isinstance( value, ( list, tuple ) ):
        return [ as_jsonable( v ) for v in value ]
    return value


def values_match( a, b, rtol=MATCH_RTOL, atol=MATCH_ATOL ):
    """True when two variable values denote the same design point.

    Numbers compare with a tolerance (a value written to JSON and read
    back is not always bit-identical); everything else compares equal.
    Booleans are excluded from the numeric path so True does not match 1.
    """
    a, b = as_jsonable( a ), as_jsonable( b )
    if isinstance( a, bool ) or isinstance( b, bool ):
        return a is b or a == b
    if isinstance( a, numbers.Number ) and isinstance( b, numbers.Number ):
        return bool( np.isclose( a, b, rtol=rtol, atol=atol ) )
    return a == b


def normalise_groups( groups ):
    """Accept ``None``, a single label or an iterable of labels and return
    a list of unique labels in the order given."""
    if groups is None:
        return []
    if isinstance( groups, str ):
        groups = [ groups ]
    out = []
    for g in groups:
        g = str( g )
        if g not in out:
            out.append( g )
    return out


class JobIndex:
    """
    Reads and writes the ``jobs_index.json`` of one results root.

    Arguments:
        root (str|Path) : the results root - the directory holding the
            ``job_N`` directories (a ``SimulationIterator``'s
            ``work_area_path``).
        job_prefix (str) : directory name prefix, ``SimulationIterator.JNAME``.
    """

    def __init__( self, root, job_prefix='job_' ):
        self.root = Path( root )
        self.job_prefix = job_prefix
        self.records = []
        self._loaded = False

    # ------------------------------------------------------------------
    # persistence

    @property
    def path( self ):
        return self.root / JOBS_INDEX_PATH

    def load( self, rebuild_if_missing=True ):
        """Read the index from disk. When no index file exists but job
        directories do, derive one from them."""
        if self.path.exists():
            try:
                with open( self.path ) as f:
                    data = json.load( f )
                self.records = data.get( 'jobs', [] )
                self._loaded = True
                # jobs written by a run that never reached the index (or by
                # an older version) are still adopted
                if rebuild_if_missing: self._adopt_unindexed()
                return self
            except ( OSError, ValueError ) as exc:
                logger.warning( f'Could not read {self.path}: {exc}. Rebuilding.' )
        self.records = []
        self._loaded = True
        if rebuild_if_missing:
            self.rebuild()
        return self

    def save( self ):
        """Write the index atomically, so a reader polling the file never
        sees it half-written."""
        if not self.root.exists():
            return
        data = { 'jobs': self.records }
        tmp = self.path.with_name( self.path.name + '.tmp' )
        try:
            with open( tmp, 'w' ) as f:
                json.dump( data, f, indent=1 )
            os.replace( tmp, self.path )
        except OSError as exc:
            # Indexing must never break a workflow that otherwise ran fine.
            logger.warning( f'Could not write {self.path}: {exc}' )

    def rebuild( self, save=True ):
        """Derive the index from the job directories on disk.

        Group labels of jobs already in the index are preserved - they
        exist nowhere else.
        """
        known = { r.get( 'job' ): r for r in self.records }
        records = []
        for job_dir in self.iter_job_dirs():
            rec = self._record_from_dir( job_dir )
            if rec is None:
                continue
            old = known.get( job_dir.name )
            if old is not None:
                rec['groups'] = normalise_groups( old.get( 'groups' ) )
                rec['created_at'] = old.get( 'created_at', rec['created_at'] )
            records.append( rec )
        self.records = records
        self._loaded = True
        if save:
            self.save()
        return self

    def _adopt_unindexed( self ):
        """Add job directories that are on disk but not in the index."""
        known = { r.get( 'job' ) for r in self.records }
        added = False
        for job_dir in self.iter_job_dirs():
            if job_dir.name in known:
                continue
            rec = self._record_from_dir( job_dir )
            if rec is not None:
                self.records.append( rec )
                added = True
        if added:
            self.records.sort( key=lambda r: self._job_number( r.get( 'job', '' ) ) )
            self.save()

    def _record_from_dir( self, job_dir ):
        """Build an index record by reading one job directory."""
        var_path = job_dir / ITER_VARIABLES_PATH
        variables = {}
        if var_path.exists():
            try:
                with open( var_path ) as f:
                    variables = json.load( f )
            except ( OSError, ValueError ) as exc:
                logger.warning( f'Could not read {var_path}: {exc}' )
                return None
        elif not ( job_dir / ACTIONS_OUTPUT_PATH ).exists():
            # neither variables nor outputs: not a job directory
            return None
        # A job whose outputs were written ran to completion; one without
        # them was interrupted (or is running right now).
        done = ( job_dir / ACTIONS_OUTPUT_PATH ).exists()
        try:
            stamp = job_dir.stat().st_mtime
        except OSError:
            stamp = time.time()
        return { 'job': job_dir.name,
                 'groups': [],
                 'variables': variables,
                 'state': 'done' if done else 'unknown',
                 'created_at': stamp,
                 'updated_at': stamp }

    def _ensure( self ):
        if not self._loaded:
            self.load()
        return self

    # ------------------------------------------------------------------
    # job directories

    def _job_number( self, name ):
        try:
            return int( name[ len( self.job_prefix ): ] )
        except ( ValueError, TypeError ):
            return -1

    def iter_job_dirs( self ):
        """Job directories on disk, in numeric order."""
        if not self.root.exists():
            return []
        dirs = [ d for d in self.root.iterdir()
                 if d.is_dir() and d.name.startswith( self.job_prefix )
                 and self._job_number( d.name ) >= 0 ]
        return sorted( dirs, key=lambda d: self._job_number( d.name ) )

    def next_job_number( self ):
        """The first unused job number, considering both the index and the
        directories on disk (either may be ahead of the other)."""
        used = [ self._job_number( d.name ) for d in self.iter_job_dirs() ]
        self._ensure()
        used += [ self._job_number( r.get( 'job', '' ) ) for r in self.records ]
        used = [ n for n in used if n >= 0 ]
        return max( used ) + 1 if used else 0

    def job_path( self, job ):
        """Absolute path of a job directory, given its name or record."""
        if isinstance( job, dict ):
            job = job.get( 'job' )
        return self.root / job

    # ------------------------------------------------------------------
    # updating

    def record_job( self, job, variables, groups=None, state='running' ):
        """Insert or update the entry for one job."""
        self._ensure()
        groups = normalise_groups( groups )
        variables = as_jsonable( variables )
        now = time.time()
        for rec in self.records:
            if rec.get( 'job' ) == job:
                rec['variables'] = variables
                rec['state'] = state
                rec['updated_at'] = now
                for g in groups:
                    if g not in rec.setdefault( 'groups', [] ):
                        rec['groups'].append( g )
                self.save()
                return rec
        rec = { 'job': job,
                'groups': groups,
                'variables': variables,
                'state': state,
                'created_at': now,
                'updated_at': now }
        self.records.append( rec )
        self.save()
        return rec

    def set_state( self, job, state ):
        """Set the state ('running', 'done', 'failed') of one job."""
        self._ensure()
        for rec in self.records:
            if rec.get( 'job' ) == job:
                rec['state'] = state
                rec['updated_at'] = time.time()
                self.save()
                return rec
        return None

    def add_groups( self, jobs, groups ):
        """Add group labels to the given jobs (names or records).

        Returns the list of job names that were changed.
        """
        self._ensure()
        groups = normalise_groups( groups )
        names = { j.get( 'job' ) if isinstance( j, dict ) else str( j ) for j in jobs }
        changed = []
        for rec in self.records:
            if rec.get( 'job' ) not in names:
                continue
            have = rec.setdefault( 'groups', [] )
            for g in groups:
                if g not in have:
                    have.append( g )
                    if rec['job'] not in changed: changed.append( rec['job'] )
            rec['updated_at'] = time.time()
        if changed:
            self.save()
        return changed

    def remove_groups( self, jobs, groups ):
        """Remove group labels from the given jobs. Returns the job names
        that were changed."""
        self._ensure()
        groups = normalise_groups( groups )
        names = { j.get( 'job' ) if isinstance( j, dict ) else str( j ) for j in jobs }
        changed = []
        for rec in self.records:
            if rec.get( 'job' ) not in names:
                continue
            have = rec.setdefault( 'groups', [] )
            for g in groups:
                if g in have:
                    have.remove( g )
                    if rec['job'] not in changed: changed.append( rec['job'] )
            rec['updated_at'] = time.time()
        if changed:
            self.save()
        return changed

    # ------------------------------------------------------------------
    # querying

    def group_names( self ):
        """All group labels in use, sorted."""
        self._ensure()
        names = set()
        for rec in self.records:
            names.update( rec.get( 'groups', [] ) )
        return sorted( names )

    def find( self, where=None, groups=None, state='done',
              match_all_groups=False, rtol=MATCH_RTOL, atol=MATCH_ATOL ):
        """Records matching a partial set of variable values and/or groups.

        Arguments:
            where (dict) : variable values that must match. Only the given
                variables are compared, so ``{'K': 0.2}`` matches every job
                run with that K whatever else varied.
            groups (str|list) : keep jobs carrying any of these labels (all
                of them when ``match_all_groups``).
            state (str) : required job state, ``None`` to accept any.
                Defaults to 'done' - a job that failed or is still running
                has no results to retrieve.
        Returns:
            list of records, in job order.
        """
        self._ensure()
        groups = normalise_groups( groups )
        out = []
        for rec in self.records:
            if state is not None and rec.get( 'state' ) != state:
                continue
            if groups:
                have = rec.get( 'groups', [] )
                hits = [ g for g in groups if g in have ]
                if match_all_groups:
                    if len( hits ) != len( groups ): continue
                elif not hits:
                    continue
            if where:
                rvars = rec.get( 'variables', {} )
                if any( k not in rvars or not values_match( rvars[k], v, rtol, atol )
                        for k, v in where.items() ):
                    continue
            out.append( rec )
        return out

    def find_exact( self, variables, state='done', rtol=MATCH_RTOL, atol=MATCH_ATOL ):
        """The record of the job run with exactly this set of variable
        values (same names, matching values), or None.

        The same design point can legitimately be run more than once (a
        changed deck, a new solver version), so the *most recent* matching
        job wins - it describes the current state of that design point.

        Used for reuse: a job that differs in a variable not mentioned in
        ``variables`` is a different design point and must not be reused.
        """
        self._ensure()
        variables = as_jsonable( variables )
        found = None
        for rec in self.records:
            if state is not None and rec.get( 'state' ) != state:
                continue
            rvars = rec.get( 'variables', {} )
            if set( rvars.keys() ) != set( variables.keys() ):
                continue
            if all( values_match( rvars[k], v, rtol, atol ) for k, v in variables.items() ):
                found = rec
        return found

    def read_outputs( self, job ):
        """Unpickle the action outputs of one job (name or record)."""
        path = self.job_path( job ) / ACTIONS_OUTPUT_PATH
        if not path.exists():
            raise DataNotFoundError( f'No results in {path}: the job did not complete.' )
        with open( path, 'rb' ) as f:
            return pickle.load( f )


# -------------------

class SimulationIterator(WorkAction):
    """
    Used to evaluate different designs in different directories.
    It calls the graph in different subdirectories -- a subdirectory per design.
    Use WorkArea to overwrite the results in a directory.

    This is designed as a top-level action.

    Every job is recorded in a ``jobs_index.json`` at the results root
    (see ``JobIndex`` in this module), which maps the job directories to the
    variable values they were run with and to the group labels they carry.
    That index backs the retrieval methods (``find_jobs``, ``results_for``,
    ``collect``) and ``reuse_existing``, and can be rebuilt from the job
    directories at any time.

    An existing results directory is added to: jobs are numbered after the
    ones already there, so a study can be extended in a later session and
    a finished job is never written over. Pass ``clean_start=True`` to
    delete the results directory first.

    args:
        graph (DirectedGraph) : DirectedGraph or WorkFlow
        parameter_list (list) : Only needed to provided default values to eval. Maybe not needed.
        work_area_path (str) : Default is to ./{graph.name}
        copy_paths (list) :
        clean_start (bool) : delete the results directory (jobs, index and
            all) before starting.
        cleanup (Cleanup) : remove bulk solver output from each job
            directory once that job's graph has run, so a long study does
            not fill the disk with field output. See
            :class:`kunene.args.Cleanup`; ``True`` selects the default
            policy, ``None`` (the default) keeps every file. A job that
            failed is never cleaned -- its deck and solver log are what you
            debug it with -- and neither are ``actions_output.pkl``,
            ``iter_variables.json`` or the index, so ``results_for``,
            ``collect`` and ``reuse_existing`` keep working on a cleaned
            study. A ``WorkArea`` nested in the graph inherits this policy
            unless it sets its own.
        groups (str|list) : default group label(s) for the jobs this
            iterator runs. Overridden per call by the ``groups`` argument
            of ``solve``/``collect_for_expdes``/``collect_for_varrange``,
            and settable at any time as ``iterator.groups``.
        reuse_existing (bool) : when True, a design point that already has
            a completed job in the results directory is not run again: its
            stored outputs are returned instead. Default False, which runs
            every design point given, whether or not an equivalent job is
            already there.
        max_workers (int) : how many jobs of a sweep may run at the same
            time, each in a child process of its own (forked where the
            platform has fork, spawned on Windows -- see
            ``kunene.util.parallel``). The default 1 runs
            them one after the other. Only the sweep methods
            (``collect_for_expdes``, ``collect_for_varrange``) fan out;
            ``solve`` is one design point and always runs here. This
            process stays the only one that allocates job directories and
            writes the index, so the numbering cannot race, and a child
            leaves its results in its own job directory, where they are
            read back once it exits -- so the results are structured
            exactly as in a serial run. A job that fails aborts the sweep,
            as it does when running serially: the jobs still running are
            terminated and ``AsyncActionError`` is raised with the child's
            traceback. Set it no higher than the machine can run solvers:
            each job is a full graph, and a solver action may itself use
            several cores (and an ``asynch`` graph several processes).
            On a platform without fork (Windows) the child is a fresh
            interpreter, so the graph and its actions must be picklable
            and action classes must live in an importable module rather
            than in the calling script (``SpawnError`` otherwise); the
            child does not re-import the script.

    Returns:
        dict: Output from graph (it adds nothing).
    """

    JNAME = 'job_'

    # an enclosing work area's cleanup does not reach into the job
    # directories: they are numbered and cleaned here, as each job finishes
    _cleans_own_dirs = True

    def __init__( self, graph, parameter_list=None,
                 work_area_path=None, copy_paths=None, clean_start=False,
                 groups=None, reuse_existing=False, cleanup=None,
                 max_workers=1):

        assert isinstance( graph, WorkAction )
        #assert isinstance( graph, DirectedGraph ) # ? must be a graph

        super().__init__( graph.name+'_Iter', "", copy_paths=( copy_paths or [] )+graph.copy_paths )

        self.graph = graph
        self.parameter_list = parameter_list

        self.last_job_path = None

        self._check_names( [] )

        self.run_iter = 0               # jobs processed; zeroed by a batch
        self.jobs_total = None          # size of the batch running, if any
        self._status_reporter = None    # created on first solve()
        self._current_job = None        # last job directory started
        # True while a sweep (collect_for_* / solve_parallel) is running, so
        # the solve() calls a serial sweep makes leave its batch counts
        # alone while a solve() of the caller's own clears them
        self._in_batch = False

        self.max_workers = int( max_workers )
        if self.max_workers < 1:
            raise ParameterError(
                f'max_workers must be at least 1, got {max_workers}.' )

        if work_area_path is None:
            work_area_path = Path.cwd().joinpath( self.graph.name )
        
        # Expand ~ and environment variables
        expanded_path = os.path.expandvars(os.path.expanduser(str(work_area_path)))
        self.work_area_path = Path(expanded_path)

        self.groups = groups            # default labels; str or list
        self.reuse_existing = reuse_existing
        self.cleanup = Cleanup.coerce( cleanup )
        self._index = JobIndex( self.work_area_path, job_prefix=self.JNAME )
        self.reused_jobs = []           # jobs loaded instead of re-run

        if clean_start: self.rm_rundir()
        self.description = f'Simulation iterator for graph {graph.name}'

    def rm_rundir( self ):
        # the results root is about to go: end the run status first, so no
        # heartbeat writes into the deleted tree and the next run of this
        # study can own the directory again
        self._finish_status( 'done' )

        sim_path = self.work_area_path
        if sim_path.exists():  shutil.rmtree( sim_path )
        self._index = JobIndex( self.work_area_path, job_prefix=self.JNAME )

    def __getstate__( self ):
        """
        Pickle the iterator for a job's child process, without the state
        that belongs to the parent.

        Under the ``spawn`` start method (Windows) a job's child is a fresh
        interpreter and gets the iterator as a pickle rather than as a copy
        of this process's memory. The run-level ``StatusReporter`` is
        dropped: it owns the ``status.json`` at the *results root*, which
        only the parent writes -- a child reports through the reporter its
        own graph creates in the job directory. Under ``fork`` the child
        inherits a copy it likewise never touches, so both start methods
        behave the same.
        """
        state = super().__getstate__()
        state['_status_reporter'] = None
        return state

    # ------------------------------------------------------------------
    # the job index and JNAME have to agree
    #
    # The index recognises and numbers job directories by their prefix, so
    # it must use the same one as JNAME. JNAME can be changed after the
    # iterator was built (``itr.JNAME = 'design_'``); the index created in
    # __init__ would then still look for the old prefix, recognise none of
    # the directories it writes, hand out job number 0 every time, and each
    # run would overwrite the one before. Reading the index through a
    # property keeps the two in step however and whenever JNAME is set --
    # on the instance, on the class, or in a subclass.
    #
    # Change the prefix on an empty results directory. Jobs already written
    # under the old prefix stay in the index and can still be read back by
    # name, but they no longer take part in the numbering, so the new
    # prefix starts its own series at 0.

    @property
    def _index( self ):
        index = self.__index
        if index.job_prefix != self.JNAME:
            logger.debug( f'Job directory prefix changed to \'{self.JNAME}\'.' )
            index.job_prefix = self.JNAME
        return index

    @_index.setter
    def _index( self, index ):
        self.__index = index

    def _check_names( self, name_list=None ):
        """ Cannot have duplicates -- create a problem with callbacks """
        if name_list is None: name_list = []
        if self.name in name_list: raise ActionNameError( f"Duplicate actions name \'{self.name}\'" )
        name_list.append( self.name )
        self.graph._check_names( name_list )

    def _tree_children( self ):
        return [ self.graph ]

    def _cleanup_run_dir( self, base_dir ):
        # the results root; the jobs underneath it are cleaned one by one
        # as they finish (see solve), not as part of an enclosing plan
        path = Path( self.work_area_path )
        return path if path.is_absolute() else Path( base_dir ) / path

    def _work_dir_tree( self, cleanup=None ):
        cleanup = self.cleanup if self.cleanup is not None else cleanup
        job_children = [
            ( 'iter_variables.json   (this design\'s variable values)', [] ),
            ( 'actions_output.pkl   (this design\'s action outputs)', [] ),
        ]
        if self.max_workers > 1:
            # a job that runs in a child process writes its output here
            # instead of to the terminal, which the progress bars own
            job_children.append(
                ( f'{JOB_LOG_PATH}   (this job\'s stdout and stderr)', [] ) )
        job_children += _copy_path_nodes( self.copy_paths )
        job_children += self.graph._work_dir_entries( cleanup )
        children = [
            ( 'status.json   (run progress: current job, jobs done; see kunene.progress)', [] ),
            ( 'jobs_index.json   (job -> variable values and group labels; see kunene.simulation_iterator)', [] ),
            ( f'{self.JNAME}0/   (one directory per design evaluation)', job_children ),
            ( f'{self.JNAME}1/ … {self.JNAME}N/', [] ),
        ]
        return ( f'{_display_path(self.work_area_path)}/   (results root)', children )

    def _work_dir_entries( self, cleanup=None ):
        # A SimulationIterator creates its own results directory: contribute
        # it as a subtree rather than flattening files into the parent.
        return [ self._work_dir_tree( cleanup ) ]

    def _in_last_run_dir( func ):
        """ decorator execute last run """
        def wrapper( self, *args, **kwargs ):
            root_dir = Path.cwd()
            os.chdir( self.last_job_path )
            try:
                vals = func( self, *args, **kwargs )
            finally:
                os.chdir( root_dir )
            return vals
        return wrapper

    def write_outputs( self, evals ):
        """
        Called in run subdirectory.
        """
        with open(kunene.args.ACTIONS_OUTPUT_PATH, 'wb') as f:
            pickle.dump(evals, f)

    def read_outputs( self ):
        """
        Called in run subdirectory.
        """
        with open(kunene.args.ACTIONS_OUTPUT_PATH, 'rb') as f:
            ret = pickle.load(f)
        return ret

    def gather_outputs( self ):
        """
        The action outputs of every completed job in the results directory,
        in job order.

        Taken from the job index, so a gap in the numbering (a job
        directory deleted or archived) no longer cuts the list short, and
        jobs that failed or are still running are skipped instead of
        raising.

        Returns:
            list: one ``{action_name: value}`` dict per completed job.
        """
        idx = self.job_index()
        ret = []
        for rec in idx.find( state='done' ):
            if not ( idx.job_path( rec ) / kunene.args.ACTIONS_OUTPUT_PATH ).exists():
                # stale index entry: the directory was removed by hand
                logger.warning( f'Skipping {rec["job"]}: no {kunene.args.ACTIONS_OUTPUT_PATH}.' )
                continue
            ret.append( idx.read_outputs( rec ) )
        return ret

    @staticmethod
    def outcomes_as_lists( list_of_evals ):
        """
        Transform list of evals (a list of dictionaries) to a dictionary of lists.
        [{'a':1},{'a':1}] -> {'a':[1,2]}

        Args:
            list_of_evals: a list of dictionaries
        Returns:
            dict_lists:  dictionaries of lists
        """
        outcome = None
        for evals in list_of_evals:
            if outcome is None: outcome = {k:[] for k in evals.keys() }
            for k,val in evals.items():
                if k not in outcome.keys(): raise ParameterError( f'Variable \'{k}\' not set in all runs' )
            for k,val in evals.items(): outcome[k].append( val )
        SimulationIterator._subdicts_as_lists( outcome ) # transforms the list of dictionaries into a dictionary of lists.
        return outcome


    def iterdir( self ):
        """
        The run directories of jobs that were previously run, in job order.

        Every job directory present in the results directory is returned,
        including after a gap in the numbering; the former scan stopped at
        the first missing number. Use ``find_jobs`` to select by variable
        value, group or state.

        Returns:
            list: Path instances.
        """
        return self.job_index().iter_job_dirs()


    def _resolve_groups( self, groups=None ):
        """Group labels for a job: the ones given for this call, else the
        iterator's own (constructor argument or ``iterator.groups``)."""
        if groups is None: groups = self.groups
        return normalise_groups( groups )

    def _allocate_job_path( self ):
        """Path of the next job directory.

        The number continues after whatever is already in the results
        directory (index and directories on disk both consulted), so a
        study can be added to across sessions and an existing job is never
        written over. Use ``clean_start=True`` to start from an empty
        results directory instead.
        """
        return self.work_area_path.joinpath( self.JNAME + str( self._index.next_job_number() ) )

    def _start_batch( self, jobs_total ):
        """Begin a batch of design points: the job counts a reader sees
        are the batch's, so they start from zero here rather than counting
        on from whatever this iterator ran before."""
        self.run_iter = 0
        self.jobs_total = jobs_total
        self._in_batch = True

    def _finish_status( self, state='done' ):
        """End the run-level status: final state, heartbeat stopped, and
        the results root released so the next run -- of this iterator or
        another one on the same directory -- owns its own file. A later
        call recreates the reporter, so an iterator stays usable.

        The batch ends with it, whichever way it ended, so a design point
        solved afterwards is not counted against a batch it had no part in.
        Every way out of a sweep passes through here -- the end of it, and
        the failure paths of ``solve`` and ``solve_parallel``.
        """
        self._in_batch = False
        if self._status_reporter is not None:
            self._status_reporter.finish( state )
            self._status_reporter = None

    def _report_job( self, job_name ):
        """One job has become the current one (serial run)."""
        self._current_job = job_name
        self._report_status( [ job_name ], state='running' )

    def _report_status( self, running_jobs, state='running' ):
        """Run-level progress for external consumers (e.g. a GUI process):
        a status.json at the results root with the job counts, the jobs
        running right now (``current_jobs``, more than one when
        ``max_workers`` > 1) and the last job started (``current_job``,
        kept for readers that follow a single job).

        The counts are the current batch's (see ``_start_batch``), and
        ``running_jobs`` is whatever is running at this moment -- every
        caller has to pass what it knows, including the ones that did not
        start a job themselves, or the file would claim a batch is idle
        while its other jobs are still going.
        """
        fields = { 'jobs_total': self.jobs_total,
                   'jobs_done': self.run_iter,
                   'current_job': self._current_job,
                   'current_jobs': list( running_jobs ) }

        # An iterator nested inside a graph is not pass-through -- its jobs
        # have directories and status files of their own -- so it holds one
        # entry in the enclosing graph's file rather than showing the
        # actions of a job there. Say which job is running in it, with the
        # fraction of the batch where the total is known (a sweep; an
        # iterator inside a graph is asked for one design point at a time).
        fraction = self.run_iter / self.jobs_total if self.jobs_total else None
        message = self._current_job
        if message and self.jobs_total:
            message = f'{message} ({self.run_iter} of {self.jobs_total})'
        if message:
            self.report_progress( fraction=fraction, message=message )

        if self._status_reporter is None:
            # takeover: a new run of a study owns its results root, even
            # when an earlier iterator object in this process still holds a
            # reporter for it -- otherwise the new run would report nothing
            # and the old reporter would go on heartbeating a stale file
            self._status_reporter = StatusReporter( self.name,
                                                    directory=self.work_area_path,
                                                    takeover=True )
            self._status_reporter.start( actions=None, state=state, **fields )
        else:
            self._status_reporter.update( state=state, **fields )

    def _reuse( self, val_dict, job_groups ):
        """Return the stored outputs of a completed job run with exactly
        these variable values, or None when there is no such job.

        The job is re-labelled with this call's groups, so a design point
        computed for one study can be pulled into another without running
        the graph again.

        Run-level progress is left to the caller: a reuse hit inside a
        parallel batch must not report the batch as idle while its other
        jobs are still running.
        """
        rec = self._index.find_exact( val_dict )
        if rec is None:
            return None
        job_name = rec['job']
        try:
            ret = self._index.read_outputs( job_name )
        except DataNotFoundError:
            # index says done but the outputs are gone: run it again
            logger.warning( f'Job {job_name} has no {kunene.args.ACTIONS_OUTPUT_PATH}; re-running.' )
            self._index.set_state( job_name, 'unknown' )
            return None

        if job_groups:
            self._index.add_groups( [ job_name ], job_groups )
        self.last_job_path = self._index.job_path( job_name )
        self.reused_jobs.append( job_name )
        logger.debug( f'Reusing {job_name} for {val_dict}' )

        self._current_job = job_name
        self.run_iter += 1
        return ret

    def _with_defaults( self, val_dict ):
        """The design point completed with the default value of every
        parameter the caller did not give a value for."""
        if val_dict is None: val_dict = {}
        if not isinstance( val_dict, Mapping ):
            raise ParameterError(
                f'A design point is a {{variable name: value}} dict, '
                f'not {type(val_dict).__name__}.' )

        pl = self.parameter_list if self.parameter_list is not None else self.parameters()
        for def_par in pl:
            if def_par.name not in val_dict:
                if def_par.value is None:
                   raise ParameterError( f'Parameter \'{def_par.name}\' must have a value defined in SimulationIterator.solve().' )
                val_dict[def_par.name]=def_par.value
        return val_dict

    def _start_job( self, val_dict, job_groups ):
        """Create the next job directory, record it in the index and copy
        the input files into it. Returns ``(job_name, job_dir)``.

        Always called in this process, also when the jobs themselves run in
        child processes: the job number comes from the index and the
        directories on disk, so allocating it anywhere else would let two
        jobs claim the same number and run in the same directory.
        """
        self.last_job_path = self._allocate_job_path()
        self.last_job_path.mkdir(mode=0o777, parents=True, exist_ok=True)

        # record the job before it runs, so an interrupted run still leaves
        # the design point and its group labels in the index
        job_name = self.last_job_path.name
        self._index.record_job( job_name, val_dict, groups=job_groups, state='running' )

        if self.copy_paths is not None:
            logger.debug( f'Copying paths {self.copy_paths}' )
            for fname in self.copy_paths:
                src = Path(fname)
                if not src.exists():
                    raise MissingPathError( f'Path \"{fname}\" not found. Either the path does not exist or you must specify the full path.' )
                if src.is_dir():
                    shutil.copytree(src, self.last_job_path / src.name)
                else:
                    shutil.copy2(src, self.last_job_path)

        return job_name, Path.cwd() / self.last_job_path

    def _run_job( self, job_dir, val_dict ):
        """Run the graph for one design point in its job directory and
        leave the results there.

        Changing directory is process-wide, which is why running two jobs
        at once takes a process each (see ``max_workers``) rather than a
        thread each.
        """
        root_dir = Path.cwd()
        os.chdir( job_dir )
        logger.debug( f'Running in directory {job_dir}' )
        try:
            with open( kunene.args.ITER_VARIABLES_PATH,'w' ) as vf:
                json.dump( as_jsonable( val_dict ), vf )

            ret = self.graph.solve( val_dict )

            self.write_outputs( ret )
        finally:
            os.chdir( root_dir )
        return ret

    def solve(self,  val_dict=None, groups=None ):
        """
        Evaluate one design point, here in this process. A sweep runs
        several at a time when ``max_workers`` > 1; this does not.

        Args:
            val_dict (dict) : variable values for this design point.
            groups (str|list) : group label(s) for this job, overriding
                the iterator's default.

        Returns:
            dict: Output from graph (it adds nothing).
        """

        val_dict = self._with_defaults( val_dict )

        if not self._in_batch:
            # one design point of the caller's own, not part of a sweep:
            # there is no batch for a reader to count it against
            self.jobs_total = None

        job_groups = self._resolve_groups( groups )

        if self.reuse_existing:
            reused = self._reuse( val_dict, job_groups )
            if reused is not None:
                self._report_status( [], state='idle' )
                return reused

        job_name, job_dir = self._start_job( val_dict, job_groups )
        self._report_job( job_name )

        try:
            ret = self._run_job( job_dir, val_dict )
        except BaseException:
            self._index.set_state( job_name, 'failed' )
            self._report_status( [], state='failed' )
            self._finish_status( 'failed' )
            raise
        self._index.set_state( job_name, 'done' )

        # the graph has run to the end and its outputs are on disk, so the
        # bulk solver files have been read by whatever needed them
        clean_run_dir( self.graph, job_dir, self.cleanup )
        self.run_iter += 1
        self._report_status( [], state='idle' )

        return ret

    @staticmethod
    def _as_design_points( design_points ):
        """The designs to ``solve_parallel`` should be a list not a dict.
        """
        if isinstance( design_points, Mapping ):
            raise ParameterError(
                'solve_parallel() evaluates a batch of design points, not the '
                'single {variable name: value} dict solve() takes. Use solve() '
                'for one design point, or pass [val_dict] to run it as a batch '
                'of one.' )

        if isinstance( design_points, (str, bytes) ) or \
           not isinstance( design_points, Iterable ):
            raise ParameterError(
                'solve_parallel() takes a sequence of {variable name: value} '
                f'dicts, not {type(design_points).__name__}.' )

        design_points = list( design_points )   # an iterator is fine too
        for iexp, pars_vals in enumerate( design_points ):
            if not isinstance( pars_vals, Mapping ):
                raise ParameterError(
                    f'Design point {iexp} is a {type(pars_vals).__name__}, not '
                    'a {variable name: value} dict.' )
        return design_points

    def solve_parallel( self, design_points, groups=None, progress_bar=None ):
        """
        Evaluate a batch of design points ``max_workers`` at a time, one
        child process per job -- the plural of ``solve``, which evaluates
        one design point here in this process.

        Used by ``collect_for_expdes``, and directly when the design points
        come from something that hands them out in batches (a generation of
        an optimizer, say) and the outputs are wanted as they are, without
        the design matrix ``collect_for_expdes`` builds.

        A child runs the graph in the directory handed to it and leaves its
        results there (``actions_output.pkl``); this process reads them back
        once the child has exited, so nothing has to survive a pipe and the
        results are structured exactly as in a serial run. Job directories
        and the index stay the business of this process alone.

        A job that fails aborts the batch, as it does when the sweep runs
        serially: the jobs still running are terminated -- their results
        could not be used anyway -- marked failed in the index, and
        ``AsyncActionError`` is raised carrying the child's traceback. The
        jobs that finished keep their results, so the batch can be resumed
        with ``reuse_existing=True``.

        The children are forked where the platform has fork and spawned
        otherwise (Windows); ``kunene.util.parallel`` chooses. Spawning
        costs the graph having to be picklable -- with its action classes
        in an importable module, since the child does not re-import the
        calling script -- but the sweep is otherwise the same either way.

        Args:
            design_points (list) : one ``{variable name: value}`` dict per
                design point, as ``solve`` takes for a single one. Missing
                parameters are filled in with their default values.
            groups (str|list) : group label(s) for these jobs, overriding
                the iterator's default.
            progress_bar (bool) : report progress as ``tqdm`` bars on
                stderr: one counting the jobs of the batch, and under it a
                bar per job running right now, fed from that job's
                ``status.json`` so it follows the job's actions and a
                solver's percent-complete. The default None shows them when
                tqdm is installed and stderr is a terminal; True insists
                (and warns when tqdm is missing), False never shows them.
                The bars are a convenience for a terminal:
                ``status.json`` is written whichever way this is set.

        Returns:
            list: one ``{action name: value}`` dict per design point, in the
            order the design points were given.

        Raises:
            ParameterError: if the batch is not a sequence of design points
                -- notably when a single ``{variable name: value}`` dict is
                handed over, as ``solve`` takes.
        """
        design_points = self._as_design_points( design_points )

        ctx = parallel.get_context()

        manager = parallel.start_manager( ctx )
        errors = manager.dict()

        results = [ None ] * len( design_points )
        queued = list( enumerate( design_points ) )
        running = {}             # job name -> (process, index in results)
        failure = None           # (job name, error text) of the first failure
        bar = _JobBar( len( design_points ), desc=self.name, enabled=progress_bar )
        last_bar_poll = 0.0

        self._start_batch( len( design_points ) )

        try:
            while queued or running:
                while queued and len( running ) < self.max_workers:
                    iexp, pars_vals = queued.pop( 0 )
                    val_dict = self._with_defaults( pars_vals )
                    job_groups = self._resolve_groups( groups )

                    if self.reuse_existing:
                        reused = self._reuse( val_dict, job_groups )
                        if reused is not None:
                            results[ iexp ] = reused
                            bar.step()
                            # the count moved on; the jobs running now did not
                            self._report_status(
                                running,
                                state='running' if running or queued else 'idle' )
                            continue

                    job_name, job_dir = self._start_job( val_dict, job_groups )
                    proc = parallel.start_process(
                        ctx, _run_job_in_child,
                        args=( self, job_dir, val_dict, job_name, errors ) )
                    running[ job_name ] = ( proc, iexp )
                    self._current_job = job_name
                    logger.debug( f'Started {job_name} ({len(running)} running)' )
                    self._report_status( running )
                    bar.running( { n: self._index.job_path( n ) for n in running } )

                finished = [ n for n, ( p, _ ) in running.items() if not p.is_alive() ]
                if not finished:
                    # every worker is busy: pace the wait instead of
                    # spinning a core, and keep the job bars moving from
                    # the status files the children write
                    time.sleep( 0.05 )
                    if time.time() - last_bar_poll > BAR_POLL_INTERVAL:
                        last_bar_poll = time.time()
                        bar.poll()
                    continue

                for job_name in finished:
                    proc, iexp = running.pop( job_name )
                    proc.join()
                    error = errors.get( job_name )
                    if error is None and proc.exitcode != 0:
                        # hard death: segfault, oom-kill, terminate()
                        error = f'job process exited with code {proc.exitcode}'
                    if error is None:
                        try:
                            results[ iexp ] = self._index.read_outputs( job_name )
                        except DataNotFoundError as err:
                            error = str( err )
                    if error is not None:
                        self._index.set_state( job_name, 'failed' )
                        failure = ( job_name, error )
                        bar.failed( job_name )
                        break
                    self._index.set_state( job_name, 'done' )
                    self.run_iter += 1
                    self._report_status(
                        running, state='running' if running or queued else 'idle' )
                    bar.step()
                    bar.running( { n: self._index.job_path( n ) for n in running } )

                if failure is not None:
                    self._abort_running_jobs( running )
                    break
        finally:
            bar.close()
            manager.shutdown()

        if failure is not None:
            job_name, error = failure
            # the jobs of this batch are all over: the one that failed, and
            # the ones terminated with it (_abort_running_jobs stamped their
            # own status files, which their killed processes could not)
            self._report_status( [], state='failed' )
            self._finish_status( 'failed' )
            raise AsyncActionError( f"Job '{job_name}' failed: {error}" )

        self._finish_status( 'done' )
        return results

    def _abort_running_jobs( self, running ):
        """Stop the jobs still running after one of them failed, and mark
        them failed in the index: they were cut short, so their directories
        hold no usable result."""
        for job_name, ( proc, _ ) in list( running.items() ):
            if proc.is_alive():
                proc.terminate()
                proc.join( timeout=2.0 )
            self._index.set_state( job_name, 'failed' )
            # a terminated process cannot report itself, and its file would
            # otherwise claim to be running for ever
            mark_failed( self._index.job_path( job_name ) / STATUS_PATH,
                         message='terminated: another job failed' )
            logger.warning( f'Terminated {job_name}: another job failed.' )
        running.clear()

    # ------------------------------------------------------------------
    # retrieving and grouping results already on disk
    #
    # These methods never run the graph. They work on an iterator that was
    # only just constructed (pointed at an existing results directory), so
    # results can be inspected in a separate session or by a GUI.

    def job_index( self, rebuild=False ):
        """The ``JobIndex`` of the results directory.

        Arguments:
            rebuild (bool) : re-derive the index from the job directories
                on disk. Group labels of known jobs are preserved.
        """
        if rebuild:
            self._index.load( rebuild_if_missing=False )
            self._index.rebuild()
        else:
            self._index._ensure()
        return self._index

    def find_jobs( self, where=None, groups=None, state='done', match_all_groups=False ):
        """Paths of the job directories matching variable values and/or groups.

        Arguments:
            where (dict) : variable values to match; only the variables
                given are compared (``{'K': 0.2}`` matches any T).
            groups (str|list) : keep jobs carrying any of these labels
                (all of them when ``match_all_groups``).
            state (str) : required job state, default 'done'; None for any.
        """
        idx = self.job_index()
        return [ idx.job_path( r ) for r in
                 idx.find( where=where, groups=groups, state=state,
                           match_all_groups=match_all_groups ) ]

    def find_job( self, where=None, groups=None, state='done' ):
        """Path of the first matching job directory, or None."""
        hits = self.find_jobs( where=where, groups=groups, state=state )
        return hits[0] if hits else None

    def results_for( self, variables, groups=None ):
        """The stored action outputs of the job run with these variable
        values - the results of a past run, without running the graph.

        A job whose variables are exactly ``variables`` is preferred - the
        most recent one, if the design point was run more than once; a
        partial match is accepted when it is unambiguous.

        Raises:
            DataNotFoundError: when no job matches, or when several jobs
                match only partially. Use ``find_jobs`` or ``collect`` to
                get every job for a design point that was run repeatedly.
        """
        idx = self.job_index()
        rec = idx.find_exact( variables )
        if rec is None:
            hits = idx.find( where=variables, groups=groups )
            if not hits:
                raise DataNotFoundError(
                    f'No completed job in {self.work_area_path} was run with {variables}.' )
            if len( hits ) > 1:
                names = ', '.join( r['job'] for r in hits[:5] )
                raise DataNotFoundError(
                    f'{len(hits)} jobs match {variables} ({names}...): the request is '
                    f'ambiguous. Use find_jobs() or collect() to get them all.' )
            rec = hits[0]
        return idx.read_outputs( rec )

    def variables_of( self, job ):
        """The variable values a job was run with (job name or Path)."""
        idx = self.job_index()
        name = Path( job ).name if isinstance( job, ( str, Path ) ) else job
        for rec in idx.records:
            if rec.get( 'job' ) == name:
                return rec.get( 'variables', {} )
        raise DataNotFoundError( f'No job \'{name}\' in {self.work_area_path}.' )

    def groups_of( self, job ):
        """The group labels of a job (job name or Path)."""
        idx = self.job_index()
        name = Path( job ).name if isinstance( job, ( str, Path ) ) else job
        for rec in idx.records:
            if rec.get( 'job' ) == name:
                return list( rec.get( 'groups', [] ) )
        raise DataNotFoundError( f'No job \'{name}\' in {self.work_area_path}.' )

    def group_names( self ):
        """All group labels in use in this results directory."""
        return self.job_index().group_names()

    def add_groups( self, groups, jobs=None, where=None, state='done' ):
        """Label jobs with one or more groups.

        Give either ``jobs`` (names or Paths) or ``where`` (variable values
        selecting the jobs). Grouping is metadata, so jobs can be labelled
        long after they ran, and a job can be in several groups.

        Returns:
            list: names of the jobs that were changed.
        """
        idx = self.job_index()
        if jobs is None:
            jobs = [ p.name for p in self.find_jobs( where=where, state=state ) ]
        else:
            jobs = [ Path( j ).name if isinstance( j, ( str, Path ) ) else j for j in jobs ]
        return idx.add_groups( jobs, groups )

    def remove_groups( self, groups, jobs=None, where=None, state='done' ):
        """Remove group labels from jobs. See ``add_groups``."""
        idx = self.job_index()
        if jobs is None:
            jobs = [ p.name for p in self.find_jobs( where=where, state=state ) ]
        else:
            jobs = [ Path( j ).name if isinstance( j, ( str, Path ) ) else j for j in jobs ]
        return idx.remove_groups( jobs, groups )

    def collect( self, groups=None, where=None, state='done', match_all_groups=False ):
        """Read back the results of a set of jobs already on disk.

        Returns the same pair as ``collect_for_expdes`` - so a group of
        runs can be plotted or post-processed exactly like a fresh sweep -
        but nothing is executed.

        Arguments:
            groups (str|list) : keep jobs carrying any of these labels.
            where (dict) : variable values to match (partial).
            match_all_groups (bool) : require every label instead of any.

        Returns:
            par_val_dict (dict): variable name -> array of values, one per job.
            outcome (dict): action name -> list of values, one per job.
        """
        idx = self.job_index()
        recs = idx.find( where=where, groups=groups, state=state,
                         match_all_groups=match_all_groups )
        if not recs:
            raise DataNotFoundError(
                f'No completed job in {self.work_area_path} matches '
                f'groups={groups}, where={where}.' )

        list_of_evals = [ idx.read_outputs( r ) for r in recs ]
        outcome = SimulationIterator.outcomes_as_lists( list_of_evals )

        var_names = list( recs[0].get( 'variables', {} ).keys() )
        par_val_dict = {}
        for key in var_names:
            vals = []
            for r in recs:
                if key not in r.get( 'variables', {} ):
                    raise ParameterError( f'Variable \'{key}\' not set in all runs' )
                vals.append( r['variables'][key] )
            par_val_dict[key] = np.array( vals )

        return par_val_dict, outcome

    def outputs(self):
        return self.graph.outputs()

    @staticmethod
    def _subdicts_as_lists( outcome ):
        """
        Rewrites the values of the input dictionary if they are lists of dictionaries.

        This function iterates over the items in the input dictionary `outcome`. For each key-value pair,
        if the value is a list and the first element of the list is a dictionary, it transforms the list 
        of dictionaries into a dictionary of lists. The keys of the new dictionary are the keys of the 
        dictionaries in the list, and the values are lists of the corresponding values from the original 
        dictionaries.

        Args:
            outcome (dict): The input dictionary to be rewritten.

        Returns:
            None: The function modifies the input dictionary in place.
        """
        for k, val_list in outcome.items():
            if len(val_list) > 0:
                if type(val_list[0]) == dict:
                    nd = {kk:[] for kk in val_list[0].keys()}
                    for vv in val_list:
                        for kk in nd.keys():
                            nd[kk].append( vv[kk] )
                    outcome[k] = nd



    def collect_for_varrange( self, var_range_dict, dependent_pars=None,
                              groups=None, progress_bar=None ):
        """
        Creates a combination of var_range_dict. Input is not a experimental design.

        The combinations are evaluated one at a time, or ``max_workers`` at
        a time when the iterator was given that argument.

        Args:
            var_range_dict (dict) : variable name, values to combine.
            dependent_pars (dict) : variable name, expression.
            groups (str|list) : group label(s) for the jobs of this sweep,
                overriding the iterator's default.
            progress_bar (bool) : passed to ``solve_parallel`` when the
                design points run in parallel; ignored for a serial sweep,
                which has no bars. The default None shows them when tqdm is
                installed and stderr is a terminal.

        Returns:
            par_val_dict (dict): parameter names, value list
            outcome (dict): evaluation name, value list. Value can be list or dict (of lists).
        """

        iterators_values = var_range_dict.values()
        exp_des =  [p for p in product(*iterators_values)]
        var_names = [key for key in var_range_dict.keys() ]
        return self.collect_for_expdes( exp_des, var_names, dependent_pars,
                                        groups=groups, progress_bar=progress_bar )

    def _design_values( self, combination, var_names, dependent_pars ):
        """The variable values of one design point: the combination itself
        plus the parameters derived from it."""
        pars_vals = {key:combination[i] for i,key in enumerate(var_names) }
        if dependent_pars is not None:
            dp_dict = {}
            for key in dependent_pars:
                dp_dict[key] = eval( dependent_pars[key], {}, pars_vals )
            pars_vals.update( dp_dict )
        return pars_vals

    def collect_for_expdes( self, exp_des, var_names, dependent_pars=None,
                            groups=None, progress_bar=None ):
        """
        Evaluate every design point of an experimental design, one at a
        time or ``max_workers`` at a time (see the constructor). The
        results are returned in the order the design points were given,
        whichever way they ran.

        Args:
            exp_des
            groups (str|list) : group label(s) for the jobs of this sweep,
                overriding the iterator's default.
            progress_bar (bool) : passed to ``solve_parallel`` when the
                design points run in parallel; ignored for a serial sweep,
                which has no bars. The default None shows them when tqdm is
                installed and stderr is a terminal.

        Returns:
            par_val_dict (dict): parameter names, value list
            outcome (dict): evaluation name, value list. Value can be list or dict (of lists).
        """
        disp = []
        more_node_data = []

        self._start_batch( len( exp_des ) )

        all_combinations = []
        design_points = []
        for combination in exp_des:
            all_combinations.append( combination )
            design_points.append(
                self._design_values( combination, var_names, dependent_pars ) )

        if self.max_workers > 1 and len( design_points ) > 1:
            logger.debug( f'\n\tRunning {len(design_points)} evaluations, '
                          f'{self.max_workers} at a time' )
            list_of_evals = self.solve_parallel( design_points, groups,
                                                 progress_bar=progress_bar )
        else:
            list_of_evals = []
            for iexp, pars_vals in enumerate( design_points ):
                logger.debug( f'\n\tRunning evaluation {iexp+1} of {len(exp_des)} {pars_vals}' )
                logger.debug( f'\n\t Parameters: {pars_vals}' )
                logger.debug(   f'\t Dependent parameters: {dependent_pars}' )
                evals = self.solve( pars_vals, groups=groups )
                for k,v in evals.items():
                    if isinstance(v,numbers.Number):
                        logger.debug( f'\t\t Result: {k},{v}' )
                    else:
                        logger.debug( f'\t\t Result: {k},{type(v)}' )

                list_of_evals.append( evals )

        outcome = SimulationIterator.outcomes_as_lists( list_of_evals )

        self._finish_status( 'done' )

        all_combinations = np.array( all_combinations )
        par_val_dict = {key: None for key in var_names }
        for i, key in enumerate( var_names ):
            par_val_dict[key] = all_combinations[:,i]

        return par_val_dict, outcome

    def parameters(self):
        if self._parameters_cache is not None:
            return self._parameters_cache
        tmp_path = self.work_area_path / 'variables_discovery'
        tmp_path.mkdir(mode=0o777, parents=True, exist_ok=True)

        if self.copy_paths:
            logger.debug(f'Copying paths {self.copy_paths}')
            for fname in self.copy_paths:
                src = Path(fname)
                if not src.exists():
                    raise MissingPathError(f'Path "{fname}" not found. Either the path does not exist or you must specify the full path.')
                if src.is_dir():
                    shutil.copytree(src, tmp_path / src.name, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, tmp_path)

        root_dir = Path.cwd()
        os.chdir(tmp_path)
        try:
            vrs = self.graph.parameters()
        finally:
            os.chdir(root_dir)
        self._parameters_cache = vrs
        return vrs
