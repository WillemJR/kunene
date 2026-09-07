"""Running several jobs of one SimulationIterator at the same time."""

import json
import time

import pytest

from kunene.actions import MathEvaluation, WorkAction
from kunene.args import JOB_LOG_PATH, JOBS_INDEX_PATH, STATUS_PATH
from kunene.errors import AsyncActionError, ParameterError
from kunene.graph_actions import WorkFlow
from kunene.simulation_iterator import SimulationIterator


class Sleeper(WorkAction):
    """Takes long enough that jobs overlapping is measurable."""
    DELAY = 0.4

    def solve(self, val_dict=None):
        time.sleep(self.DELAY)
        return val_dict.get('x', 0) if val_dict else 0


class Chatty(WorkAction):
    """Writes to stdout the way the solver wrappers do."""
    def solve(self, val_dict=None):
        print('chatter from the job')
        return 1


class FailsOnThree(WorkAction):
    def solve(self, val_dict=None):
        if val_dict and val_dict.get('x') == 3:
            raise RuntimeError('boom')
        return val_dict.get('x', 0) if val_dict else 0


def _iterator(name, max_workers=1, **kwargs):
    wf = WorkFlow(name, actions=[MathEvaluation('calc', 'x * 10')])
    return SimulationIterator(wf, max_workers=max_workers, **kwargs)


def test_max_workers_must_be_at_least_one():
    with pytest.raises(ParameterError):
        _iterator('Bad', max_workers=0)


