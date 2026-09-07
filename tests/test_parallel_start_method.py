"""
Running child processes on a platform without ``fork``.

``SimulationIterator(max_workers=N)`` and an ``asynch`` ``DirectedGraph``
both run work in child processes. Where the platform has ``fork`` the child
is a copy of this process and nothing has to be picklable; on Windows the
only start method is ``spawn``, where the child is a fresh interpreter and
the target callable and its arguments travel as a pickle.

These tests force ``spawn`` on any platform, so the Windows path is
exercised by the ordinary (Linux) test run.
"""

import json
import multiprocessing
import os
import pathlib
import pickle
import subprocess
import sys
import textwrap
import time

import pytest

from kunene.actions import MathEvaluation, WorkAction
from kunene.args import JOBS_INDEX_PATH, JOB_LOG_PATH, STATUS_PATH
from kunene.errors import AsyncActionError
from kunene.graph_actions import DirectedGraph, WorkFlow
from kunene.progress import StatusReporter
from kunene.simulation_iterator import SimulationIterator
from kunene.util import parallel


# Actions a spawned child has to be able to unpickle, so they live at
# module level -- a class defined inside a test function could not travel.

class Chatty(WorkAction):
    """Writes to stdout the way the solver wrappers do."""
    def solve(self, val_dict=None):
        print('chatter from the job')
        return 1


class Boom(WorkAction):
    def solve(self, val_dict=None):
        raise RuntimeError('boom')


class Adder(WorkAction):
    def solve(self, val_dict=None):
        time.sleep(0.1)
        return (val_dict or {}).get('x', 0) + 1


@pytest.fixture
def spawned(monkeypatch):
    """Make kunene start its child processes the way Windows must."""
    monkeypatch.setattr(parallel, 'START_METHOD', 'spawn')
    monkeypatch.delenv(parallel.ENV_VAR, raising=False)
    assert parallel.get_context().get_start_method() == 'spawn'
    assert not parallel.uses_fork()


# ----------------------------------------------------------------------
# choosing the start method

def test_fork_is_preferred_where_the_platform_has_it(monkeypatch):
    monkeypatch.setattr(parallel, 'START_METHOD', None)
    monkeypatch.delenv(parallel.ENV_VAR, raising=False)
    expected = ('fork' if 'fork' in multiprocessing.get_all_start_methods()
                else 'spawn')
    assert parallel.get_context().get_start_method() == expected


def test_spawn_is_used_on_a_platform_without_fork(monkeypatch):
    """What happens on Windows: 'fork' is not a known context there."""
    monkeypatch.setattr(parallel, 'START_METHOD', None)
    monkeypatch.delenv(parallel.ENV_VAR, raising=False)

    real = multiprocessing.get_context

    def no_fork(method=None):
        if method == 'fork':
            raise ValueError("cannot find context for 'fork'")
        return real(method)

    monkeypatch.setattr(multiprocessing, 'get_context', no_fork)
    assert parallel.get_context().get_start_method() == 'spawn'


def test_start_method_can_be_forced(monkeypatch):
    monkeypatch.setattr(parallel, 'START_METHOD', 'spawn')
    assert parallel.get_context().get_start_method() == 'spawn'

    monkeypatch.setattr(parallel, 'START_METHOD', None)
    monkeypatch.setenv(parallel.ENV_VAR, 'spawn')
    assert parallel.get_context().get_start_method() == 'spawn'


def test_an_unusable_forced_method_falls_back(monkeypatch):
    """A setting this platform cannot honour must not break the run."""
    monkeypatch.setattr(parallel, 'START_METHOD', 'no_such_method')
    monkeypatch.delenv(parallel.ENV_VAR, raising=False)
    assert parallel.get_context().get_start_method() in ('fork', 'spawn')


# ----------------------------------------------------------------------
# what spawn requires: everything crossing to the child must pickle

def test_status_reporter_survives_a_pickle_round_trip(tmp_path):
    """It carries a lock, an event and a heartbeat thread, none of which
    pickle -- but a child needs a reporter to write its sidecars."""
    reporter = StatusReporter('Rep', directory=tmp_path)
    reporter.start(actions=['a'])
    try:
        clone = pickle.loads(pickle.dumps(reporter))
    finally:
        reporter.finish('done')

    assert clone._owner_pid == reporter._owner_pid   # still knows the owner
    assert clone._lock is not None                   # rebuilt, not shared
    assert clone._hb_thread is None
    clone.action_state('a', 'running', fraction=0.5)  # must not raise


def test_iterator_survives_a_pickle_round_trip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    wf = WorkFlow('Pickled', actions=[MathEvaluation('calc', 'x * 10')])
    itr = SimulationIterator(wf, max_workers=2)
    itr.solve({'x': 1})                     # gives it a live status reporter

    clone = pickle.loads(pickle.dumps(itr))

    # the results-root reporter belongs to the parent and is left behind
    assert itr._status_reporter is not None
    assert clone._status_reporter is None
    assert clone.graph.name == 'Pickled'
    assert clone.work_area_path == itr.work_area_path
    assert clone._index.job_prefix == itr._index.job_prefix


