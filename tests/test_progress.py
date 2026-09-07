import os

import json
import tempfile
import time
from pathlib import Path

import pytest

from kunene.actions import WorkAction
from kunene.args import STATUS_PATH
from kunene.errors import KuneneError, AsyncActionError
from kunene.graph_actions import WorkFlow, DirectedGraph, WorkArea, SimulationIterator
from kunene import progress


class PlusOne(WorkAction):
    def solve(self, val_dict=None):
        return (val_dict.get(self.name, 0) if val_dict else 0) + 1


class Failing(WorkAction):
    def solve(self, val_dict=None):
        raise RuntimeError('boom')


class _in_tmp_dir:
    """Run a test body in a fresh temporary directory."""
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path.cwd()
        os.chdir(self._tmp.name)
        return Path(self._tmp.name)
    def __exit__(self, *exc):
        os.chdir(self._root)
        self._tmp.cleanup()


def _read_status(path=STATUS_PATH):
    return json.loads(Path(path).read_text())


def test_workflow_writes_status():
    with _in_tmp_dir():
        wf = WorkFlow('WF', actions=[PlusOne('a'), PlusOne('b')])
        wf.solve({})

        st = _read_status()
        assert st['name'] == 'WF'
        assert st['state'] == 'done'
        assert st['pid'] == os.getpid()
        assert st['actions']['a']['state'] == 'done'
        assert st['actions']['b']['state'] == 'done'
        assert progress.is_alive(st)   # just written


def test_failed_action_is_reported():
    with _in_tmp_dir():
        wf = WorkFlow('WF', actions=[PlusOne('a'), Failing('bad'), PlusOne('c')])
        with pytest.raises(RuntimeError):
            wf.solve({})

        st = _read_status()
        assert st['state'] == 'failed'
        assert st['actions']['a']['state'] == 'done'
        assert st['actions']['bad']['state'] == 'failed'
        assert st['actions']['c']['state'] == 'pending'


def test_nested_graph_does_not_clobber_owner():
    with _in_tmp_dir():
        inner = WorkFlow('Inner', actions=[PlusOne('deep')])
        outer = WorkFlow('Outer', actions=[PlusOne('a'), inner])
        outer.solve({})

        # one file, owned by the outer graph; the inner graph reports its
        # own actions through the owner's reporter (names stay unique).
        # The sub-graph is pass-through: it holds no entry of its own, so
        # the file counts the actions and nothing else.
        st = _read_status()
        assert st['name'] == 'Outer'
        assert 'Inner' not in st['actions']
        assert list(st['actions']) == ['a', 'deep']
        assert st['actions']['deep']['state'] == 'done'


def test_workarea_status_in_work_area_dir():
    with _in_tmp_dir():
        wf = WorkFlow('WFArea', actions=[PlusOne('a')])
        area = WorkArea(wf)
        area.solve({})
        st = _read_status(Path('WFArea') / STATUS_PATH)
        assert st['name'] == 'WFArea' and st['state'] == 'done'


class PeekAtStatus(WorkAction):
    """Reports a fraction from inside a work area, then reads back what the
    two status files say about it while it is still running."""
    def solve(self, val_dict=None):
        self.report_progress(fraction=0.5, message='halfway')
        return {'here':  json.loads(Path(STATUS_PATH).read_text()),
                'above': json.loads((Path('..') / STATUS_PATH).read_text())}


