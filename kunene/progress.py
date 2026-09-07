"""
Progress reporting for long-running workflows.

Status is exchanged between the workflow process and consumers (e.g. a GUI
running as a separate process) through small ``status.json`` files written
into the work directories -- the same channel already used for
``iter_variables.json`` and ``actions_output.pkl``. Files are written
atomically (temp file + ``os.replace``), so a reader never sees a
half-written file, and either side can start, stop or crash independently.

Writer side (used internally by ``DirectedGraph`` and
``SimulationIterator``):

* ``StatusReporter`` -- owns the ``status.json`` of one directory, writes
  it on every state change, and keeps a heartbeat timestamp fresh from a
  daemon thread so a reader can tell a slow run from a dead one.
* ``MultiReporter`` -- reports to several of those at once, which is how
  a graph inside a ``WorkArea`` fills both the work area's own file and
  the file of the graph that encloses it.

Reader side (for the GUI):

* ``StatusWatcher`` -- cheap mtime-based polling of one status file.
* ``RunWatcher`` -- follows a ``SimulationIterator`` results tree: the root
  status plus the job(s) running now.
* ``watch_run()`` -- blocking generator over ``RunWatcher`` for scripts;
  GUIs with an event loop should call ``RunWatcher.poll()`` from a timer
  instead.
* ``is_alive()`` -- heartbeat-based liveness check of a status dict.
* ``job_fraction()`` -- one job's overall progress, averaged over its
  actions, for a caller that wants a single number (a progress bar).

Status file schema (a graph's file; the iterator's root file has
``jobs_total``/``jobs_done``/``current_job``/``current_jobs`` instead of
``actions`` -- ``current_jobs`` lists the jobs running at this moment,
more than one when the iterator runs with ``max_workers`` > 1, while
``current_job`` names the last job started and is kept for readers that
follow a single job)::

    {
      "name": "Radioss_WorkFlow",
      "state": "running",              # pending|running|idle|done|failed
      "pid": 12345,
      "heartbeat_interval": 5.0,
      "started_at": 1751871242.1,      # epoch seconds
      "updated_at": 1751880093.7,
      "actions": {
        "jinja_prep": {"state": "done",    "fraction": null, "message": null},
        "rad_solver": {"state": "running", "fraction": null, "message": null}
      }
    }

The ``fraction`` and ``message`` fields carry an action's own
percent-complete: solver actions fill them from their stdout files, and an
action of your own does so with ``self.report_progress(...)``. They stay
``null`` for an action that does not report, or whose output cannot be
parsed.
"""

import json
import os
import threading
import time
import weakref
from pathlib import Path

from kunene.args import STATUS_PATH

import logging
logger = logging.getLogger(__name__)


HEARTBEAT_INTERVAL = 5.0

# suffix of the per-action sidecar files written by forked child processes
# (asynch actions); the owning process merges them into status.json
SIDECAR_SUFFIX = '.progress.json'

# A directory has one owning reporter per process: a graph nested inside
# another graph runs in the same directory and must not clobber the owner's
# file. The nested graph's reporter simply becomes a no-op and the graph
# reports its actions through the owner's reporter instead.
#
# Path -> weak reference to the reporter that owns it. Weak, so an owner
# that was dropped without being finished (a workflow object that went out
# of scope) does not hold its directory for the life of the process.
_owned_paths = {}
_owned_lock = threading.Lock()


def _owner_of( path ):
    """The live, still-active reporter of this path, if any. Caller holds
    _owned_lock."""
    ref = _owned_paths.get( path )
    owner = ref() if ref is not None else None
    return owner if owner is not None and owner._active else None


def _write_json_atomic( path, data ):
    """Write data as JSON such that a concurrent reader never sees a
    partial file: write to a sibling temp file, then os.replace (atomic
    on POSIX)."""
    tmp = path.with_name( path.name + '.tmp' )
    with open( tmp, 'w' ) as f:
        json.dump( data, f, indent=1 )
    os.replace( tmp, path )


