"""
Deciding from a solver's log whether its run finished
(kunene/util/solver_termination.py and WorkAction._check_termination).

Synthetic logs only: the markers are shaped like Abstrao's, but the rule is
defined here, so no solver action is involved.
"""
import json
import logging
import pickle
import re
from pathlib import Path

import pytest

from kunene.actions import WorkAction
from kunene.args import Cleanup, STATUS_PATH, JOBS_INDEX_PATH
from kunene.errors import SolverError, SolverTerminationError, KuneneError, ParameterError
from kunene.graph_actions import WorkFlow
from kunene.simulation_iterator import SimulationIterator
from kunene.util.solver_termination import (
    TerminationRule, Termination, check_termination, describe_returncode, failure_message,
    NORMAL, ERROR, INCOMPLETE, NO_LOG, TAIL_LINES )


def _run_time( text ):
    m = re.findall( r'^\s*time = (\S+)', text, re.MULTILINE )
    return float( m[-1] ) if m else None


def _termination_time( text ):
    m = re.search( r'^\s*end time = (\S+)', text, re.MULTILINE )
    return float( m.group( 1 ) ) if m else None


RULE = TerminationRule(
    logs=( 'solver_*_log.txt', ),
    success=( r'^\* Congratulations ! Your run was completed successfully', ),
    failure=( r'^\+ solver stopped due to (?P<reason>.*?) \+\s*$', ),
    context=r'^/!\\ -?-> ',
    warnings=( r'^/!\\ Evaluation period expires', ),
    banner=r'^\t',
    run_time=_run_time,
    termination_time=_termination_time )

HEADER = ( '\t  _____ ____  \n'
           '\t / ___// __ \\ \n'
           '/!\\ Evaluation period expires on 2026/10/31 (in 2 days ).\n'
           '\n'
           ' end time = 1.0e-4\n' )
STEPS = ' time = 1.0e-5\n time = 2.836e-05\n'
SUCCESS = '* Congratulations ! Your run was completed successfully\n'
LICENCE_FAILURE = ( '+ solver stopped due to License not found +\n'
                    '/!\\ Error context :\n'
                    '/!\\ -> In *RUN section\n'
                    '/!\\ --> Evaluation period has expired.\n' )
USER_STOP = ( '+ solver stopped due to User signal +\n'
              '/!\\ Error context :\n'
              '/!\\ -> Simulation stopped due to user signal.\n' )


def _log( tmp_path, text, name='solver_x_log.txt', bom=True, crlf=True ):
    if crlf:
        text = text.replace( '\n', '\r\n' )
    data = ( b'\xef\xbb\xbf' if bom else b'' ) + text.encode( 'utf-8' )
    ( tmp_path / name ).write_bytes( data )
    return tmp_path / name


# ----------------------------------------------------------------------
# check_termination

def test_success_read_through_bom_and_crlf( tmp_path ):
    _log( tmp_path, HEADER + STEPS + SUCCESS )
    t = check_termination( RULE, tmp_path )
    assert t.state == NORMAL and t.normal
    assert t.log == 'solver_x_log.txt'
    assert t.reason is None and t.details == ()


def test_failure_with_reason_and_context( tmp_path ):
    _log( tmp_path, HEADER + LICENCE_FAILURE )
    t = check_termination( RULE, tmp_path )
    assert t.state == ERROR
    assert t.reason == 'License not found'
    assert t.details == ( 'In *RUN section', 'Evaluation period has expired.' )
    assert t.last_time is None          # stopped before its first step
    assert t.termination_time == 1.0e-4


def test_failure_without_reason_group_reports_the_line( tmp_path ):
    rule = TerminationRule( logs=( 'run.log', ), success=( 'DONE', ), failure=( r'FATAL', ) )
    ( tmp_path / 'run.log' ).write_text( 'step 1\n*** FATAL: out of memory\n' )
    t = check_termination( rule, tmp_path )
    assert t.state == ERROR and t.reason == '*** FATAL: out of memory'
    assert t.details == ()


def test_incomplete_and_no_log( tmp_path ):
    _log( tmp_path, HEADER + STEPS )
    t = check_termination( RULE, tmp_path )
    assert t.state == INCOMPLETE
    assert t.last_time == 2.836e-05 and t.termination_time == 1.0e-4

    empty = tmp_path / 'empty'
    empty.mkdir()
    t = check_termination( RULE, empty )
    assert t.state == NO_LOG and t.log is None
    assert t.searched == RULE.logs


def test_failure_after_success_is_an_error( tmp_path ):
    # a restart appended to a log that already reported success
    _log( tmp_path, HEADER + SUCCESS + STEPS + USER_STOP )
    t = check_termination( RULE, tmp_path )
    assert t.state == ERROR and t.reason == 'User signal'
    assert t.details == ( 'Simulation stopped due to user signal.', )


def test_last_failure_is_reported( tmp_path ):
    _log( tmp_path, HEADER + LICENCE_FAILURE + STEPS + USER_STOP )
    assert check_termination( RULE, tmp_path ).reason == 'User signal'