def test_parallel_sweep_matches_serial(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    _, serial = _iterator('Serial').collect_for_varrange({'x': [1, 2, 3, 4, 5]})
    _, parallel = _iterator('Parallel', max_workers=3).collect_for_varrange(
        {'x': [1, 2, 3, 4, 5]})

    # same results, in the order the design points were given
    assert serial['calc'] == [10, 20, 30, 40, 50]
    assert parallel['calc'] == serial['calc']
    assert parallel['x'] == serial['x']

    # one job directory per design point, each with its own results
    jobs = sorted((tmp_path / 'Parallel').glob('job_*'))
    assert [j.name for j in jobs] == [f'job_{i}' for i in range(5)]
    for i, job in enumerate(jobs):
        assert json.loads((job / 'iter_variables.json').read_text()) == {'x': i + 1}

    index = json.loads((tmp_path / 'Parallel' / JOBS_INDEX_PATH).read_text())
    assert [r['state'] for r in index['jobs']] == ['done'] * 5


def test_parallel_sweep_runs_jobs_at_the_same_time(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    wf = WorkFlow('Slow', actions=[Sleeper('slow')])
    itr = SimulationIterator(wf, max_workers=4)

    start = time.time()
    itr.collect_for_varrange({'x': [1, 2, 3, 4]})
    elapsed = time.time() - start

    # serially this is 4 * DELAY; four at once is one DELAY plus overhead
    assert elapsed < 3 * Sleeper.DELAY
    assert len(list((tmp_path / 'Slow').glob('job_*'))) == 4


def test_parallel_reports_running_jobs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    wf = WorkFlow('Reported', actions=[Sleeper('slow')])
    itr = SimulationIterator(wf, max_workers=2)
    itr.collect_for_varrange({'x': [1, 2]})

    root = json.loads((tmp_path / 'Reported' / STATUS_PATH).read_text())
    assert root['state'] == 'done'
    assert root['jobs_done'] == 2
    assert root['jobs_total'] == 2
    assert root['current_jobs'] == []          # none left running
    assert root['current_job'] == 'job_1'      # last one started

    for job in ('job_0', 'job_1'):
        st = json.loads((tmp_path / 'Reported' / job / STATUS_PATH).read_text())
        assert st['state'] == 'done'
        assert st['actions']['slow']['state'] == 'done'


def test_failing_job_aborts_the_sweep(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    wf = WorkFlow('Failing', actions=[FailsOnThree('maybe')])
    itr = SimulationIterator(wf, max_workers=2)

    with pytest.raises(AsyncActionError) as excinfo:
        itr.collect_for_varrange({'x': [1, 2, 3, 4]})

    assert 'boom' in str(excinfo.value)         # the child's traceback
    assert 'RuntimeError' in str(excinfo.value)

    index = json.loads((tmp_path / 'Failing' / JOBS_INDEX_PATH).read_text())
    states = {r['job']: r['state'] for r in index['jobs']}
    assert states['job_2'] == 'failed'          # x == 3
    assert 'running' not in states.values()     # nothing left dangling

    root = json.loads((tmp_path / 'Failing' / STATUS_PATH).read_text())
    assert root['state'] == 'failed'


def test_solve_parallel_reports_its_own_batch(tmp_path, monkeypatch):
    """A batch run directly, not through collect_for_*, still says how many
    jobs it has and that it finished."""
    monkeypatch.chdir(tmp_path)

    itr = _iterator('Direct', max_workers=2)
    itr.solve_parallel([{'x': 1}, {'x': 2}, {'x': 3}], progress_bar=False)

    root = json.loads((tmp_path / 'Direct' / STATUS_PATH).read_text())
    assert root['jobs_total'] == 3
    assert root['jobs_done'] == 3
    assert root['state'] == 'done'
    assert root['current_jobs'] == []


def test_failed_batch_stops_claiming_jobs_are_running(tmp_path, monkeypatch):
    """The jobs terminated because another one failed cannot report
    themselves -- their processes are gone -- so the parent does it."""
    monkeypatch.chdir(tmp_path)

    wf = WorkFlow('Cut', actions=[FailsOnThree('maybe'), Sleeper('slow')])
    itr = SimulationIterator(wf, max_workers=3)

    with pytest.raises(AsyncActionError):
        itr.solve_parallel([{'x': 1}, {'x': 3}, {'x': 2}], progress_bar=False)

    root = json.loads((tmp_path / 'Cut' / STATUS_PATH).read_text())
    assert root['state'] == 'failed'
    assert root['current_jobs'] == []      # nothing is running any more

    for job in sorted((tmp_path / 'Cut').glob('job_*')):
        st = json.loads((job / STATUS_PATH).read_text())
        states = {k: v['state'] for k, v in st['actions'].items()}
        assert st['state'] == 'failed', (job.name, st)
        assert 'running' not in states.values(), (job.name, states)


def test_progress_bar_reports_the_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    itr = _iterator('Bar', max_workers=2)
    itr.solve_parallel([{'x': 1}, {'x': 2}, {'x': 3}], progress_bar=True)

    err = capsys.readouterr().err
    assert 'Bar_Iter' in err        # the batch bar is labelled with the iterator
    assert '3/3' in err             # and ends on the last job
    assert '  job_0' in err         # each running job gets a bar of its own
    assert '  job_2' in err


def test_progress_bar_can_be_switched_off(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    itr = _iterator('Quiet', max_workers=2)
    itr.solve_parallel([{'x': 1}, {'x': 2}], progress_bar=False)
    assert capsys.readouterr().err == ''

    # the default is quiet too when stderr is not a terminal, as here
    itr.solve_parallel([{'x': 3}])
    assert capsys.readouterr().err == ''


def test_collect_passes_the_progress_bar_flag(tmp_path, monkeypatch, capsys):
    """progress_bar=True reaches solve_parallel from collect_for_varrange,
    even though stderr is not a terminal under pytest."""
    monkeypatch.chdir(tmp_path)

    itr = _iterator('Threaded', max_workers=2)
    itr.collect_for_varrange({'x': [1, 2, 3]}, progress_bar=True)

    err = capsys.readouterr().err
    assert 'Threaded_Iter' in err   # the batch bar
    assert '3/3' in err
    assert '  job_0' in err         # and a job bar under it


def test_collect_default_stays_quiet_off_a_terminal(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _iterator('Quiet2', max_workers=2).collect_for_varrange({'x': [1, 2]})
    assert capsys.readouterr().err == ''


def test_child_output_goes_to_the_job_log(tmp_path, monkeypatch, capfd):
    """A job's own printing must not reach the terminal, where it would
    break the bars' line positions. capfd, not capsys: the redirect is done
    on the file descriptors, so the test has to capture them."""
    monkeypatch.chdir(tmp_path)

    wf = WorkFlow('Logged', actions=[Chatty('chat')])
    itr = SimulationIterator(wf, max_workers=2)
    itr.solve_parallel([{'x': 1}, {'x': 2}], progress_bar=False)

    captured = capfd.readouterr()
    assert 'chatter' not in captured.out
    assert 'chatter' not in captured.err

    # the results directory is named after the graph; '_Iter' is the
    # iterator action's own name, which is what labels the bar
    log = tmp_path / 'Logged' / 'job_0' / JOB_LOG_PATH
    assert log.exists() and 'chatter' in log.read_text()


def test_parallel_sweep_reuses_existing_jobs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    _, first = _iterator('Reuse', max_workers=2).collect_for_varrange({'x': [1, 2, 3]})

    itr = _iterator('Reuse', max_workers=2, reuse_existing=True)
    _, again = itr.collect_for_varrange({'x': [1, 2, 3]})

    assert again['calc'] == first['calc']
    assert len(itr.reused_jobs) == 3
    # nothing was run again, so no new job directories
    assert len(list((tmp_path / 'Reuse').glob('job_*'))) == 3


def test_solve_parallel_rejects_a_single_design_point(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    itr = _iterator('OneDict', max_workers=2)
    with pytest.raises(ParameterError, match='solve'):
        itr.solve_parallel({'x': 1})

    # ... which is the batch of one it was meant to be
    assert itr.solve_parallel([{'x': 1}]) == [{'x': 1, 'calc': 10}]


def test_solve_parallel_rejects_a_batch_that_is_not_design_points(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    itr = _iterator('NotDicts', max_workers=2)
    with pytest.raises(ParameterError):
        itr.solve_parallel([1, 2, 3])
    with pytest.raises(ParameterError):
        itr.solve_parallel(3)

    # an iterator of design points is a sequence for this purpose
    assert itr.solve_parallel(iter([{'x': 1}, {'x': 2}])) == [
        {'x': 1, 'calc': 10}, {'x': 2, 'calc': 20}]
