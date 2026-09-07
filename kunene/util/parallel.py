"""
Choosing the multiprocessing start method kunene runs child processes
with, and starting them so that the calling script is left alone.

Two places in kunene run work in a child process: a ``SimulationIterator``
with ``max_workers`` > 1 (a process per job) and an ``asynch``
``DirectedGraph`` (a process per action). Both used to ask for ``fork``
explicitly, which exists only on POSIX -- on Windows the request fell
through to ``spawn`` and the run then died at ``Process.start()``, because
under ``spawn`` the target callable and its arguments must be *pickled* to
reach a fresh interpreter and both call sites passed a closure.

The workers are module-level functions now and the objects they carry
(actions, graphs, the iterator, ``StatusReporter``) know how to pickle
themselves, so both start methods work. ``fork`` stays the preference where
it exists: it is cheaper, it needs nothing to be picklable, and the child
inherits the parent's imports, logging configuration and working directory.
``spawn`` is the fallback -- on Windows because there is nothing else, and
on Python 3.14, where ``fork`` is no longer the default on Linux either.

**A spawned child does not re-import the calling script.** Stock
``multiprocessing`` makes a spawned child run the ``__main__`` module
again (as ``__mp_main__``) so that objects pickled by reference to it can
be found -- so the script's top-level code runs once more in every child,
and a script that starts the run at top level starts it again there
(multiprocessing raises a RuntimeError about bootstrapping). kunene does
not need the script in the child: the workers live in kunene modules and
the graph travels as a pickle. So ``start_process`` hides ``__main__``'s
``__spec__`` and ``__file__`` from multiprocessing while it collects what
the child is told to import, and the child comes up without touching the
script, which runs once, as written.

The one thing that then cannot travel is an object pickled *by reference
to the script* -- an action class (or a function) defined in the script
itself, since the child has no ``__main__`` to look it up in. ``start_process``
checks for that before starting the child and raises ``SpawnError`` naming
the offender: move it into a module the child can import. Setting
``IMPORT_MAIN`` (or ``KUNENE_SPAWN_IMPORTS_MAIN=1``) restores
multiprocessing's behaviour instead, with what it implies for the script.

Set ``KUNENE_START_METHOD`` (or ``kunene.util.parallel.START_METHOD``)
to force one method -- useful to exercise the Windows path on Linux, or to
avoid ``fork`` in a process that has already started threads.
"""

import io
import os
import sys
import pickle
import threading
import multiprocessing
from types import FunctionType

from kunene.errors import SpawnError

import logging
logger = logging.getLogger( __name__ )


# Overrides the preference below when set to a start method name
# ('fork', 'spawn', 'forkserver'). None leaves the choice to this module.
START_METHOD = None

ENV_VAR = 'KUNENE_START_METHOD'

# cheapest first; the first one this platform has is used
PREFERRED_METHODS = ( 'fork', 'spawn' )

# When true a spawned child re-imports the calling script, as stock
# multiprocessing has it: objects defined in the script can then be
# unpickled there, at the price of the script's top-level code running
# again in every child. None defers to the environment.
IMPORT_MAIN = None

IMPORT_MAIN_ENV_VAR = 'KUNENE_SPAWN_IMPORTS_MAIN'

# ``__main__`` is process-wide state; one start at a time may hide it
_main_lock = threading.Lock()


def _wanted_methods( preferred=None ):
    """Start methods to try, most wanted first."""
    forced = preferred or START_METHOD or os.environ.get( ENV_VAR ) or None
    if forced:
        # a forced method still falls back, so an unusable setting degrades
        # to a working run rather than to an exception
        return ( forced, ) + tuple( m for m in PREFERRED_METHODS if m != forced )
    return PREFERRED_METHODS


def get_context( preferred=None ):
    """
    A ``multiprocessing`` context to start kunene child processes with:
    ``fork`` where the platform has it, ``spawn`` otherwise (Windows).

    Arguments:
        preferred (str) : start method to try first, overriding
            ``START_METHOD`` and ``$KUNENE_START_METHOD``.
    Returns:
        multiprocessing.context.BaseContext
    """
    for method in _wanted_methods( preferred ):
        try:
            return multiprocessing.get_context( method )
        except ValueError:      # this platform has no such method
            continue
    logger.debug( 'None of the preferred start methods is available; '
                  'using the platform default.' )
    return multiprocessing.get_context()