class StatusReporter:
    """
    Writes the ``status.json`` of one directory.

    Used by ``DirectedGraph.solve`` (per-action states, in the run
    directory) and ``SimulationIterator.solve`` (job counts, at the results
    root). A write happens on every state change; between changes a daemon
    thread rewrites the file every ``heartbeat_interval`` seconds so
    ``updated_at`` stays fresh while the process is alive.

    Reporting must never break a workflow: write errors are logged and
    swallowed, and a reporter for a directory that is already owned by
    another reporter in this process silently becomes a no-op.

    **Fork safety (asynch actions).** An asynch action runs ``solve()`` in
    a forked child process holding a copy of this reporter. A child never
    touches ``status.json`` (two processes rewriting one file would race)
    and never uses the inherited lock (which may have been copied in a
    locked state): ``action_state`` detects the pid change and writes the
    single entry to a per-action sidecar file
    (``.<action>.progress.json``) instead. The owning process folds
    sidecars into ``status.json`` on every write and every heartbeat, so
    child fractions surface at heartbeat cadence, and deletes them when
    the action reaches a terminal state (or at ``finish``).

    Arguments:
        name (str) : name of the graph/iterator this status describes.
        directory (str|Path) : where ``status.json`` is written. Default
            is the current working directory (resolved immediately, so
            later ``os.chdir`` calls do not move the file).
        heartbeat_interval (float) : seconds between heartbeat writes.
            Default is ``progress.HEARTBEAT_INTERVAL`` (read at call time).
        takeover (bool) : claim the directory even when another reporter in
            this process still owns it, stopping that one instead of going
            quiet. A new run of a study takes its results root over this
            way -- the previous run is over, and its reporter must not go
            on heartbeating into the new run's file. The default False is
            what a graph wants: a graph nested in an owned directory stays
            a no-op and reports through the owner.
    """

    def __init__( self, name, directory='.', heartbeat_interval=None,
                  takeover=False ):
        if heartbeat_interval is None:
            heartbeat_interval = HEARTBEAT_INTERVAL
        self.path = Path( directory ).resolve() / STATUS_PATH
        self.heartbeat_interval = heartbeat_interval
        self._owner_pid = os.getpid()
        self._lock = threading.Lock()
        self._hb_thread = None
        self._hb_stop = threading.Event()

        with _owned_lock:
            owner = _owner_of( self.path )
            if owner is not None and not takeover:
                self._active = False
            else:
                if owner is not None:
                    owner._displace()
                _owned_paths[ self.path ] = weakref.ref( self )
                self._active = True

        # Actions whose state this process has actually set. Only for those
        # does the owner keep authority over the state when merging the
        # sidecar files of a child process (see _merge_sidecars): an action
        # inside a pass-through container that ran in a child process is
        # registered by start() but driven only in that child, so its
        # sidecar is what the state has to come from.
        self._driven = set()

        now = time.time()
        self._status = {
            'name': name,
            'state': 'pending',
            'pid': os.getpid(),
            'heartbeat_interval': heartbeat_interval,
            'started_at': now,
            'updated_at': now,
            'actions': {},
        }

    # ------------------------------------------------------------------
    # crossing to a child process

    def __getstate__( self ):
        """Pickle the reporter without its synchronisation primitives.

        Under the ``spawn`` start method (Windows, and Python 3.14 on
        Linux) an action carries its reporter to the child as a pickle,
        and a lock, an event and a running thread do not pickle. A child
        needs none of them: it writes per-action sidecar files, one writer
        per file and so without the lock, and it never heartbeats. Under
        ``fork`` the child gets copies of these objects instead -- copies
        it likewise never uses, the inherited lock possibly copied in a
        locked state, which is why ``action_state`` keys off ``_owner_pid``
        rather than off what it holds. ``_owner_pid`` travels with the
        state, so a spawned child still recognises itself as a child.
        """
        state = self.__dict__.copy()
        for key in ( '_lock', '_hb_thread', '_hb_stop' ):
            state.pop( key, None )
        return state

    def __setstate__( self, state ):
        self.__dict__.update( state )
        self._lock = threading.Lock()
        self._hb_thread = None
        self._hb_stop = threading.Event()

    @property
    def active( self ):
        """False when another reporter in this process owns the directory
        (all reporting methods are then no-ops)."""
        return self._active

    def start( self, actions=None, **fields ):
        """Mark the run as started and begin the heartbeat.

        Arguments:
            actions (list) : names of the actions to report on; all start
                in state 'pending'. Omit for an iterator-level status.
            fields : extra top-level fields (e.g. jobs_total=48).
        """
        if not self._active: return
        if os.getpid() != self._owner_pid: return
        with self._lock:
            self._status['state'] = 'running'
            self._status['started_at'] = time.time()
            if actions is not None:
                self._driven = set()
                self._status['actions'] = {
                    a: { 'state': 'pending', 'fraction': None, 'message': None }
                    for a in actions }
            self._status.update( fields )
            self._write()
        self._hb_stop.clear()
        self._hb_thread = threading.Thread( target=self._heartbeat, daemon=True )
        self._hb_thread.start()

    def action_state( self, action, state, fraction=None, message=None ):
        """Set one action's state ('pending'|'running'|'done'|'failed')."""
        if not self._active: return
        if os.getpid() != self._owner_pid:
            # forked child (asynch action): sidecar file, one writer per
            # file, no inherited lock; the owner merges it on its next write
            self._write_sidecar( action, { 'state': state, 'fraction': fraction,
                                           'message': message } )
            return
        with self._lock:
            entry = self._status['actions'].setdefault( action, {} )
            entry['state'] = state
            entry['fraction'] = fraction
            entry['message'] = message
            self._driven.add( action )
            self._write()

    def fail_running( self, actions, message=None ):
        """Mark as failed those of ``actions`` that are still running.

        Used when the process that was reporting them died -- an asynch
        work area that crashed, or one terminated because a sibling
        failed. Actions that already reached a terminal state keep it, and
        ones that never started stay 'pending'; only what was in flight
        becomes 'failed'. Sidecars are merged first, so a child's own last
        word wins over this.
        """
        if not self._active: return
        if os.getpid() != self._owner_pid: return
        with self._lock:
            self._merge_sidecars()
            for action in actions:
                entry = self._status['actions'].get( action )
                if entry is None or entry.get( 'state' ) != 'running':
                    continue
                self._status['actions'][action] = { 'state': 'failed',
                                                    'fraction': None,
                                                    'message': message }
                self._driven.add( action )
            self._write()

    def update( self, **fields ):
        """Update top-level fields (e.g. state='idle', jobs_done=3)."""
        if not self._active: return
        if os.getpid() != self._owner_pid: return
        with self._lock:
            self._status.update( fields )
            self._write()

    def finish( self, state='done' ):
        """Stop the heartbeat, write the final state and release the
        directory so a later run can own it again."""
        if not self._active: return
        if os.getpid() != self._owner_pid: return
        self._hb_stop.set()
        if self._hb_thread is not None:
            self._hb_thread.join( timeout=2.0 )
            self._hb_thread = None
        with self._lock:
            self._status['state'] = state
            self._write()   # merges any remaining sidecars first
            for part in self.path.parent.glob( '.*' + SIDECAR_SUFFIX ):
                try:
                    part.unlink()
                except OSError:
                    pass
        with _owned_lock:
            ref = _owned_paths.get( self.path )
            if ref is not None and ref() is self:
                del _owned_paths[ self.path ]
        self._active = False

    def _displace( self ):
        """Another reporter has taken this directory over (a new run of
        the same study). Stop reporting, and stop the heartbeat: the file
        belongs to the new owner now. The status file itself is left
        alone -- rewriting it here is exactly what must not happen."""
        self._active = False
        self._hb_stop.set()

    def _heartbeat( self ):
        while not self._hb_stop.wait( self.heartbeat_interval ):
            with self._lock:
                if not self._write():
                    break   # directory is gone; stop quietly

    def _write_sidecar( self, action, entry ):
        """Write one action's entry to its sidecar file (called in a forked
        child process). Atomic; no lock needed -- one writer per file."""
        part = self.path.parent / f'.{action}{SIDECAR_SUFFIX}'
        data = dict( entry )
        data['action'] = action
        data['updated_at'] = time.time()
        try:
            _write_json_atomic( part, data )
        except OSError as err:
            logger.warning( f'Could not write progress sidecar {part}: {err}' )

    def _merge_sidecars( self ):
        """Fold child-process sidecar files into the actions dict. Caller
        holds self._lock. For actions this process drives itself, the owner
        keeps authority over the *state* and takes only fraction/message
        while the action is running; a sidecar for a terminal action is
        deleted. Actions it never drives -- those of a graph nested inside
        an asynch action, and those of a pass-through container that ran in
        a child process -- are adopted wholesale, state included."""
        try:
            parts = list( self.path.parent.glob( '.*' + SIDECAR_SUFFIX ) )
        except OSError:
            return
        for part in parts:
            try:
                data = json.loads( part.read_text() )
                aname = data['action']
            except ( OSError, ValueError, KeyError ):
                continue
            entry = self._status['actions'].get( aname )
            if aname not in self._driven:
                # an action this process never sets the state of: the child
                # that runs it is the only one that knows, so the sidecar is
                # authoritative (state included)
                self._status['actions'][aname] = {
                    'state': data.get( 'state', 'running' ),
                    'fraction': data.get( 'fraction' ),
                    'message': data.get( 'message' ) }
            elif entry is not None and entry.get( 'state' ) == 'running':
                entry['fraction'] = data.get( 'fraction' )
                entry['message'] = data.get( 'message' )
            else:
                try:
                    part.unlink()
                except OSError:
                    pass

    def _write( self ):
        """Write the status file. Caller holds self._lock. Returns False on
        failure -- reporting must never break the workflow."""
        self._merge_sidecars()
        self._status['updated_at'] = time.time()
        try:
            _write_json_atomic( self.path, self._status )
            return True
        except OSError as err:
            logger.warning( f'Could not write status file {self.path}: {err}' )
            return False


