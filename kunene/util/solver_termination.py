"""
Did a solver run finish? Read from its log, not from its exit code.

A solver can fail without a non-zero exit code: it writes the reason to its
log and exits 0. The rule here is that **a run succeeded only if its log
says so** -- a missing success message is a failure even with exit code 0,
and the reason, when the log gives one, is what the error reports.

A :class:`TerminationRule` says how one solver reports finishing (which
logs, which success and failure lines); :func:`check_termination` applies
it to a run directory and returns a :class:`Termination`;
:func:`failure_message` turns a failed one into the text of an error.
``WorkAction._check_termination`` is the hook a solver action calls with
these (see ``kunene/actions.py``).

Same contract as :mod:`kunene.util.solver_progress`, whose time parsers a
rule reuses: these functions take files or text and never raise on missing
or malformed input. Logs reach several MB, so only their end is read (and
their start, when the rule needs something written near the top).
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

NORMAL = 'normal'
ERROR = 'error'
INCOMPLETE = 'incomplete'
NO_LOG = 'no_log'

TAIL_LINES = 20
HEAD_BYTES = 64 * 1024
# lines between a failure line and its first context line that are skipped,
# e.g. Abstrao's '/!\ Error context :' header
CONTEXT_GAP = 3


@dataclass( frozen=True )
class TerminationRule:
    """
    How one solver says it finished.

    Arguments:
        logs : files to read, relative to the run directory, in order of
            preference; globs allowed. All existing ones are searched for
            the markers; the first existing one supplies the tail and the
            times when none of them has a marker.
        success : regexes; a match means normal termination.
        failure : regexes; a match means error termination, and wins over a
            success match (a restart may append to a log that already
            reports success). A named group ``reason`` becomes the reason.
        context : regex for the detail lines that follow a failure line
            (consecutive matching lines; up to ``CONTEXT_GAP`` other lines
            may come first). The text after the match is the detail.
        warnings : regexes for lines worth a warning even on success, e.g. a
            licence about to expire. Searched in the start of the log too.
        banner : regex for lines to leave out of the tail.
        run_time : a :mod:`~kunene.util.solver_progress` parser: the last
            simulated time in the log text.
        termination_time : a parser for the time the run should reach, from
            the log text (start and end of the log).
        encoding : logs may start with a BOM; CRLF line ends are fine.
        tail_bytes : how much of the end of each log is read.
    """
    logs: tuple
    success: tuple
    failure: tuple = ()
    context: str = None
    warnings: tuple = ()
    banner: str = None
    run_time: Callable = None
    termination_time: Callable = None
    encoding: str = 'utf-8-sig'
    tail_bytes: int = 256 * 1024


@dataclass( frozen=True )
class Termination:
    """
    How a run ended, as its logs tell it.

    Attributes:
        state : ``NORMAL``, ``ERROR``, ``INCOMPLETE`` (no marker at all) or
            ``NO_LOG`` (none of the rule's logs exists).
        log : the file the verdict came from, relative to the run directory.
        reason : the failure's reason, e.g. ``'License not found'``.
        details : the context lines after the failure line.
        warnings : lines matched by the rule's warnings.
        last_time : the last simulated time, if the log has one.
        termination_time : the time the run should have reached, if known.
        tail : the last lines of the log, for a message without a reason.
        searched : the rule's log names, for a message when there is no log.
    """
    state: str
    log: str = None
    reason: str = None
    details: tuple = ()
    warnings: tuple = ()
    last_time: float = None
    termination_time: float = None
    tail: tuple = ()
    searched: tuple = ()

    @property
    def normal( self ):
        return self.state == NORMAL


def _existing_logs( rule, run_dir ):
    """The rule's logs present in ``run_dir``, in rule order, globs sorted."""
    found = []
    for pattern in rule.logs:
        matches = sorted( Path( run_dir ).glob( pattern ) )
        for path in matches:
            if path.is_file() and path not in found:
                found.append( path )
    return found


def _decode( data, encoding ):
    return data.decode( encoding, errors='replace' ).replace( '\r', '' )


def _read_log( path, rule, need_head ):
    """
    ``(head, tail)`` text of a log. A file within ``tail_bytes`` is read
    whole and returned as the tail (head empty); otherwise the tail starts
    at the first full line of the last ``tail_bytes``, and the head -- the
    first ``HEAD_BYTES`` -- is read only when ``need_head``.
    """
    try:
        size = path.stat().st_size
        with open( path, 'rb' ) as f:
            if size <= rule.tail_bytes:
                return '', _decode( f.read(), rule.encoding )
            head = _decode( f.read( HEAD_BYTES ), rule.encoding ) if need_head else ''
            f.seek( size - rule.tail_bytes )
            tail = f.read()
    except OSError:
        return '', ''
    tail = tail[ tail.find( b'\n' ) + 1: ]      # drop the partial first line
    return head, _decode( tail, rule.encoding )


def _last_failure( rule, text ):
    """The last failure match in ``text`` (over all failure regexes), or None."""
    last = None
    for pattern in rule.failure:
        for m in re.finditer( pattern, text, re.MULTILINE ):
            if last is None or m.start() > last.start():
                last = m
    return last


def _context_lines( rule, text, match ):
    """The detail lines after a failure match, prefix removed."""
    if not rule.context:
        return ()
    context = re.compile( rule.context )
    following = text[ match.end(): ].split( '\n' )[ 1: ]
    details, skipped = [], 0
    for line in following:
        m = context.search( line )
        if m:
            details.append( line[ m.end(): ].strip() or line.strip() )
            continue
        if details:
            break               # end of the consecutive run
        skipped += 1
        if skipped > CONTEXT_GAP:
            break
    return tuple( details )


def _warning_lines( rule, text ):
    lines = []
    for pattern in rule.warnings:
        for m in re.finditer( pattern, text, re.MULTILINE ):
            start = text.rfind( '\n', 0, m.start() ) + 1
            end = text.find( '\n', m.end() )
            line = text[ start: end if end >= 0 else len( text ) ].strip()
            if line and line not in lines:
                lines.append( line )
    return tuple( lines )


def _tail_lines( rule, text ):
    banner = re.compile( rule.banner ) if rule.banner else None
    lines = [ l for l in text.split( '\n' )
              if l.strip() and not ( banner and banner.search( l ) ) ]
    return tuple( l.strip() for l in lines[ -TAIL_LINES: ] )


def _parse( parser, text ):
    if parser is None or not text:
        return None
    try:
        return parser( text )
    except Exception:
        return None


def check_termination( rule, run_dir='.' ):
    """
    Read the logs ``rule`` names in ``run_dir`` and say how the run ended.

    A failure line in any log makes the run ``ERROR`` (the last one, in the
    first log that has one); otherwise a success line in any log makes it
    ``NORMAL``; with neither it is ``INCOMPLETE``; without any log
    ``NO_LOG``. Never raises.

    Arguments:
        rule (TerminationRule) : how the solver reports finishing.
        run_dir (str|Path) : the directory the solver ran in.
    Returns:
        Termination
    """
    logs = _existing_logs( rule, run_dir )
    if not logs:
        return Termination( NO_LOG, searched=tuple( rule.logs ) )

    need_head = bool( rule.warnings ) or rule.termination_time is not None
    texts = [ ( path, *_read_log( path, rule, need_head ) ) for path in logs ]

    state, verdict, failure = INCOMPLETE, None, None
    for path, head, tail in texts:
        m = _last_failure( rule, tail )
        if m:
            state, verdict, failure = ERROR, ( path, head, tail ), m
            break
    if verdict is None:
        for path, head, tail in texts:
            if any( re.search( p, tail, re.MULTILINE ) for p in rule.success ):
                state, verdict = NORMAL, ( path, head, tail )
                break
    if verdict is None:
        verdict = texts[0]
    path, head, tail = verdict

    reason, details = None, ()
    if failure is not None:
        reason = failure.groupdict().get( 'reason' )
        if reason is None:
            line_end = tail.find( '\n', failure.end() )
            line_start = tail.rfind( '\n', 0, failure.start() ) + 1
            reason = tail[ line_start: line_end if line_end >= 0 else len( tail ) ]
        reason = reason.strip() or None
        details = _context_lines( rule, tail, failure )

    warnings = []
    for _, h, t in texts:
        for line in _warning_lines( rule, h + '\n' + t ):
            if line not in warnings:
                warnings.append( line )

    try:
        log_name = str( path.relative_to( run_dir ) )
    except ValueError:
        log_name = str( path )

    return Termination( state,
                        log=log_name,
                        reason=reason,
                        details=details,
                        warnings=tuple( warnings ),
                        last_time=_parse( rule.run_time, tail ),
                        termination_time=_parse( rule.termination_time, head + '\n' + tail ),
                        tail=_tail_lines( rule, tail ),
                        searched=tuple( rule.logs ) )


def describe_returncode( returncode, cmd=None ):
    """
    Human-readable interpretation of a subprocess return code.

    A process killed by a signal is reported as a negative code by
    subprocess, or as 128+signum by a wrapper shell script.
    """
    signum = None
    if returncode < 0:
        signum = -returncode
    elif returncode > 128:
        signum = returncode - 128

    if signum is not None:
        try:
            import signal
            name = signal.Signals( signum ).name
        except ( ValueError, ImportError ):
            name = f'signal {signum}'
        return f'terminated by {name} (exit code {returncode})'

    if returncode == 127:
        on_path = f" - is '{cmd}' on PATH?" if cmd else ''
        return f'command not found (exit code 127){on_path}'
    if returncode == 126:
        return 'command found but not executable (exit code 126)'
    return f'exit code {returncode}'


def _stopped_at( termination ):
    t, t_end = termination.last_time, termination.termination_time
    if t is None:
        return None
    if t_end:
        return f'Stopped at t = {t:.3e} of {t_end:.3e} ({100. * t / t_end:.1f}%).'
    return f'Stopped at t = {t:.3e}.'


def failure_message( termination, solver, run_dir, returncode=None ):
    """
    The text of the error for a run that did not end normally: one readable
    line with the reason and the log, then the time reached, the exit code,
    and -- only when the log gives no reason -- its last lines.

    Arguments:
        termination (Termination|None) : None when no rule was checked.
        solver (str) : the solver's name, e.g. ``'Abstrao'``.
        run_dir (str|Path) : the directory it ran in.
        returncode (int|None) : the solver's exit code.
    Returns:
        str
    """
    failed_exit = returncode not in ( None, 0 )
    exit_text = describe_returncode( returncode ) if failed_exit else None
    head = f'{solver} run in {run_dir} failed'

    if termination is None:
        return f'{head}: {exit_text}' if exit_text else head

    state = termination.state
    if state == ERROR:
        cause = termination.reason or 'error termination'
        if termination.details:
            cause += ' -- ' + '; '.join( termination.details )
    elif state == NORMAL:
        cause = f'the log reports a normal termination, but the solver ended with {exit_text}'
        exit_text = None
    elif state == NO_LOG:
        cause = f"the solver wrote no log ({', '.join( termination.searched )})"
    else:
        cause = 'the log does not report a normal termination'

    first = f'{head}: {cause}'
    if termination.log:
        first += f' (log: {termination.log})'
    lines = [ first ]
    if state != NORMAL:
        stopped = _stopped_at( termination )
        if stopped:
            lines.append( stopped )
    if exit_text:
        lines.append( f'The solver ended with {exit_text}.' )
    if state in ( ERROR, INCOMPLETE ) and not termination.reason and not termination.details \
       and termination.tail:
        lines.append( f'Last lines of {termination.log}:' )
        lines.extend( f'    {l}' for l in termination.tail )
    return '\n'.join( lines )