def test_failure_in_a_later_log_wins_over_success_in_the_first( tmp_path ):
    rule = TerminationRule( logs=( 'a.log', 'b.log' ), success=( '^OK$', ), failure=( '^ERROR (?P<reason>.*)$', ) )
    ( tmp_path / 'a.log' ).write_text( 'OK\n' )
    ( tmp_path / 'b.log' ).write_text( 'ERROR disk full\n' )
    t = check_termination( rule, tmp_path )
    assert t.state == ERROR and t.log == 'b.log' and t.reason == 'disk full'


def test_tail_drops_banner_and_blank_lines( tmp_path ):
    lines = ''.join( f' line {i}\n\n' for i in range( 30 ) )
    _log( tmp_path, HEADER + lines )
    t = check_termination( RULE, tmp_path )
    assert len( t.tail ) == TAIL_LINES
    assert t.tail[-1] == 'line 29'
    assert all( l and not l.startswith( '_' ) for l in t.tail )

    _log( tmp_path, HEADER )
    t = check_termination( RULE, tmp_path )
    assert not any( '____' in l for l in t.tail )


def test_large_log_is_read_from_its_end( tmp_path ):
    # a success marker at the very start lies beyond tail_bytes, so it is
    # not seen; the time and the failure in the last KB are
    filler = ' ' + 'x' * 99 + '\n'
    text = SUCCESS + HEADER + filler * 60000 + ' time = 7.5e-05\n' + USER_STOP
    path = _log( tmp_path, text )
    assert path.stat().st_size > 6_000_000
    t = check_termination( RULE, tmp_path )
    assert t.state == ERROR and t.reason == 'User signal'
    assert t.last_time == 7.5e-05
    assert t.termination_time == 1.0e-4     # from the head
    assert t.warnings == ( '/!\\ Evaluation period expires on 2026/10/31 (in 2 days ).', )

    _log( tmp_path, SUCCESS + filler * 60000 )
    assert check_termination( RULE, tmp_path ).state == INCOMPLETE


def test_malformed_log_does_not_raise( tmp_path ):
    ( tmp_path / 'solver_x_log.txt' ).write_bytes( b'\xff\xfe\x00garbage\x80\x81\n' )
    assert check_termination( RULE, tmp_path ).state == INCOMPLETE

    def broken( text ):
        raise ValueError( 'parser bug' )
    rule = TerminationRule( logs=RULE.logs, success=RULE.success, run_time=broken )
    assert check_termination( rule, tmp_path ).last_time is None


# ----------------------------------------------------------------------
# failure_message and describe_returncode

def test_message_carries_reason_details_and_time( tmp_path ):
    _log( tmp_path, HEADER + STEPS + LICENCE_FAILURE )
    msg = failure_message( check_termination( RULE, tmp_path ), 'Abstrao', tmp_path )
    first, second = msg.splitlines()[ :2 ]
    assert first == ( f'Abstrao run in {tmp_path} failed: License not found -- In *RUN section; '
                      'Evaluation period has expired. (log: solver_x_log.txt)' )
    assert second == 'Stopped at t = 2.836e-05 of 1.000e-04 (28.4%).'
    assert 'Last lines' not in msg


def test_message_for_incomplete_run_shows_the_tail( tmp_path ):
    _log( tmp_path, HEADER + STEPS + ' writing restart file\n' )
    msg = failure_message( check_termination( RULE, tmp_path ), 'Abstrao', tmp_path )
    assert 'does not report a normal termination' in msg
    assert 'Last lines of solver_x_log.txt:' in msg
    assert '    writing restart file' in msg


def test_describe_returncode():
    assert 'SIGKILL' in describe_returncode( -9 )
    assert 'SIGKILL' in describe_returncode( 137 )
    assert describe_returncode( 127, cmd='abstrao' ) == \
        "command not found (exit code 127) - is 'abstrao' on PATH?"
    assert describe_returncode( 127 ) == 'command not found (exit code 127)'
    assert 'not executable' in describe_returncode( 126 )
    assert describe_returncode( 3 ) == 'exit code 3'


# ----------------------------------------------------------------------
# WorkAction._check_termination

class LogSolver( WorkAction ):
    """Writes a log (and a bulky plot file), then checks its termination
    as a solver action would after running the solver."""

    def __init__( self, name, log_text=None, returncode=0, use_rule=True, on_incomplete=None ):
        super().__init__( name )
        self.log_text = log_text
        self.returncode = returncode
        self.use_rule = use_rule
        if on_incomplete is not None:
            self.on_incomplete = on_incomplete

    def solve( self, val_dict=None ):
        Path( 'plot01' ).write_text( 'x' * 100 )
        if self.log_text is not None:
            Path( 'solver_x_log.txt' ).write_text( self.log_text )
        self._check_termination( self.returncode, solver='Fake' )
        return True

    def _termination_rule( self ):
        return RULE if self.use_rule else None

    def _produced_files( self ):
        return [ 'solver_x_log.txt', 'plot*' ]

    def _disposable_files( self ):
        return [ 'plot*' ]