class MultiReporter:
    """
    Reports the same action states to several status files at once.

    A graph inside a ``WorkArea`` has two audiences: its own
    ``status.json`` in the work-area directory (which a GUI, or a
    standalone ``WorkArea``, follows on its own) and the ``status.json`` of
    the graph that encloses the work area -- a job directory, typically,
    whose file is what the per-job progress bars read. The work area itself
    holds no entry there (it is pass-through, see
    ``WorkAction._progress_names``), so without this the enclosing file
    would say nothing at all while the solver inside runs.

    Only what a graph does *to an action* is forwarded -- reporting where
    it is, and failing what a dead child process left running. Starting and
    finishing a status file belong to the reporter that owns it. ``active``
    tells ``FileProgressTail`` and ``_RemoteProgressPoller`` there is
    somewhere to report to.

    Arguments:
        reporters (list) : the reporters to write to; inactive ones (and
            ``None``) are dropped, and reporting is in the given order.
    """

    def __init__( self, reporters ):
        self.reporters = [ r for r in reporters if r is not None and r.active ]

    @property
    def active( self ):
        return bool( self.reporters )

    def action_state( self, action, state, fraction=None, message=None ):
        for reporter in self.reporters:
            reporter.action_state( action, state, fraction=fraction,
                                   message=message )

    def fail_running( self, actions, message=None ):
        for reporter in self.reporters:
            reporter.fail_running( actions, message=message )