def uses_fork( ctx=None ):
    """True when a child of this context inherits the parent's memory
    instead of being pickled into a fresh interpreter."""
    if ctx is None:
        ctx = get_context()
    return ctx.get_start_method() == 'fork'


def imports_main():
    """True when a spawned child is to re-import the calling script."""
    if IMPORT_MAIN is not None:
        return bool( IMPORT_MAIN )
    return os.environ.get( IMPORT_MAIN_ENV_VAR, '' ).lower() in ( '1', 'true', 'yes' )


class _MainReferenceFinder( pickle.Pickler ):
    """
    A pickler that only takes note of what it would pickle *by reference
    to the calling script* -- classes and functions whose module is
    ``__main__`` -- since those are what a child without the script cannot
    rebuild. The bytes go nowhere.
    """

    def __init__( self ):
        super().__init__( io.BytesIO() )
        self.found = []

    def reducer_override( self, obj ):
        if isinstance( obj, ( type, FunctionType ) ) \
                and getattr( obj, '__module__', None ) == '__main__':
            name = getattr( obj, '__qualname__', repr( obj ) )
            if name not in self.found:
                self.found.append( name )
        return NotImplemented         # pickle it the usual way


def main_references( *objects ):
    """
    Names (qualified) of the classes and functions defined in the calling
    script that pickling ``objects`` would refer to.

    A diagnostic only: an object that does not pickle at all is reported
    as far as the pickler got, and the real error is left to
    ``Process.start()``.
    """
    finder = _MainReferenceFinder()
    try:
        finder.dump( objects )
    except Exception:
        pass
    return finder.found


class _hidden_main:
    """
    Keep ``__main__`` out of the preparation data multiprocessing sends a
    spawned child: with neither a ``__spec__`` name nor a ``__file__`` to
    go by, ``spawn.get_preparation_data`` tells the child to import nothing.
    """

    _UNSET = object()

    def __enter__( self ):
        _main_lock.acquire()
        self.main = sys.modules.get( '__main__' )
        if self.main is None:
            return self
        self.spec = getattr( self.main, '__spec__', self._UNSET )
        self.file = getattr( self.main, '__file__', self._UNSET )
        # __spec__ is read directly (not with getattr), so it must exist
        self.main.__spec__ = None
        if self.file is not self._UNSET:
            del self.main.__file__
        return self

    def __exit__( self, *exc ):
        try:
            if self.main is not None:
                if self.spec is self._UNSET:
                    del self.main.__spec__
                else:
                    self.main.__spec__ = self.spec
                if self.file is not self._UNSET:
                    self.main.__file__ = self.file
        finally:
            _main_lock.release()
        return False


def _leaves_main_alone( ctx ):
    """True when children of ``ctx`` are to come up without the script."""
    return not uses_fork( ctx ) and not imports_main()


def start_manager( ctx ):
    """
    ``ctx.Manager()``, started the way ``start_process`` starts a child:
    the manager's server is a spawned process too, and would otherwise
    re-import the calling script.
    """
    if not _leaves_main_alone( ctx ):
        return ctx.Manager()
    with _hidden_main():
        return ctx.Manager()


def start_process( ctx, target, args=() ):
    """
    Create and start a child process running ``target( *args )``.

    Under ``fork`` this is ``ctx.Process( ... ).start()``. Under ``spawn``
    the child is started without re-importing the calling script (see the
    module docstring), unless ``imports_main()`` says otherwise.

    Arguments:
        ctx : the context from ``get_context``.
        target (callable) : a module-level function.
        args (tuple) : its arguments; pickled under ``spawn``.
    Returns:
        multiprocessing.Process : the started process.
    Raises:
        SpawnError : under ``spawn``, when ``args`` refer to a class or
            function defined in the calling script, which the child could
            not rebuild without importing the script.
    """
    proc = ctx.Process( target=target, args=args )
    if not _leaves_main_alone( ctx ):
        proc.start()
        return proc

    offenders = main_references( args )
    if offenders:
        raise SpawnError(
            f"Cannot start a child process with the '{ctx.get_start_method()}' "
            f"start method: {', '.join( offenders )} "
            f"{'is' if len( offenders ) == 1 else 'are'} defined in the "
            "calling script, and the child does not import the script. "
            "Move it into a module the child can import (any .py file on "
            "sys.path), or set KUNENE_SPAWN_IMPORTS_MAIN=1 to let the "
            "child re-import the script as multiprocessing normally does "
            "-- in which case the script must not start the run at top "
            "level." )

    with _hidden_main():
        proc.start()
    return proc