def test_workarea_progress_passes_through_to_the_enclosing_graph():
    with _in_tmp_dir():
        inner = WorkFlow('Inner', actions=[PeekAtStatus('deep')])
        outer = DirectedGraph('Outer')
        outer.add_action(WorkArea(inner))
        outer.add_action(PlusOne('after'))
        out = outer.solve({})

        # the work area holds no entry of its own; what is inside it does
        st = _read_status()
        assert 'Inner_WorkArea' not in st['actions']
        assert list(st['actions']) == ['deep', 'after']
        assert st['actions']['deep']['state'] == 'done'

        seen = out['Inner_WorkArea']['deep']
        # the work area still writes a file of its own, with its own actions
        assert seen['here']['name'] == 'Inner'
        assert list(seen['here']['actions']) == ['deep']
        assert seen['here']['actions']['deep']['fraction'] == 0.5
        # ... and the same progress reaches the enclosing graph's file, which
        # is the one a per-job progress bar reads
        assert seen['above']['name'] == 'Outer'
        assert seen['above']['actions']['deep']['fraction'] == 0.5
        assert seen['above']['actions']['deep']['message'] == 'halfway'
        assert seen['above']['actions']['after']['state'] == 'pending'


def test_workarea_in_a_job_reports_into_the_job_status():
    with _in_tmp_dir():
        inner = WorkFlow('Run', actions=[PlusOne('deep')])
        job = WorkFlow('It', actions=[WorkArea(inner), PlusOne('after')])
        SimulationIterator(job).solve({})

        # the job's file -- the one the job bar reads -- counts the two
        # actions, not the work area wrapping one of them
        st = _read_status(Path('It') / 'job_0' / STATUS_PATH)
        assert list(st['actions']) == ['deep', 'after']
        assert st['actions']['deep']['state'] == 'done'
        assert (Path('It') / 'job_0' / 'Run' / STATUS_PATH).exists()


class PeekAtEnclosingGraph(WorkAction):
    """From inside a job of a nested study, read the status file of the
    graph that holds the study: two levels up, past the job directory and
    the study's results root."""
    def solve(self, val_dict=None):
        return json.loads((Path('..') / '..' / STATUS_PATH).read_text())


def test_nested_iterator_keeps_one_entry_and_names_its_job():
    """A study is the opposite of a work area: its jobs have directories
    and status files of their own, so it holds one entry in the enclosing
    graph's file and reports the job it is busy with into it."""
    with _in_tmp_dir():
        study = SimulationIterator(WorkFlow('Inner',
                                            actions=[PeekAtEnclosingGraph('peek')]))
        outer = DirectedGraph('Outer')
        outer.add_action(study)
        outer.add_action(PlusOne('after'))
        out = outer.solve({})

        st = _read_status()
        assert list(st['actions']) == [study.name, 'after']
        assert st['actions'][study.name]['state'] == 'done'

        seen = out[study.name]['peek']['actions'][study.name]
        assert seen['state'] == 'running'
        assert seen['message'] == 'job_0'


def test_iterator_root_and_job_status():
    with _in_tmp_dir():
        wf = WorkFlow('It', actions=[PlusOne('a')])
        itr = SimulationIterator(wf)
        itr.solve({})
        itr.solve({})

        root = _read_status(Path('It') / STATUS_PATH)
        assert root['name'] == 'It_Iter'
        assert root['state'] == 'idle'
        assert root['jobs_done'] == 2
        assert root['jobs_total'] is None   # bare solve(): total unknown
        assert root['current_job'] == 'job_1'

        for job in ('job_0', 'job_1'):
            st = _read_status(Path('It') / job / STATUS_PATH)
            assert st['state'] == 'done'
            assert st['actions']['a']['state'] == 'done'


def test_iterator_collect_sets_totals_and_done():
    with _in_tmp_dir():
        wf = WorkFlow('Coll', actions=[PlusOne('a')])
        itr = SimulationIterator(wf)
        itr.collect_for_varrange({'V1': [1, 2, 3]})

        root = _read_status(Path('Coll') / STATUS_PATH)
        assert root['jobs_total'] == 3
        assert root['jobs_done'] == 3
        assert root['state'] == 'done'


