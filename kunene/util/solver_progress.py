"""
Parsing of solver text output for percent-complete progress reporting.

Each solver periodically prints the current simulation time to its
(redirected) stdout, and the termination time is known from the input
deck. ``current_time / termination_time`` is an honest completion
fraction for explicit solvers, whose time step is roughly constant.

These functions take *text* (the caller reads the file, typically only
its tail) and return a float or None -- never raise on malformed input,
since progress parsing must not break a solver run. Formats were
calibrated against real output in this repository:

- OpenRadioss engine stdout:  `` NC=  567500 T= 1.9975E+00 DT= ...``
- OpenRadioss engine deck:    ``/RUN/name/1/`` with the stop time on the
  following line
- LS-DYNA deck:               ``*CONTROL_TERMINATION`` with ENDTIM on the
  next non-comment line
- OpenFOAM log:               ``Time = 0.005s``; ``endTime  0.5;`` in
  ``system/controlDict``
"""

import re

_FLOAT = r'[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?'

_RADIOSS_NC_T = re.compile( r'\bNC=\s*\d+\s+T=\s*(' + _FLOAT + r')' )
# native LS-DYNA stdout patterns: '  1000 t 9.9993E-04 dt 1.00E-06 ...'
# and 'write d3plot file at time 1.990E-02'
_DYNA_T_DT = re.compile( r'\bt\s+(' + _FLOAT + r')\s+dt\b' )
_DYNA_AT_TIME = re.compile( r'\bat time\s+(' + _FLOAT + r')' )
_OPENFOAM_TIME = re.compile( r'^Time\s*=\s*(' + _FLOAT + r')s?\s*$', re.MULTILINE )
_OPENFOAM_END = re.compile( r'\bendTime\s+(' + _FLOAT + r')\s*;' )
_OPENFOAM_START = re.compile( r'\bstartTime\s+(' + _FLOAT + r')\s*;' )


def radioss_run_time( text ):
    """Latest simulation time in OpenRadioss engine output (NC=/T= lines)."""
    matches = _RADIOSS_NC_T.findall( text )
    return float( matches[-1] ) if matches else None


def radioss_termination_time( text ):
    """Stop time from an OpenRadioss engine deck: the first number line
    after ``/RUN``."""
    lines = text.splitlines()
    for i, line in enumerate( lines ):
        if line.lstrip().startswith( '/RUN' ):
            for nxt in lines[i + 1:]:
                nxt = nxt.strip()
                if not nxt or nxt.startswith( '#' ):
                    continue
                try:
                    return float( nxt.split()[0] )
                except ValueError:
                    break   # malformed card: try a later /RUN, if any
    return None


def dyna_run_time( text ):
    """Latest simulation time in LS-DYNA output. Handles both native
    LS-DYNA stdout and LS-DYNA decks run through OpenRadioss (which print
    NC=/T= lines). Times are monotonic, so the maximum found is the
    latest."""
    candidates = ( _RADIOSS_NC_T.findall( text )
                   + _DYNA_T_DT.findall( text )
                   + _DYNA_AT_TIME.findall( text ) )
    return max( ( float( c ) for c in candidates ), default=None )


def dyna_termination_time( text ):
    """ENDTIM from a *CONTROL_TERMINATION card of an LS-DYNA deck: the
    first field of the first non-comment line after the keyword."""
    lines = text.splitlines()
    for i, line in enumerate( lines ):
        if line.upper().startswith( '*CONTROL_TERMINATION' ):
            for nxt in lines[i + 1:]:
                if not nxt.strip() or nxt.lstrip().startswith( '$' ):
                    continue
                try:
                    return float( nxt.replace( ',', ' ' ).split()[0] )
                except ValueError:
                    return None
    return None


def openfoam_run_time( text ):
    """Latest simulation time in an OpenFOAM solver log ('Time = 0.005s')."""
    matches = _OPENFOAM_TIME.findall( text )
    return float( matches[-1] ) if matches else None


def openfoam_end_time( text ):
    """endTime from a system/controlDict. A 'stopAt endTime;' entry does
    not match (no number follows)."""
    m = _OPENFOAM_END.search( text )
    return float( m.group( 1 ) ) if m else None


def openfoam_start_time( text ):
    """startTime from a system/controlDict, or None (e.g. 'latestTime')."""
    m = _OPENFOAM_START.search( text )
    return float( m.group( 1 ) ) if m else None
