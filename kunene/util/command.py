"""
Running external programs (solvers, converters) without a shell.

A solver command is given by the user either as a list, ``['runradioss',
'-np', '4']``, used as it is, or as a string. A string is taken to be a
single executable when it names one -- so ``C:\\Program Files\\LSTC\\ls-dyna.exe``
survives its spaces and backslashes -- and is otherwise split into the
program and its arguments, ``'runradioss -np 1 -nt 4'``. The split is
platform aware: POSIX ``shlex`` rules treat a backslash as an escape and would
turn ``C:\\LSTC\\ls-dyna.exe`` into ``C:LSTCls-dyna.exe``, so on Windows the
non-POSIX rules are used and the quotes they leave are stripped.

The program is started with ``shell=False``, so nothing in a file name is
interpreted by ``cmd.exe`` or ``sh``: redirect output with ``stdout=`` rather
than ``>``. A command needing a shell (``module load ...; ls-dyna``) belongs in
a wrapper script, whose path is then the command.

Passing ``cmd_file=`` to :func:`run` appends the command line to that file
before the program starts, so a run directory records how its solver was
invoked (``ls-dyna-double i=inp.k``) and the run can be repeated by hand.
"""

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from kunene.errors import SolverError

import logging
logger = logging.getLogger(__name__)


def as_argv( cmd ):
    """The argument list of command ``cmd`` (a str or a list/tuple)."""
    if isinstance( cmd, (list, tuple) ):
        return [ str(c) for c in cmd ]
    if isinstance( cmd, os.PathLike ):
        return [ os.fspath(cmd) ]
    if not isinstance( cmd, str ) or not cmd.strip():
        raise SolverError( f'invalid command {cmd!r}: expected a non-empty str or list' )

    cmd = cmd.strip()
    # a whole string naming a program is one executable, spaces and all
    if shutil.which( cmd ) or Path( cmd ).is_file():
        return [ cmd ]
    if os.name == 'nt':
        return [ _unquote(t) for t in shlex.split( cmd, posix=False ) ]
    return shlex.split( cmd )


def _unquote( token ):
    if len(token) >= 2 and token[0] == token[-1] and token[0] in '"\'':
        return token[1:-1]
    return token


def as_command_line( argv ):
    """``argv`` as one line that the platform's shell runs as the same command."""
    if os.name == 'nt':
        return subprocess.list2cmdline( argv )
    return shlex.join( argv )


def run( cmd, *args, cmd_file=None, **kwargs ):
    """Run ``cmd`` followed by ``args`` without a shell.

    Keyword arguments go to :func:`subprocess.run`. Returns the
    ``CompletedProcess``; a non-zero return code is left to the caller. A
    program that cannot be found or started raises :class:`SolverError`.

    ``cmd_file`` (str or Path), when given, is a text file the command line
    is appended to, one line per command, before the program is started --
    so it is there to debug with even when the program is not found.
    """
    argv = as_argv( cmd ) + [ os.fspath(a) if isinstance(a, os.PathLike) else str(a) for a in args ]
    logger.debug( f'running {argv}' )
    if cmd_file is not None:
        with open( cmd_file, 'a' ) as f:
            f.write( as_command_line( argv ) + '\n' )
    try:
        return subprocess.run( argv, **kwargs )
    except FileNotFoundError as e:
        raise SolverError( f"command not found: '{argv[0]}' - is it on PATH? (command {argv})" ) from e
    except PermissionError as e:
        raise SolverError( f"command not executable: '{argv[0]}' (command {argv})" ) from e