def test_batch_counts_start_from_zero_each_sweep():
    """jobs_done is the count of the batch being run, not of everything the
    iterator has ever run: a second sweep of three used to report six."""
    with _in_tmp_dir():
        itr = SimulationIterator(WorkFlow('Batches', actions=[PlusOne('a')]))
        status = Path('Batches') / STATUS_PATH

        itr.collect_for_varrange({'V': [1, 2, 3]})
        root = _read_status(status)
        assert (root['jobs_done'], root['jobs_total']) == (3, 3)
        assert root['state'] == 'done'

        itr.collect_for_varrange({'V': [4, 5, 6]})
        root = _read_status(status)
        assert (root['jobs_done'], root['jobs_total']) == (3, 3)

        # a design point of the caller's own belongs to no batch, so there
        # is no total to count it against
        itr.solve({'V': 9})
        root = _read_status(status)
        assert root['jobs_total'] is None
        assert root['state'] == 'idle'


def test_a_finished_study_releases_its_results_root():
    """A study that has run lets go of its status.json, so a later study on
    the same directory owns it and is the one a reader sees."""
    with _in_tmp_dir():
        first = SimulationIterator(WorkFlow('Again', actions=[PlusOne('a')]))
        first.collect_for_varrange({'V': [1, 2]})
        assert first._status_reporter is None       # finished and released

        # started over, while the first iterator is still in scope
        second = SimulationIterator(WorkFlow('Again', actions=[PlusOne('a')]),
                                    clean_start=True)
        second.collect_for_varrange({'V': [1, 2, 3]})
        root = _read_status(Path('Again') / STATUS_PATH)
        assert root['state'] == 'done'
        assert (root['jobs_done'], root['jobs_total']) == (3, 3)

        # and one that adds to the same results directory takes it over too
        third = SimulationIterator(WorkFlow('Again', actions=[PlusOne('a')]))
        third.solve({'V': 7})
        root = _read_status(Path('Again') / STATUS_PATH)
        assert root['jobs_done'] == 1
        assert root['current_job'] == 'job_3'


def test_reuse_leaves_run_level_progress_to_the_caller():
    """Reusing a job must not report the run itself: inside a parallel
    batch that would claim the batch idle while other jobs are running."""
    with _in_tmp_dir():
        first = SimulationIterator(WorkFlow('Reuse', actions=[PlusOne('a')]))
        first.collect_for_varrange({'V': [1, 2]})

        later = SimulationIterator(WorkFlow('Reuse', actions=[PlusOne('a')]),
                                   reuse_existing=True)
        assert later._reuse({'V': 1}, []) is not None
        assert later._status_reporter is None       # nothing reported here

        # the caller is what reports it, and solve() does
        later.solve({'V': 1})
        root = _read_status(Path('Reuse') / STATUS_PATH)
        assert root['state'] == 'idle'
        assert root['jobs_done'] == 2


def test_a_new_reporter_takes_a_directory_over():
    with _in_tmp_dir() as tmp:
        old = progress.StatusReporter('Old', directory=tmp)
        old.start(actions=['a'])

        # a graph nested in an owned directory still stands aside
        nested = progress.StatusReporter('Nested', directory=tmp)
        assert not nested.active

        # a new run of a study claims it instead, and the old reporter goes
        # quiet rather than heartbeating over the new run's file
        new = progress.StatusReporter('New', directory=tmp, takeover=True)
        assert new.active and not old.active
        new.start(actions=['b'])
        old.action_state('a', 'done')              # a no-op now
        assert list(_read_status()['actions']) == ['b']

        old.finish('done')                         # must not disown 'new'
        assert new.active
        new.finish('done')


def test_mark_failed_stamps_a_file_whose_writer_died():
    with _in_tmp_dir() as tmp:
        rep = progress.StatusReporter('Killed', directory=tmp,
                                      heartbeat_interval=60.0)
        rep.start(actions=['a', 'b'])
        rep.action_state('a', 'running', fraction=0.5)
        # the process is terminated here, with 'b' never started
        progress.mark_failed(Path(tmp) / STATUS_PATH, message='terminated')

        st = _read_status()
        assert st['state'] == 'failed'
        assert st['actions']['a']['state'] == 'failed'
        assert st['actions']['a']['message'] == 'terminated'
        assert st['actions']['a']['fraction'] is None
        assert st['actions']['b']['state'] == 'pending'   # it never ran

        # a terminal file is left alone, and a missing one is not an error
        progress.mark_failed(Path(tmp) / STATUS_PATH, message='again')
        assert _read_status()['actions']['a']['message'] == 'terminated'
        progress.mark_failed(Path(tmp) / 'no_such_status.json')
        rep.finish('failed')


