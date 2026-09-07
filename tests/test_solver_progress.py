import os

import json
import tempfile
import time
from pathlib import Path

from kunene.actions import WorkAction
from kunene.args import STATUS_PATH
from kunene.graph_actions import WorkFlow
from kunene.progress import StatusReporter, FileProgressTail
from kunene.util import solver_progress as sp


# samples copied from real output in this repository
RADIOSS_STDOUT = """\
 NC=       0 T= 0.0000E+00 DT= 3.5309E-06 ERR=  0.0% DM/M= 0.0000E+00
     H3D FILE: rad_run_file.h3d UPDATED:  FRAME=     1 , NC=       0 , TIME=   0.000
 NC=     500 T= 1.7655E-03 DT= 3.5309E-06 ERR= -0.0% DM/M= 0.0000E+00
 NC=  567500 T= 1.9975E+00 DT= 3.5186E-06 ERR= -4.3% DM/M= 0.0000E+00
"""

RADIOSS_ENGINE_DECK = """\
/RUN/cube_TYPE7/1/
2.00000000000000
/H3D/NODA/CONT
"""

DYNA_DECK_COMMENTED = """\
*CONTROL_TERMINATION
$   ENDTIM
10.000E+00
$---+----1----+----2
"""

DYNA_DECK_FIXED = """\
*CONTROL_TERMINATION
$$  ENDTIM    ENDCYC     DTMIN    ENDENG    ENDMAS     NOSOL
      40.0
*CONTROL_TIMESTEP
"""

DYNA_NATIVE_STDOUT = """\
     1000 t 9.9993E-04 dt 1.00E-06 flush i/o buffers
 write d3plot file            at time  1.9900E-03
     3000 t 2.9993E-03 dt 1.00E-06
"""

OPENFOAM_LOG = """\
Courant Number mean: 0.1 max: 0.2
Time = 0.005s

smoothSolver:  Solving for Ux
Time = 0.015s
"""

OPENFOAM_CONTROLDICT = """\
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         0.5;
"""


def test_radioss_parsers():
    assert sp.radioss_run_time(RADIOSS_STDOUT) == 1.9975
    assert sp.radioss_run_time('no progress here') is None
    assert sp.radioss_termination_time(RADIOSS_ENGINE_DECK) == 2.0
    assert sp.radioss_termination_time('/H3D/NODA/CONT\n') is None


def test_dyna_parsers():
    assert sp.dyna_termination_time(DYNA_DECK_COMMENTED) == 10.0
    assert sp.dyna_termination_time(DYNA_DECK_FIXED) == 40.0
    assert sp.dyna_termination_time('*NODE\n1, 0., 0., 0.\n') is None
    # native stdout: latest of 't ... dt' and 'at time' lines
    assert sp.dyna_run_time(DYNA_NATIVE_STDOUT) == 2.9993e-03
    # LS-DYNA deck run through OpenRadioss prints NC=/T= lines
    assert sp.dyna_run_time(RADIOSS_STDOUT) == 1.9975


def test_openfoam_parsers():
    assert sp.openfoam_run_time(OPENFOAM_LOG) == 0.015
    assert sp.openfoam_end_time(OPENFOAM_CONTROLDICT) == 0.5
    assert sp.openfoam_start_time(OPENFOAM_CONTROLDICT) == 0.0
    # 'stopAt endTime;' must not match as the end time
    assert sp.openfoam_end_time('stopAt          endTime;\n') is None
    assert sp.openfoam_start_time('startFrom latestTime;\n') is None


class _in_tmp_dir:
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path.cwd()
        os.chdir(self._tmp.name)
        return Path(self._tmp.name)
    def __exit__(self, *exc):
        os.chdir(self._root)
        self._tmp.cleanup()


def _action_status(name, path=STATUS_PATH):
    return json.loads(Path(path).read_text())['actions'][name]


