"""
Removing bulk solver output from run directories after a graph has run.

A design study keeps every job directory, and solver field output (d3plot
files, OpenRadioss animation files, VTK directories) is what fills a disk.
The policy for removing it is a :class:`kunene.args.Cleanup`, passed as
the ``cleanup`` argument of a ``WorkArea`` or a ``SimulationIterator``.

The split of responsibility is deliberate:

* the *actions* know which of their files are bulk output -- they declare
  it through ``WorkAction._disposable_files()``, and the user can subtract
  from that per action with ``DynaAnalysis( ..., keep=['d3plot'] )``;
* the *work areas* know *when* deleting is safe -- only once the whole
  graph has run, so a d3plot reader downstream of the solver has already
  read what it needed, and never for a job that failed, whose deck and log
  are what you need to debug it.

This module holds the walk that turns an action tree plus a policy into a
per-directory delete plan, and the code that applies it.
"""

import fnmatch
import shutil
from pathlib import Path

from kunene.args import PROTECTED_FROM_CLEANUP

import logging
logger = logging.getLogger(__name__)


def _build_plan( action, run_dir, cleanup ):
    """
    Work out what to delete, per directory, for an action subtree.

    Walks the action tree from ``action``, tracking the directory each
    action runs in: a nested ``WorkArea`` moves its children into its own
    subdirectory, so its solver's files are found there rather than in the
    parent's directory. A nested work area that carries its own ``cleanup``
    overrides the inherited policy for its subtree; a nested
    ``SimulationIterator`` is skipped, since it cleans its own job
    directories as it runs them.

    Arguments:
        action (WorkAction) : root of the subtree to clean.
        run_dir (str|Path) : directory ``action`` runs in.
        cleanup (Cleanup) : the policy to apply.
    Returns:
        dict : ``{ directory (Path) : ( remove globs, keep globs ) }``.
    """
    plan = {}
    if cleanup is not None:
        _walk( action, Path( run_dir ), cleanup, plan )
    return plan


def _walk( action, run_dir, cleanup, plan ):

    own = getattr( action, 'cleanup', None )
    if own is not None:
        cleanup = own       # a nested work area overrides for its subtree

    here = Path( action._cleanup_run_dir( run_dir ) )

    # a SimulationIterator numbers its own job directories and cleans them
    # as it runs them. An enclosing plan does not descend into it, but must
    # know its results root so that a 'remove everything' policy in the
    # parent directory does not delete the whole study.
    if getattr( action, '_cleans_own_dirs', False ):
        plan.setdefault( here, ( [], [] ) )
        return

    remove = cleanup.globs_for( action )
    keep = list( cleanup.keep ) + list( getattr( action, 'keep_files', [] ) )
    if remove or keep:
        prev_remove, prev_keep = plan.get( here, ( [], [] ) )
        plan[here] = ( _extend_unique( prev_remove, remove ),
                       _extend_unique( prev_keep, keep ) )

    for child in action._tree_children():
        _walk( child, here, cleanup, plan )


def _extend_unique( base, extra ):
    out = list( base )
    for e in extra:
        if e not in out:
            out.append( e )
    return out


def _apply_plan( plan, dry_run=False ):
    """
    Delete the files a plan selects.

    Protected files (``kunene.args.PROTECTED_FROM_CLEANUP``) and the run
    directories of the plan itself are never removed, whatever the patterns
    match -- so a ``Cleanup( remove='*' )`` empties a job directory without
    taking out the nested work area whose own files are being kept.

    Arguments:
        plan (dict) : as returned by :func:`_build_plan`.
        dry_run (bool) : report without deleting.
    Returns:
        list : the paths removed (or that would be removed), as strings.
    """
    protected_dirs = { d.resolve() for d in plan if d.exists() }
    removed = []

    for directory, ( remove, keep ) in plan.items():
        if not directory.is_dir():
            continue

        kept = _matches( directory, keep )
        for path in sorted( _matches( directory, remove ) ):
            if path in kept:
                continue
            if path.name in PROTECTED_FROM_CLEANUP:
                continue
            if _holds_run_dir( path, protected_dirs ):
                continue

            removed.append( str( path ) )
            if dry_run:
                continue
            try:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree( path )
                else:
                    path.unlink()
            except OSError as err:
                # cleanup is housekeeping: a file already gone, or one the
                # solver still holds open, must not fail a finished run
                logger.warning( f'Could not remove {path}: {err}' )
                removed.pop()

    if removed:
        what = 'Would remove' if dry_run else 'Removed'
        logger.info( f'{what} {len(removed)} path(s) during cleanup.' )
        logger.debug( f'{what}: {removed}' )
    return removed


def _matches( directory, globs ):
    """Paths in ``directory`` matched by any of the glob patterns."""
    found = set()
    for pattern in globs:
        pattern = str( pattern ).rstrip( '/' )
        if not pattern:
            continue
        try:
            found.update( directory.glob( pattern ) )
        except ( ValueError, NotImplementedError ):
            logger.warning( f'Ignoring cleanup pattern {pattern!r}.' )
    return found


def _holds_run_dir( path, protected_dirs ):
    """True if ``path`` is, or contains, a directory the plan cleans."""
    if not path.is_dir():
        return False
    resolved = path.resolve()
    return any( resolved == d or resolved in d.parents for d in protected_dirs )


def clean_run_dir( action, run_dir, cleanup ):
    """
    Build and apply a cleanup plan for one run directory.

    Arguments:
        action (WorkAction) : the action (usually a graph) that ran.
        run_dir (str|Path) : the directory it ran in.
        cleanup (Cleanup) : policy, or ``None`` to do nothing.
    Returns:
        list : the paths removed, as strings.
    """
    if cleanup is None:
        return []
    return _apply_plan( _build_plan( action, run_dir, cleanup ),
                       dry_run=cleanup.dry_run )


def marks_removed( name, cleanup, action ):
    """
    Whether the work-directory display should mark an entry as removed.

    Used by ``print_work_dir()`` so the predicted directory structure stays
    honest about what cleanup takes away again.

    Arguments:
        name (str) : file or directory name the action produces.
        cleanup (Cleanup) : the policy in force, or ``None``.
        action (WorkAction) : the action that produces it.
    Returns:
        bool
    """
    if cleanup is None:
        return False
    name = name.rstrip( '/' )
    if name in PROTECTED_FROM_CLEANUP:
        return False
    keep = list( cleanup.keep ) + list( getattr( action, 'keep_files', [] ) )
    if any( fnmatch.fnmatch( name, str(k).rstrip('/') ) for k in keep ):
        return False
    return any( fnmatch.fnmatch( name, str(g).rstrip('/') )
                for g in cleanup.globs_for( action ) )