def test_status_watcher_polls_changes():
    with _in_tmp_dir() as tmp:
        watcher = progress.StatusWatcher(Path(tmp) / STATUS_PATH)
        assert watcher.poll() is None   # no file yet: no news, no error

        rep = progress.StatusReporter('W', directory=tmp)
        rep.start(actions=['a'])
        st = watcher.poll()
        assert st is not None and st['state'] == 'running'
        assert watcher.poll() is None   # unchanged

        time.sleep(0.02)                # ensure a new mtime
        rep.action_state('a', 'done')
        rep.finish('done')
        st = watcher.poll()
        assert st is not None and st['state'] == 'done'
        assert watcher.last is st


def test_run_watcher_follows_current_job():
    with _in_tmp_dir() as tmp:
        wf = WorkFlow('RW', actions=[PlusOne('a')])
        itr = SimulationIterator(wf)
        rw = progress.RunWatcher(Path(tmp) / 'RW')
        assert rw.poll() is None        # nothing yet

        itr.solve({})
        snap = rw.poll()
        assert snap is not None
        assert snap['root']['jobs_done'] == 1
        assert snap['job_name'] == 'job_0'
        assert snap['job']['actions']['a']['state'] == 'done'
        assert rw.poll() is None        # unchanged

        itr.solve({})
        snap = rw.poll()
        assert snap['job_name'] == 'job_1'
        assert snap['job']['state'] == 'done'

        assert 'job 2 of ?' in progress.format_status(snap)


def test_run_watcher_follows_several_jobs_at_once():
    with _in_tmp_dir() as tmp:
        root = Path(tmp) / 'Multi'
        (root / 'job_0').mkdir(parents=True)
        (root / 'job_1').mkdir(parents=True)
        rw = progress.RunWatcher(root)

        rep = progress.StatusReporter('M_Iter', directory=root)
        rep.start(actions=None, jobs_total=4, jobs_done=0,
                  current_job='job_1', current_jobs=['job_0', 'job_1'])
        job0 = progress.StatusReporter('G', directory=root / 'job_0')
        job1 = progress.StatusReporter('G', directory=root / 'job_1')
        job0.start(actions=['a'])
        job1.start(actions=['a'])
        job0.action_state('a', 'running', fraction=0.5, message='time 5 of 10')

        snap = rw.poll()
        assert list(snap['jobs']) == ['job_0', 'job_1']
        assert snap['job_name'] == 'job_0'          # first of them
        assert snap['job'] is snap['jobs']['job_0']

        text = progress.format_status(snap)
        assert 'job 0 of 4' in text
        assert 'job_0 - G: running' in text         # named, so they are told apart
        assert 'job_1 - G: running' in text
        assert '50%   time 5 of 10' in text

        time.sleep(0.02)                            # ensure a new mtime
        job0.finish('done')
        rep.update(jobs_done=1, current_jobs=['job_1'])
        snap = rw.poll()
        assert list(snap['jobs']) == ['job_1']      # job_0 is no longer followed
        assert 'job_0' not in progress.format_status(snap)

        job1.finish('done')
        rep.finish('done')