def test_file_progress_tail_reports_fraction():
    with _in_tmp_dir() as tmp:
        reporter = StatusReporter('G', directory=tmp)
        reporter.start(actions=['solver'])

        log = Path('sim.stdout')
        tail = FileProgressTail(reporter, 'solver', log,
                                sp.radioss_run_time, t_end=2.0, interval=0.05)
        tail.start()
        try:
            log.write_text(' NC=     500 T= 1.0000E+00 DT= 3.5E-06\n')
            time.sleep(0.2)
            entry = _action_status('solver')
            assert abs(entry['fraction'] - 0.5) < 1e-6
            assert entry['state'] == 'running'
            assert 'of 2' in entry['message']

            with open(log, 'a') as f:
                f.write(' NC=    1000 T= 2.0000E+00 DT= 3.5E-06\n')
            time.sleep(0.2)
            assert _action_status('solver')['fraction'] == 1.0
        finally:
            tail.stop()
        reporter.finish('done')


def test_tail_noop_without_reporter_or_t_end():
    with _in_tmp_dir() as tmp:
        reporter = StatusReporter('G', directory=tmp)
        reporter.start(actions=['a'])
        # no termination time -> no thread
        tail = FileProgressTail(reporter, 'a', 'x.log', sp.radioss_run_time, t_end=None)
        tail.start()
        assert tail._thread is None
        tail.stop()
        # no reporter -> no thread
        tail = FileProgressTail(None, 'a', 'x.log', sp.radioss_run_time, t_end=1.0)
        tail.start()
        assert tail._thread is None
        tail.stop()
        reporter.finish('done')


class FakeSolver(WorkAction):
    """Emulates a solver action: writes progress lines to a log file while
    a FileProgressTail reports through the graph-provided reporter."""
    def __init__(self, name):
        super().__init__(name)
        self.seen_fractions = []

    def solve(self, val_dict=None):
        assert self._progress_reporter is not None
        log = Path(f'{self.name}.stdout')
        tail = FileProgressTail(self._progress_reporter, self.name, log,
                                sp.radioss_run_time, t_end=2.0, interval=0.05)
        tail.start()
        try:
            for t in ('0.0000E+00', '1.0000E+00', '2.0000E+00'):
                with open(log, 'a') as f:
                    f.write(f' NC=     500 T= {t} DT= 3.5E-06\n')
                time.sleep(0.15)
                entry = _action_status(self.name, self._progress_reporter.path)
                self.seen_fractions.append(entry['fraction'])
        finally:
            tail.stop()
        return 1.0


def test_graph_provides_reporter_to_actions():
    with _in_tmp_dir():
        solver = FakeSolver('solver')
        wf = WorkFlow('WF', actions=[solver])
        wf.solve({})

        assert solver.seen_fractions[-1] == 1.0
        assert any(f is not None and 0 < f < 1 for f in solver.seen_fractions)
        # final state: done (fraction cleared by the done sweep)
        assert _action_status('solver')['state'] == 'done'


def test_nested_graph_forwards_owner_reporter():
    with _in_tmp_dir():
        solver = FakeSolver('inner_solver')
        inner = WorkFlow('Inner', actions=[solver])
        outer = WorkFlow('Outer', actions=[inner])
        outer.solve({})

        # the solver reported into the owning (outer) graph's status file;
        # the sub-graph itself is pass-through and holds no entry there
        st = json.loads(Path(STATUS_PATH).read_text())
        assert st['name'] == 'Outer'
        assert list(st['actions']) == ['inner_solver']
        assert st['actions']['inner_solver']['state'] == 'done'
        assert any(f is not None for f in solver.seen_fractions)


if __name__ == '__main__':
    test_radioss_parsers()
    test_dyna_parsers()
    test_openfoam_parsers()
    test_file_progress_tail_reports_fraction()
    test_tail_noop_without_reporter_or_t_end()
    test_graph_provides_reporter_to_actions()
    test_nested_graph_forwards_owner_reporter()
    print('all solver progress tests passed')