class FileProgressTail:
    """
    Daemon thread reporting a solver's percent-complete while it runs.

    Solvers print the current simulation time to their (redirected) stdout
    file, and the termination time is known from the input deck. This
    thread polls the tail of that file every ``interval`` seconds, extracts
    the latest time with ``parse_time`` (see
    ``kunene.util.solver_progress``), and reports
    ``(time - t_start) / (t_end - t_start)`` as the action's ``fraction``.

    Start it right before the blocking ``subprocess.run`` and stop it in a
    ``finally`` (so no 'running' write can land after the graph marks the
    action done)::

        tail = FileProgressTail( self._progress_reporter, self.name,
                                 'run_file.stdout', dyna_run_time, t_end )
        tail.start()
        try:
            subprocess.run( ... )
        finally:
            tail.stop()

    ``start()`` is a no-op when the reporter is missing/inactive or the
    termination time is unknown -- progress reporting must never break a
    solver run, so parse errors are swallowed too.

    When the termination time is not known up front but is printed by the
    solver into its output (e.g. Abstrao's ``# Termination time is X s``
    header), pass ``t_end=None`` and a ``find_t_end`` callable; the thread
    reads the *head* of the file each poll until it can extract ``t_end``,
    then reports normally. (It reads the head, not the tail, because such a
    header scrolls out of the tail window once the file grows.)

    Arguments:
        reporter (StatusReporter) : where to report; may be None.
        action_name (str) : the action entry to update.
        path (str|Path) : the solver output file to poll.
        parse_time (callable) : ``text -> float | None``, latest sim time.
        t_end (float) : termination time from the deck; None disables unless
            ``find_t_end`` is given.
        t_start (float) : start time (OpenFOAM restarts), default 0.
        interval (float) : polling period in seconds.
        tail_bytes (int) : how much of the file end to read per poll.
        find_t_end (callable) : ``head_text -> float | None`` to discover
            ``t_end`` from the file head when it is not known up front.
        head_bytes (int) : how much of the file start to read for it.
    """

    def __init__( self, reporter, action_name, path, parse_time, t_end,
                  t_start=0.0, interval=2.0, tail_bytes=65536,
                  find_t_end=None, head_bytes=8192 ):
        self.reporter = reporter
        self.action_name = action_name
        self.path = Path( path )
        self.parse_time = parse_time
        self.t_end = t_end
        self.t_start = t_start
        self.interval = interval
        self.tail_bytes = tail_bytes
        self.find_t_end = find_t_end
        self.head_bytes = head_bytes
        self._thread = None
        self._stop = threading.Event()
        self._last_fraction = None

    def start( self ):
        if self.reporter is None or not self.reporter.active:
            return
        if self.t_end is None and self.find_t_end is None:
            return
        if self.t_end is not None and self.t_end <= self.t_start:
            return
        self._stop.clear()
        self._thread = threading.Thread( target=self._loop, daemon=True )
        self._thread.start()

    def stop( self ):
        """Stop polling and wait for the thread, so no further write can
        race with the action's final 'done'/'failed' state."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join( timeout=self.interval + 2.0 )
            self._thread = None

    def _loop( self ):
        while not self._stop.wait( self.interval ):
            self._report_once()

    def _read_tail( self ):
        with open( self.path, 'rb' ) as f:
            f.seek( 0, os.SEEK_END )
            size = f.tell()
            f.seek( max( 0, size - self.tail_bytes ) )
            return f.read().decode( errors='replace' )

    def _read_head( self ):
        with open( self.path, 'rb' ) as f:
            return f.read( self.head_bytes ).decode( errors='replace' )

    def _resolve_t_end( self ):
        """Discover t_end from the file head once it is written; leave it
        None (keep trying) until a usable value appears."""
        try:
            found = self.find_t_end( self._read_head() )
        except OSError:
            return  # file not written yet
        except Exception as err:
            logger.warning( f'Termination parsing failed for {self.path}: {err}' )
            return
        if found is not None and found > self.t_start:
            self.t_end = found

    def _report_once( self ):
        if self.t_end is None and self.find_t_end is not None:
            self._resolve_t_end()
            if self.t_end is None:
                return  # termination not known yet
        try:
            t = self.parse_time( self._read_tail() )
        except OSError:
            return  # file not written yet
        except Exception as err:
            logger.warning( f'Progress parsing failed for {self.path}: {err}' )
            return
        if t is None:
            return
        fraction = ( t - self.t_start ) / ( self.t_end - self.t_start )
        fraction = min( max( fraction, 0.0 ), 1.0 )
        if self._last_fraction is not None and abs( fraction - self._last_fraction ) < 1e-3:
            return
        self._last_fraction = fraction
        self.reporter.action_state( self.action_name, 'running',
                                    fraction=fraction,
                                    message=f'time {t:g} of {self.t_end:g}' )


def mark_failed( status_path, message=None ):
    """
    Stamp a status file whose writer has died.

    A process that is terminated -- a job of a parallel sweep cut short
    because another job failed -- cannot report itself, and would leave a
    file claiming to be running for ever. The parent that killed it calls
    this instead: the run state becomes 'failed', and so does every action
    that was still running. Actions that never started stay 'pending',
    since they never ran, and a file already reporting a terminal state is
    left as it is.

    A missing or unreadable file is not an error (the job may have died
    before writing one), and neither is a failed write: like every other
    write here, reporting must never break a workflow.

    Arguments:
        status_path (str|Path) : the ``status.json`` to stamp.
        message (str) : reason, recorded on the actions it fails.
    """
    path = Path( status_path )
    try:
        status = json.loads( path.read_text() )
    except ( OSError, ValueError ):
        return
    if status.get( 'state' ) in ( 'done', 'failed' ):
        return
    status['state'] = 'failed'
    for entry in ( status.get( 'actions' ) or {} ).values():
        if entry.get( 'state' ) == 'running':
            entry['state'] = 'failed'
            entry['fraction'] = None
            entry['message'] = message
    status['updated_at'] = time.time()
    try:
        _write_json_atomic( path, status )
    except OSError as err:
        logger.warning( f'Could not write status file {path}: {err}' )


def is_alive( status, grace=3.0 ):
    """
    True if the process that wrote this status dict appears alive: the file
    was updated within ``grace * heartbeat_interval`` seconds. Use it to
    distinguish a slow run from a dead one; a 'done'/'failed' state is
    final regardless of the heartbeat.
    """
    if not status:
        return False
    hb = status.get( 'heartbeat_interval', HEARTBEAT_INTERVAL )
    return ( time.time() - status.get( 'updated_at', 0.0 ) ) < grace * hb


class StatusWatcher:
    """
    Cheap polling reader of one status file, for a consumer in another
    process. ``poll()`` stats the file and re-reads it only when the mtime
    changed; a missing or (transiently) unparsable file is 'no news', never
    an error. The last successfully parsed status stays available as
    ``.last``.
    """

    def __init__( self, path ):
        self.path = Path( path )
        self.last = None
        self._signature = None

    def poll( self ):
        """Return the parsed status dict if it changed since the last call,
        else None."""
        try:
            st = self.path.stat()
            # mtime alone is not enough: filesystem timestamps have coarse
            # granularity (a kernel tick), so rapid successive writes can
            # share an mtime. os.replace gives the file a new inode on every
            # atomic write, so the inode reliably discriminates.
            signature = ( st.st_mtime_ns, st.st_ino, st.st_size )
            if signature == self._signature:
                return None
            self._signature = signature
            self.last = json.loads( self.path.read_text() )
            return self.last
        except ( FileNotFoundError, json.JSONDecodeError ):
            return None


class RunWatcher:
    """
    Follows a ``SimulationIterator`` results tree: the root ``status.json``
    (job counts) plus the ``status.json`` of every job it points at --
    'current_jobs' when several run at once (``max_workers`` > 1), else the
    single 'current_job'. ``poll()`` is non-blocking -- drive it from a GUI
    timer.
    """

    def __init__( self, results_root ):
        self.results_root = Path( results_root )
        self._root_watcher = StatusWatcher( self.results_root / STATUS_PATH )
        self._job_watchers = {}         # job name -> StatusWatcher

    @staticmethod
    def _job_names( root ):
        """The jobs to follow: the ones running now, or -- between jobs and
        at the end of a run -- the last one started, so its final state
        stays on display."""
        if not root:
            return []
        names = [ n for n in ( root.get( 'current_jobs' ) or [] ) if n ]
        if not names and root.get( 'current_job' ):
            names = [ root['current_job'] ]
        return names

    def poll( self ):
        """Return a snapshot if anything changed since the last call, else
        None: ``{'root':..., 'job_name':..., 'job':..., 'jobs': {name:
        status}}``, where 'jobs' holds every job being followed and
        'job_name'/'job' the first of them."""
        changed = self._root_watcher.poll() is not None
        root = self._root_watcher.last

        names = self._job_names( root )
        if list( self._job_watchers ) != names:
            # keep the watchers of jobs still being followed: a new one
            # would re-read a file that has not changed
            self._job_watchers = {
                n: self._job_watchers.get( n ) or
                   StatusWatcher( self.results_root / n / STATUS_PATH )
                for n in names }
            changed = True
        for watcher in self._job_watchers.values():
            if watcher.poll() is not None:
                changed = True

        if not changed:
            return None
        jobs = { n: w.last for n, w in self._job_watchers.items() }
        first = names[0] if names else None
        return { 'root': root,
                 'job_name': first,
                 'job': jobs.get( first ),
                 'jobs': jobs }


def watch_run( results_root, interval=1.0 ):
    """
    Blocking generator yielding ``RunWatcher`` snapshots whenever something
    changed. Convenient for scripts and terminals::

        for snap in watch_run('Radioss_WorkFlow'):
            print(format_status(snap))

    A GUI with an event loop should use ``RunWatcher.poll()`` directly.
    """
    watcher = RunWatcher( results_root )
    while True:
        snap = watcher.poll()
        if snap is not None:
            yield snap
        time.sleep( interval )


def job_fraction( status ):
    """
    How far one job's graph has got, as ``(fraction, message)``.

    A graph's status file holds per-action states, and for solver actions a
    fraction of that action. Averaging over the actions -- finished ones
    count as 1, the running one adds its own fraction when it has one --
    makes that a single number for the job, which is what a per-job
    progress bar needs. Returns ``(None, None)`` when the file says nothing
    yet.

    The fraction is therefore the *job's*, while the message belongs to the
    action running now. So that the two cannot be read as the same thing,
    the message says which action of how many is running and how far that
    action itself has got::

        rad 1 of 3: time 80 of 100 (80%)

    -- a solver 80% through the first of three actions, which leaves the
    job, and the bar, at 27%. The count is the action's place in the
    graph's action list, which is the order they appear in the status file.

    An ``asynch`` graph runs several actions at once, and then the message
    names them all with their own percentages instead::

        3 of 5 running: rad_a (80%), rad_b (34%), post
    """
    actions = ( status or {} ).get( 'actions' ) or {}
    if not actions:
        return None, None

    names = list( actions )
    done = 0.0
    running = []
    for name, entry in actions.items():
        state = entry.get( 'state' )
        if state in ( 'done', 'failed' ):
            done += 1.0
        elif state == 'running':
            fraction = entry.get( 'fraction' )
            if fraction:
                done += fraction
            running.append( ( name, entry, fraction ) )

    if not running:
        message = None
    elif len( running ) == 1:
        name, entry, fraction = running[0]
        message = f'{name} {names.index( name ) + 1} of {len( names )}'
        if entry.get( 'message' ):
            message += f": {entry['message']}"
        if fraction is not None:
            message += f' ({fraction*100:.0f}%)'
    else:
        # several actions at once: their own messages would not fit, so
        # each contributes only its name and its percentage
        parts = [ name if fraction is None else f'{name} ({fraction*100:.0f}%)'
                  for name, _, fraction in running ]
        message = ( f'{len( running )} of {len( names )} running: '
                    + ', '.join( parts ) )
    return done / len( actions ), message


def format_status( snapshot ):
    """
    Render a status dict (from ``StatusWatcher``) or a run snapshot (from
    ``RunWatcher``) as short human-readable text.
    """
    if snapshot is None:
        return '(no status)'
    if 'root' in snapshot and 'job' in snapshot:       # RunWatcher snapshot
        root = snapshot['root'] or {}
        total = root.get( 'jobs_total' )
        total = '?' if total is None else total
        lines = [ f"{root.get('name','?')}: {root.get('state','?')}, "
                  f"job {root.get('jobs_done', '?')} of {total}"
                  f"{'' if is_alive(root) else '   (no heartbeat)'}" ]
        jobs = snapshot.get( 'jobs' )
        if jobs is None:        # snapshot from before 'jobs' existed
            jobs = { snapshot.get( 'job_name' ): snapshot['job'] }
        for job_name, job_status in jobs.items():
            if job_status is None:
                continue
            job = format_status( job_status ).splitlines()
            if len( jobs ) > 1 and job_name:
                # several jobs at once: say which directory each one is
                job[0] = f'{job_name} - {job[0]}'
            lines += [ '  ' + l for l in job ]
        return '\n'.join( lines )

    lines = [ f"{snapshot.get('name','?')}: {snapshot.get('state','?')}"
              f"{'' if is_alive(snapshot) else '   (no heartbeat)'}" ]
    for aname, a in snapshot.get( 'actions', {} ).items():
        frac = a.get( 'fraction' )
        frac = f" {frac*100:.0f}%" if frac is not None else ''
        msg = a.get( 'message' )
        msg = f"   {msg}" if msg else ''
        lines.append( f"  {aname}: {a.get('state','?')}{frac}{msg}" )
    return '\n'.join( lines )