def test_job_fraction_averages_over_the_actions():
    assert progress.job_fraction(None) == (None, None)
    assert progress.job_fraction({'actions': {}}) == (None, None)

    status = {'actions': {
        'prep':   {'state': 'done'},
        'solver': {'state': 'running', 'fraction': 0.5, 'message': 'time 5 of 10'},
        'read':   {'state': 'pending'}}}
    fraction, message = progress.job_fraction(status)
    assert fraction == pytest.approx(1.5 / 3)      # one done, one half done
    # what it is busy with, where that action sits in the graph, and how far
    # it has got -- the fraction above is the job's, the smaller number
    assert message == 'solver 2 of 3: time 5 of 10 (50%)'

    # a running action without a fraction still names itself
    status['actions']['solver'] = {'state': 'running'}
    fraction, message = progress.job_fraction(status)
    assert fraction == pytest.approx(1 / 3)
    assert message == 'solver 2 of 3'


def test_job_fraction_names_every_running_action():
    """An asynch graph runs several actions at once; the message names them
    all, since none of them is 'the' current action."""
    status = {'actions': {
        'prep':  {'state': 'done'},
        'rad_a': {'state': 'running', 'fraction': 0.8, 'message': 'time 8 of 10'},
        'rad_b': {'state': 'running', 'fraction': 0.34},
        'post':  {'state': 'running'},
        'read':  {'state': 'pending'}}}
    fraction, message = progress.job_fraction(status)
    assert fraction == pytest.approx((1 + 0.8 + 0.34) / 5)
    assert message == '3 of 5 running: rad_a (80%), rad_b (34%), post'


def test_is_alive_detects_stale_heartbeat():
    now = time.time()
    assert progress.is_alive({'heartbeat_interval': 5.0, 'updated_at': now})
    assert not progress.is_alive({'heartbeat_interval': 5.0, 'updated_at': now - 60.0})
    assert not progress.is_alive(None)
    assert not progress.is_alive({})


@pytest.mark.skipif(not hasattr(os, 'fork'),
                    reason='fork-specific: on Windows an asynch child is spawned, '
                           'which test_asynch_graph_reports_fractions covers')
def test_forked_child_writes_sidecar_and_owner_merges():
    import multiprocessing
    with _in_tmp_dir() as tmp:
        rep = progress.StatusReporter('G', directory=tmp)
        rep.start(actions=['solver'])
        rep.action_state('solver', 'running')

        # a forked child (as in an asynch action) reports a fraction
        def child():
            rep.action_state('solver', 'running', fraction=0.4, message='time 0.8 of 2')
        p = multiprocessing.get_context('fork').Process(target=child)
        p.start(); p.join()

        # the child wrote a sidecar, not the status file
        sidecar = Path(tmp) / f'.solver{progress.SIDECAR_SUFFIX}'
        assert sidecar.exists()
        assert _read_status()['actions']['solver']['fraction'] is None

        # the owner's next write folds it in
        rep.update()
        entry = _read_status()['actions']['solver']
        assert entry['fraction'] == 0.4
        assert entry['message'] == 'time 0.8 of 2'
        assert entry['state'] == 'running'   # owner keeps state authority

        # terminal state: sidecar is dropped, fraction cleared by the sweep
        rep.action_state('solver', 'done')
        rep.update()
        assert not sidecar.exists()
        assert _read_status()['actions']['solver']['state'] == 'done'
        rep.finish('done')
        assert not list(Path(tmp).glob('.*' + progress.SIDECAR_SUFFIX))


class AsyncFakeSolver(WorkAction):
    """Reports fractions the way a solver action does.

    At module level, not inside the test that uses it: an asynch action is
    pickled to reach its child under the ``spawn`` start method (Windows),
    and a class defined inside a function cannot be pickled.
    """
    def solve(self, val_dict=None):
        from kunene.progress import FileProgressTail
        from kunene.util import solver_progress

        log = Path(f'{self.name}.stdout')
        tail = FileProgressTail(self._progress_reporter, self.name, log,
                                solver_progress.radioss_run_time,
                                t_end=2.0, interval=0.05)
        tail.start()
        try:
            for t in ('0.0000E+00', '1.0000E+00', '2.0000E+00'):
                with open(log, 'a') as f:
                    f.write(f' NC= 100 T= {t} DT= 1E-06\n')
                time.sleep(0.3)
        finally:
            tail.stop()
        return 1.0