def _check( tmp_path, monkeypatch, **kwargs ):
    monkeypatch.chdir( tmp_path )
    return LogSolver( 'run', **kwargs ).solve()


@pytest.mark.parametrize( 'returncode, log_text, fails, expect', [
    ( 0, HEADER + SUCCESS, False, None ),
    ( 0, HEADER + LICENCE_FAILURE, True, 'License not found' ),
    ( 0, HEADER + STEPS, True, 'does not report a normal termination' ),
    ( 0, None, True, 'the solver wrote no log (solver_*_log.txt)' ),
    ( 1, HEADER + SUCCESS, True, 'reports a normal termination, but the solver ended with exit code 1' ),
    ( -9, HEADER + STEPS, True, 'SIGKILL' ),
    ( 2, HEADER + LICENCE_FAILURE, True, 'License not found' ),
] )
def test_decision_table( tmp_path, monkeypatch, returncode, log_text, fails, expect ):
    if not fails:
        assert _check( tmp_path, monkeypatch, log_text=log_text, returncode=returncode )
        return
    with pytest.raises( SolverTerminationError ) as info:
        _check( tmp_path, monkeypatch, log_text=log_text, returncode=returncode )
    assert expect in str( info.value )
    assert info.value.returncode == returncode
    assert isinstance( info.value.termination, Termination )
    if returncode not in ( 0, 1 ):
        assert describe_returncode( returncode ) in str( info.value )


def test_without_a_rule_only_the_exit_code_decides( tmp_path, monkeypatch ):
    assert _check( tmp_path, monkeypatch, use_rule=False, log_text=HEADER + STEPS )
    with pytest.raises( SolverTerminationError, match='exit code 4' ) as info:
        _check( tmp_path, monkeypatch, use_rule=False, returncode=4 )
    assert info.value.termination is None


def test_on_incomplete_warn_logs_and_returns( tmp_path, monkeypatch, caplog ):
    with caplog.at_level( logging.ERROR, logger='kunene.actions' ):
        assert _check( tmp_path, monkeypatch, log_text=HEADER + STEPS, on_incomplete='warn' )
    assert 'does not report a normal termination' in caplog.text


def test_on_incomplete_must_be_a_known_choice( tmp_path, monkeypatch ):
    with pytest.raises( ParameterError, match='on_incomplete' ):
        _check( tmp_path, monkeypatch, log_text=HEADER + SUCCESS, on_incomplete='ignore' )


def test_warning_lines_are_logged_on_success( tmp_path, monkeypatch, caplog ):
    with caplog.at_level( logging.WARNING, logger='kunene.actions' ):
        _check( tmp_path, monkeypatch, log_text=HEADER + SUCCESS )
    assert 'Evaluation period expires on 2026/10/31' in caplog.text


def test_error_is_a_solver_error_and_pickles( tmp_path, monkeypatch ):
    with pytest.raises( SolverError ) as info:
        _check( tmp_path, monkeypatch, log_text=HEADER + LICENCE_FAILURE, returncode=0 )
    err = info.value
    assert isinstance( err, SolverTerminationError ) and isinstance( err, KuneneError )
    back = pickle.loads( pickle.dumps( err ) )
    assert str( back ) == str( err )
    assert back.returncode == 0
    assert back.termination == err.termination
    assert back.termination.reason == 'License not found'


# ----------------------------------------------------------------------
# What raising buys: no cleanup, a failed job, the reason in status.json

def test_failed_termination_skips_cleanup_and_reports_the_reason( tmp_path ):
    wf = WorkFlow( 'Study' )
    wf.add_action( LogSolver( 'run', log_text=HEADER + LICENCE_FAILURE ) )
    it = SimulationIterator( wf, work_area_path=str( tmp_path / 'study' ), cleanup=Cleanup() )
    with pytest.raises( SolverTerminationError ):
        it.solve( { 'K': 1.0 } )

    job = tmp_path / 'study' / 'job_0'
    assert ( job / 'plot01' ).exists()          # bulk output kept for debugging
    status = json.loads( ( job / STATUS_PATH ).read_text() )
    assert status['actions']['run']['state'] == 'failed'
    assert 'License not found' in status['actions']['run']['message']
    index = json.loads( ( tmp_path / 'study' / JOBS_INDEX_PATH ).read_text() )
    assert [ r['state'] for r in index['jobs'] ] == [ 'failed' ]


def test_normal_termination_is_cleaned( tmp_path ):
    wf = WorkFlow( 'Study' )
    wf.add_action( LogSolver( 'run', log_text=HEADER + SUCCESS ) )
    it = SimulationIterator( wf, work_area_path=str( tmp_path / 'study' ), cleanup=Cleanup() )
    it.solve( { 'K': 1.0 } )
    assert not ( tmp_path / 'study' / 'job_0' / 'plot01' ).exists()