def test_action_pickles_without_its_child_process():
    """_async_proc is a live Process: unpicklable, and meaningless in the
    child anyway."""
    action = Chatty('chat')
    action._async_proc = object()           # stands in for a live Process
    assert '_async_proc' not in action.__getstate__()
    assert pickle.loads(pickle.dumps(action)).name == 'chat'


# ----------------------------------------------------------------------
# the real thing, running on spawn

def test_parallel_sweep_works_without_fork(spawned, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    wf = WorkFlow('Spawned', actions=[MathEvaluation('calc', 'x * 10')])
    itr = SimulationIterator(wf, max_workers=3)
    _, outcome = itr.collect_for_varrange({'x': [1, 2, 3, 4, 5]})

    assert outcome['calc'] == [10, 20, 30, 40, 50]

    jobs = sorted((tmp_path / 'Spawned').glob('job_*'))
    assert [j.name for j in jobs] == [f'job_{i}' for i in range(5)]
    for i, job in enumerate(jobs):
        assert json.loads((job / 'iter_variables.json').read_text()) == {'x': i + 1}

    index = json.loads((tmp_path / 'Spawned' / JOBS_INDEX_PATH).read_text())
    assert [r['state'] for r in index['jobs']] == ['done'] * 5


def test_spawned_sweep_matches_a_forking_one(spawned, tmp_path, monkeypatch):
    """The start method must not show in the results."""
    monkeypatch.chdir(tmp_path)

    wf = WorkFlow('Same', actions=[MathEvaluation('calc', 'x + 1')])
    _, spawn_out = SimulationIterator(
        wf, max_workers=3).collect_for_varrange({'x': [1, 2, 3]})

    monkeypatch.setattr(parallel, 'START_METHOD', None)     # back to default
    wf2 = WorkFlow('Default', actions=[MathEvaluation('calc', 'x + 1')])
    _, default_out = SimulationIterator(
        wf2, max_workers=3).collect_for_varrange({'x': [1, 2, 3]})

    assert spawn_out == default_out


def test_spawned_jobs_report_progress(spawned, tmp_path, monkeypatch):
    """Each job writes its own status.json, which is what feeds the bars."""
    monkeypatch.chdir(tmp_path)

    wf = WorkFlow('Reported', actions=[Adder('add')])
    SimulationIterator(wf, max_workers=2).collect_for_varrange({'x': [1, 2]})

    root = json.loads((tmp_path / 'Reported' / STATUS_PATH).read_text())
    assert root['state'] == 'done'
    assert root['jobs_done'] == 2

    for job in ('job_0', 'job_1'):
        st = json.loads((tmp_path / 'Reported' / job / STATUS_PATH).read_text())
        assert st['actions']['add']['state'] == 'done'


def test_spawned_job_output_goes_to_the_job_log(spawned, tmp_path, monkeypatch, capfd):
    monkeypatch.chdir(tmp_path)

    wf = WorkFlow('Logged', actions=[Chatty('chat')])
    SimulationIterator(wf, max_workers=2).solve_parallel(
        [{'x': 1}, {'x': 2}], progress_bar=False)

    captured = capfd.readouterr()
    assert 'chatter' not in captured.out
    assert 'chatter' not in captured.err
    log = tmp_path / 'Logged' / 'job_0' / JOB_LOG_PATH
    assert log.exists() and 'chatter' in log.read_text()


def test_a_spawned_job_that_fails_aborts_the_sweep(spawned, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    wf = WorkFlow('Failing', actions=[Boom('bang')])
    itr = SimulationIterator(wf, max_workers=2)

    with pytest.raises(AsyncActionError) as excinfo:
        itr.collect_for_varrange({'x': [1, 2]})

    assert 'boom' in str(excinfo.value)             # the child's traceback
    assert 'RuntimeError' in str(excinfo.value)

    index = json.loads((tmp_path / 'Failing' / JOBS_INDEX_PATH).read_text())
    assert 'running' not in {r['state'] for r in index['jobs']}


def test_asynch_graph_works_without_fork(spawned, tmp_path, monkeypatch):
    """The other place kunene forks: an asynch graph's actions."""
    monkeypatch.chdir(tmp_path)

    graph = DirectedGraph('Async', asynch=True)
    graph.add_action(Adder('a'))
    graph.add_action(Adder('b'))

    out = graph.solve({'x': 1})
    assert out['a'] == 2 and out['b'] == 2


def test_asynch_failure_without_fork(spawned, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    graph = DirectedGraph('AsyncFail', asynch=True)
    graph.add_action(Boom('bang'))

    with pytest.raises(AsyncActionError) as excinfo:
        graph.solve({'x': 1})
    assert 'boom' in str(excinfo.value)


# ----------------------------------------------------------------------
# no ``if __name__ == '__main__':`` guard needed: the spawned child does
# not re-import the calling script

REPO_ROOT = str(pathlib.Path(__file__).resolve().parents[1])

UNGUARDED_SCRIPT = """
from kunene.actions import MathEvaluation
from kunene.graph_actions import DirectedGraph, WorkFlow
from kunene.simulation_iterator import SimulationIterator

# a bare script: no entry-point guard anywhere
wf = WorkFlow('Bare', actions=[MathEvaluation('calc', 'x * 10')])
_, out = SimulationIterator(wf, max_workers=2).collect_for_varrange(
    {'x': [1, 2, 3]})
print('sweep', out['calc'])

dg = DirectedGraph('BareAsync', asynch=True)
dg.add_action(MathEvaluation('a', 'x + 1'))
dg.add_action(MathEvaluation('b', 'x + 2'))
print('asynch', dg.solve({'x': 1})['a'], dg.solve({'x': 1})['b'])
"""

SCRIPT_WITH_ITS_OWN_ACTION = """
from kunene.actions import WorkAction
from kunene.graph_actions import WorkFlow
from kunene.simulation_iterator import SimulationIterator

class Local(WorkAction):
    def solve(self, val_dict=None):
        return val_dict['x'] * 10

%s
"""

RUN_LOCAL = """
wf = WorkFlow('Local', actions=[Local('calc')])
_, out = SimulationIterator(wf, max_workers=2).collect_for_varrange(
    {'x': [1, 2]})
print('sweep', out['calc'])
"""


def _run_script(tmp_path, source, **env_extra):
    script = tmp_path / 'study.py'
    script.write_text(source)
    env = dict(os.environ, PYTHONPATH=REPO_ROOT, KUNENE_START_METHOD='spawn')
    env.pop(parallel.IMPORT_MAIN_ENV_VAR, None)
    env.update(env_extra)
    return subprocess.run([sys.executable, str(script)], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=120)


def test_an_unguarded_script_runs_under_spawn(tmp_path):
    """The whole point: a script need not guard its entry point."""
    res = _run_script(tmp_path, UNGUARDED_SCRIPT)
    assert res.returncode == 0, res.stderr
    assert 'sweep [10, 20, 30]' in res.stdout
    assert 'asynch 2 3' in res.stdout
    assert 'bootstrapping phase' not in res.stderr


def test_an_action_defined_in_the_script_is_refused_clearly(tmp_path):
    """The child has no script to find ``Local`` in, so say so up front
    instead of dying in the child with an AttributeError."""
    res = _run_script(tmp_path, SCRIPT_WITH_ITS_OWN_ACTION % RUN_LOCAL)
    assert res.returncode != 0
    assert 'SpawnError' in res.stderr
    assert 'Local' in res.stderr
    assert 'KUNENE_SPAWN_IMPORTS_MAIN' in res.stderr


def test_import_main_restores_the_guarded_behaviour(tmp_path):
    """Opting back in: the child re-imports the script, so an in-script
    class works -- behind the guard, as with stock multiprocessing."""
    guarded = SCRIPT_WITH_ITS_OWN_ACTION % (
        "if __name__ == '__main__':\n"
        + textwrap.indent(RUN_LOCAL, '    '))
    res = _run_script(tmp_path, guarded, KUNENE_SPAWN_IMPORTS_MAIN='1')
    assert res.returncode == 0, res.stderr
    assert 'sweep [10, 20]' in res.stdout


def test_main_references_finds_only_script_level_objects():
    class Pretender(WorkAction):
        def solve(self, val_dict=None):
            return 1
    Pretender.__module__ = '__main__'            # as if defined in a script

    assert parallel.main_references(Chatty('c'), {'k': [1, 2]}) == []
    found = parallel.main_references((Pretender('p'), 3))
    assert found and found[0].endswith('Pretender')


def test_hidden_main_is_restored_after_the_start(monkeypatch):
    main = sys.modules['__main__']
    spec, file = getattr(main, '__spec__', None), getattr(main, '__file__', None)
    with parallel._hidden_main():
        assert main.__spec__ is None
        assert not hasattr(main, '__file__')
    assert getattr(main, '__spec__', None) is spec
    assert getattr(main, '__file__', None) == file


def test_start_process_refuses_a_main_object_under_spawn(spawned):
    class Pretender(WorkAction):
        def solve(self, val_dict=None):
            return 1
    Pretender.__module__ = '__main__'

    from kunene.errors import SpawnError
    with pytest.raises(SpawnError, match='Pretender'):
        parallel.start_process(parallel.get_context(), print, args=(Pretender('p'),))