def test_asynch_graph_reports_fractions():
    """End-to-end: an asynch graph's action runs in a child process; its
    FileProgressTail fractions must reach status.json via sidecars."""
    import threading

    saved = progress.HEARTBEAT_INTERVAL
    progress.HEARTBEAT_INTERVAL = 0.1   # merge sidecars quickly for the test
    try:
        with _in_tmp_dir() as tmp:
            g = DirectedGraph('AG', asynch=True)
            g.add_action(AsyncFakeSolver('solver'))

            status = Path(tmp) / STATUS_PATH
            seen = []
            worker = threading.Thread(target=g.solve, args=({},))
            worker.start()
            while worker.is_alive():
                try:
                    entry = json.loads(status.read_text())['actions']['solver']
                    if entry.get('fraction') is not None:
                        seen.append(entry['fraction'])
                except (FileNotFoundError, json.JSONDecodeError, KeyError):
                    pass
                time.sleep(0.05)
            worker.join()

            assert seen, 'no fractions from the asynch child observed'
            assert max(seen) > 0.4
            st = json.loads(status.read_text())
            assert st['state'] == 'done'
            assert st['actions']['solver']['state'] == 'done'
            assert not list(Path(tmp).glob('.*' + progress.SIDECAR_SUFFIX))
    finally:
        progress.HEARTBEAT_INTERVAL = saved


def test_asynch_work_areas_report_the_actions_inside_them():
    """Two solvers running at once, each in a work area of its own (the
    shape parallel branches need, since they share a directory otherwise).
    Each work area runs in a child process, so the actions inside it reach
    the owner's status.json as sidecar files -- and the owner must take
    their state from there, having never set it itself."""
    import threading

    saved = progress.HEARTBEAT_INTERVAL
    progress.HEARTBEAT_INTERVAL = 0.1   # merge sidecars quickly for the test
    try:
        with _in_tmp_dir() as tmp:
            g = DirectedGraph('AG', asynch=True)
            g.add_action(WorkArea(WorkFlow('Wa', actions=[AsyncFakeSolver('sol_a')])))
            g.add_action(WorkArea(WorkFlow('Wb', actions=[AsyncFakeSolver('sol_b')])))

            status = Path(tmp) / STATUS_PATH
            seen = {'sol_a': [], 'sol_b': []}
            worker = threading.Thread(target=g.solve, args=({},))
            worker.start()
            while worker.is_alive():
                try:
                    actions = json.loads(status.read_text())['actions']
                except (FileNotFoundError, json.JSONDecodeError, KeyError):
                    actions = {}
                for name, fractions in seen.items():
                    entry = actions.get(name) or {}
                    if entry.get('fraction') is not None:
                        fractions.append(entry['fraction'])
                time.sleep(0.05)
            worker.join()

            st = json.loads(status.read_text())
            assert 'Wa_WorkArea' not in st['actions']
            assert list(st['actions']) == ['sol_a', 'sol_b']
            for name, fractions in seen.items():
                assert fractions, f'no fractions from {name} observed'
                assert st['actions'][name]['state'] == 'done'
            assert not list(Path(tmp).glob('.*' + progress.SIDECAR_SUFFIX))
    finally:
        progress.HEARTBEAT_INTERVAL = saved


class SlowPlusOne(WorkAction):
    def solve(self, val_dict=None):
        time.sleep(3.0)
        return 1


class HardCrash(WorkAction):
    def solve(self, val_dict=None):
        os._exit(9)   # simulates a segfault/oom-kill: no exception raised


def test_asynch_failed_child_reports_failed():
    with _in_tmp_dir():
        g = DirectedGraph('AF', asynch=True)
        g.add_action(Failing('bad'))
        g.add_action(SlowPlusOne('slow_sibling'))

        with pytest.raises(AsyncActionError) as excinfo:
            g.solve({})
        # the child's exception type, message and traceback are surfaced
        assert 'bad' in str(excinfo.value)
        assert 'RuntimeError: boom' in str(excinfo.value)

        st = _read_status()
        assert st['state'] == 'failed'
        assert st['actions']['bad']['state'] == 'failed'
        assert 'RuntimeError' in st['actions']['bad']['message']
        # the still-running sibling was terminated and marked failed
        assert st['actions']['slow_sibling']['state'] == 'failed'
        assert 'terminated' in st['actions']['slow_sibling']['message']


def test_asynch_crashed_child_reports_failed():
    with _in_tmp_dir():
        g = DirectedGraph('AC', asynch=True)
        g.add_action(HardCrash('crasher'))

        with pytest.raises(AsyncActionError) as excinfo:
            g.solve({})
        assert 'exited with code 9' in str(excinfo.value)
        st = _read_status()
        assert st['state'] == 'failed'
        assert st['actions']['crasher']['state'] == 'failed'


class SlowSaysItStarted(WorkAction):
    """Announces itself in the directory above its work area, then runs on."""
    MARKER = 'slow_started'

    def solve(self, val_dict=None):
        (Path('..') / self.MARKER).touch()
        time.sleep(3.0)
        return 1


class FailsOnceSiblingRuns(WorkAction):
    """Fails only once the sibling work area is actually running, so the
    terminated-sibling path is reached whatever the start method: under
    spawn a child takes long enough to boot that failing immediately would
    cut the sibling short before it had reported anything."""

    def solve(self, val_dict=None):
        for _ in range(200):
            if (Path('..') / SlowSaysItStarted.MARKER).exists():
                break
            time.sleep(0.05)
        raise RuntimeError('boom')


def test_asynch_work_area_failure_fails_what_it_left_running():
    """A work area holds no entry to mark failed -- but the actions inside
    it do, and a child process that is terminated cannot report them
    itself, so the graph fails whatever they left running."""
    with _in_tmp_dir():
        g = DirectedGraph('AWF', asynch=True)
        g.add_action(WorkArea(WorkFlow('Wbad', actions=[FailsOnceSiblingRuns('bad')])))
        g.add_action(WorkArea(WorkFlow('Wslow', actions=[SlowSaysItStarted('slow')])))

        with pytest.raises(AsyncActionError):
            g.solve({})

        st = _read_status()
        assert st['state'] == 'failed'
        assert list(st['actions']) == ['bad', 'slow']
        assert st['actions']['bad']['state'] == 'failed'
        # terminated with its work area, not left 'running' for ever
        assert st['actions']['slow']['state'] == 'failed'
        assert 'terminated' in (st['actions']['slow']['message'] or '')


def test_asynch_success_still_works():
    with _in_tmp_dir():
        g = DirectedGraph('AS', asynch=True)
        a = g.add_action(PlusOne('a'))
        b = g.add_action(PlusOne('b'))
        g.add_action(PlusOne('c'), parents=[a, b])
        ret = g.solve({})
        assert ret['c'] == 1
        st = _read_status()
        assert st['state'] == 'done'
        assert all(v['state'] == 'done' for v in st['actions'].values())


def test_atomic_write_leaves_no_temp_file():
    with _in_tmp_dir() as tmp:
        rep = progress.StatusReporter('A', directory=tmp)
        rep.start(actions=['x'])
        rep.finish('done')
        names = [p.name for p in Path(tmp).iterdir()]
        assert STATUS_PATH in names
        assert not any(n.endswith('.tmp') for n in names)


if __name__ == '__main__':
    test_workflow_writes_status()
    test_failed_action_is_reported()
    test_nested_graph_does_not_clobber_owner()
    test_workarea_status_in_work_area_dir()
    test_iterator_root_and_job_status()
    test_iterator_collect_sets_totals_and_done()
    test_status_watcher_polls_changes()
    test_run_watcher_follows_current_job()
    test_is_alive_detects_stale_heartbeat()
    test_atomic_write_leaves_no_temp_file()
    print('all progress tests passed')
