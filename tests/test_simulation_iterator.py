import sys, os, shutil
from pathlib import Path
import pytest

from kunene.graph_actions import WorkFlow, WorkArea
from kunene.simulation_iterator import SimulationIterator, JobIndex
from kunene.actions import MathEvaluation
from kunene.errors import KuneneError, DataNotFoundError

def test_work_area_custom_path():
    print("Testing WorkArea with custom path...")
    wf = WorkFlow("TestWF")
    wf.add_action(MathEvaluation("calc", "2 * x"))
    
    custom_path = Path("custom_work_area")
    if custom_path.exists():
        shutil.rmtree(custom_path)
        
    wa = WorkArea(wf, work_area_path=str(custom_path))
    params = {'x': 5}
    
    try:
        res = wa.solve(params)
        assert res['calc'] == 10
        assert custom_path.exists()
    finally:
        wa.rm_rundir()

@pytest.mark.parametrize("path_template", [
    "/tmp/kunene_WA_test",
    "~/tmp/kunene_WA_test",
    "$HOME/tmp/kunene_WA_test"
])
def test_simulation_iterator_special_paths(path_template):
    print(f"Testing SimulationIterator with path: {path_template}")
    wf = WorkFlow("TestWF_Special")
    wf.add_action(MathEvaluation("calc", "x + 1"))
    
    # We need to resolve the path for verification in the test
    resolved_path = Path(os.path.expandvars(os.path.expanduser(path_template)))
    
    if resolved_path.exists():
        shutil.rmtree(resolved_path)
        
    sim_iter = SimulationIterator(wf, work_area_path=path_template, clean_start=True)
    
    try:
        # Run iteration
        params = {'x': 100}
        res = sim_iter.solve(params) 
        
        # Verify
        assert res['calc'] == 101
        assert sim_iter.work_area_path == resolved_path
        assert resolved_path.exists()
        assert (resolved_path / "job_0").exists()
        
    finally:
        sim_iter.rm_rundir()
        # Clean up the ~/tmp or $HOME/tmp if we created it
        if resolved_path.exists():
            shutil.rmtree(resolved_path)


# ----------------------------------------------------------------------
# job index: retrieving past results and grouping runs

def _study_iterator( path, **kwargs ):
    """A fresh iterator pointed at the same results directory. Used to
    check that results can be retrieved by code that did not run them."""
    wf = WorkFlow("Study")
    wf.add_action(MathEvaluation("energy", "K * T"))
    return SimulationIterator(wf, work_area_path=str(path), **kwargs)


@pytest.fixture
def study_path(tmp_path):
    return tmp_path / "study"


def test_simulation_iterator_still_importable_from_graph_actions():
    """SimulationIterator moved to its own module; workflows import it
    from graph_actions and must keep working."""
    from kunene.graph_actions import SimulationIterator as FromGraphActions
    assert FromGraphActions is SimulationIterator


def test_index_records_variables_and_groups(study_path):
    itr = _study_iterator(study_path, groups='baseline')
    itr.collect_for_varrange({'K': [0.2, 0.3], 'T': [75]})

    # a new iterator reads the index written by the run
    reader = _study_iterator(study_path)
    recs = reader.job_index().records
    assert [r['job'] for r in recs] == ['job_0', 'job_1']
    assert recs[0]['variables'] == {'K': 0.2, 'T': 75}
    assert recs[0]['groups'] == ['baseline']
    assert all(r['state'] == 'done' for r in recs)


def test_results_for_returns_stored_outputs(study_path):
    itr = _study_iterator(study_path)
    itr.collect_for_varrange({'K': [0.2, 0.3], 'T': [75]})

    reader = _study_iterator(study_path)
    assert reader.results_for({'K': 0.2, 'T': 75})['energy'] == pytest.approx(15.0)
    # a partial, unambiguous match is accepted
    assert reader.results_for({'K': 0.3})['energy'] == pytest.approx(22.5)
    assert reader.find_job(where={'K': 0.3}).name == 'job_1'

    with pytest.raises(DataNotFoundError):
        reader.results_for({'K': 9.9, 'T': 75})
    # T=75 holds for both jobs: ambiguous rather than silently wrong
    with pytest.raises(DataNotFoundError):
        reader.results_for({'T': 75})


def test_jobs_carry_several_groups_and_can_be_retagged(study_path):
    itr = _study_iterator(study_path, groups='baseline')
    itr.collect_for_varrange({'K': [0.2, 0.3], 'T': [75]})

    reader = _study_iterator(study_path)
    reader.add_groups(['screened', 'converged'], where={'K': 0.2})
    assert reader.groups_of('job_0') == ['baseline', 'screened', 'converged']
    assert reader.group_names() == ['baseline', 'converged', 'screened']

    assert [p.name for p in reader.find_jobs(groups='baseline')] == ['job_0', 'job_1']
    assert [p.name for p in reader.find_jobs(groups='converged')] == ['job_0']
    # any-of versus all-of
    assert len(reader.find_jobs(groups=['converged', 'baseline'])) == 2
    assert len(reader.find_jobs(groups=['converged', 'baseline'],
                                match_all_groups=True)) == 1

    reader.remove_groups('converged', jobs=['job_0'])
    assert reader.find_jobs(groups='converged') == []


def test_collect_reads_a_group_from_disk(study_path):
    itr = _study_iterator(study_path)
    itr.collect_for_varrange({'K': [0.2, 0.3], 'T': [75]}, groups='sweep_a')
    itr2 = _study_iterator(study_path, reuse_existing=True)
    itr2.collect_for_varrange({'K': [0.5], 'T': [75]}, groups='sweep_b')

    reader = _study_iterator(study_path)
    pars, out = reader.collect(groups='sweep_a')
    assert list(pars['K']) == pytest.approx([0.2, 0.3])
    assert out['energy'] == pytest.approx([15.0, 22.5])

    pars, out = reader.collect(groups='sweep_b')
    assert list(pars['K']) == pytest.approx([0.5])
    assert out['energy'] == pytest.approx([37.5])

    with pytest.raises(DataNotFoundError):
        reader.collect(groups='never_used')


def test_reuse_existing_skips_completed_design_points(study_path):
    itr = _study_iterator(study_path, groups='first')
    itr.collect_for_varrange({'K': [0.2], 'T': [75]})

    itr2 = _study_iterator(study_path, reuse_existing=True, groups='second')
    pars, out = itr2.collect_for_varrange({'K': [0.2, 0.5], 'T': [75]})

    assert itr2.reused_jobs == ['job_0']            # K=0.2 came off disk
    assert out['energy'] == pytest.approx([15.0, 37.5])
    # only the new design point created a directory, numbered after job_0
    jobs = sorted(p.name for p in study_path.iterdir()
                  if p.is_dir() and p.name.startswith('job_'))
    assert jobs == ['job_0', 'job_1']
    # the reused job now belongs to both studies
    assert itr2.groups_of('job_0') == ['first', 'second']


def test_existing_results_directory_is_added_to(study_path):
    """A later session extends the study instead of being refused, and
    without overwriting the jobs that are already there."""
    first = _study_iterator(study_path, groups='session_1')
    first.solve({'K': 0.2, 'T': 75})

    second = _study_iterator(study_path, groups='session_2')
    second.solve({'K': 0.3, 'T': 75})       # same values would also be re-run
    second.solve({'K': 0.2, 'T': 75})

    recs = second.job_index().records
    assert [r['job'] for r in recs] == ['job_0', 'job_1', 'job_2']
    assert [r['groups'] for r in recs] == [['session_1'], ['session_2'], ['session_2']]
    assert second.results_for({'K': 0.3, 'T': 75})['energy'] == pytest.approx(22.5)
    # the first session's job is untouched: still there, still labelled
    assert first.job_index(rebuild=True).find(where={'K': 0.2})[0]['job'] == 'job_0'


def test_repeated_design_point_resolves_to_the_most_recent_job(study_path):
    """Appending means the same values can appear twice (changed deck,
    new solver version). The latest job describes that design point."""
    first = _study_iterator(study_path)
    first.solve({'K': 0.2, 'T': 75})

    wf = WorkFlow("Study")
    wf.add_action(MathEvaluation("energy", "2 * K * T"))     # 'changed deck'
    second = SimulationIterator(wf, work_area_path=str(study_path))
    second.solve({'K': 0.2, 'T': 75})

    assert [p.name for p in second.find_jobs(where={'K': 0.2})] == ['job_0', 'job_1']
    assert second.results_for({'K': 0.2, 'T': 75})['energy'] == pytest.approx(30.0)

    # and reuse picks up the latest as well
    third = SimulationIterator(wf, work_area_path=str(study_path), reuse_existing=True)
    _, out = third.collect_for_varrange({'K': [0.2], 'T': [75]})
    assert third.reused_jobs == ['job_1']
    assert out['energy'] == pytest.approx([30.0])


def test_jobs_are_never_overwritten_when_a_job_dir_is_missing(study_path):
    """Numbering comes from the whole results directory, not from a per
    instance counter, so a gap does not send a run over existing jobs."""
    itr = _study_iterator(study_path)
    itr.collect_for_varrange({'K': [0.2, 0.3], 'T': [75]})
    shutil.rmtree(study_path / 'job_0')

    later = _study_iterator(study_path)
    later.solve({'K': 0.9, 'T': 1.0})

    assert later.job_index(rebuild=True).find(where={'K': 0.3})[0]['job'] == 'job_1'
    assert later.results_for({'K': 0.9, 'T': 1.0})['energy'] == pytest.approx(0.9)
    assert later.results_for({'K': 0.3, 'T': 75})['energy'] == pytest.approx(22.5)


def test_index_rebuilds_from_job_directories(study_path):
    itr = _study_iterator(study_path)
    itr.collect_for_varrange({'K': [0.2, 0.3], 'T': [75]})

    # an index written before this feature existed / deleted by hand
    (study_path / 'jobs_index.json').unlink()

    reader = _study_iterator(study_path)
    recs = reader.job_index().records
    assert [r['job'] for r in recs] == ['job_0', 'job_1']
    assert recs[1]['variables'] == {'K': 0.3, 'T': 75}
    assert reader.results_for({'K': 0.3, 'T': 75})['energy'] == pytest.approx(22.5)


def test_iterdir_and_gather_outputs_survive_a_gap(study_path):
    itr = _study_iterator(study_path)
    itr.collect_for_varrange({'K': [0.2, 0.3, 0.4], 'T': [75]})
    shutil.rmtree(study_path / 'job_0')          # archived by hand

    later = _study_iterator(study_path)
    assert [p.name for p in later.iterdir()] == ['job_1', 'job_2']
    assert [o['energy'] for o in later.gather_outputs()] == pytest.approx([22.5, 30.0])


def test_gather_outputs_skips_a_failed_job(study_path):
    wf = WorkFlow("Study")
    wf.add_action(MathEvaluation("energy", "K * T"))
    itr = SimulationIterator(wf, work_area_path=str(study_path))
    itr.solve({'K': 0.2, 'T': 75})
    with pytest.raises(KuneneError):
        itr.solve({'K': 0.3})                    # no T: the graph fails
    itr.solve({'K': 0.4, 'T': 75})

    # job_1 failed and has no outputs; it is skipped, not raised on
    assert [p.name for p in itr.iterdir()] == ['job_0', 'job_1', 'job_2']
    assert [o['energy'] for o in itr.gather_outputs()] == pytest.approx([15.0, 30.0])


def test_failed_job_is_not_reused(study_path):
    wf = WorkFlow("Study")
    wf.add_action(MathEvaluation("energy", "K * missing_name"))
    itr = SimulationIterator(wf, work_area_path=str(study_path))
    with pytest.raises(KuneneError):
        itr.solve({'K': 0.2})

    rec = itr.job_index().find(state=None)[0]
    assert rec['state'] == 'failed'
    assert rec['variables'] == {'K': 0.2}
    # retrieval only offers completed jobs
    assert itr.find_jobs(where={'K': 0.2}) == []
    with pytest.raises(DataNotFoundError):
        itr.results_for({'K': 0.2})


# ----------------------------------------------------------------------
# the job directory prefix

def test_jname_changed_on_the_instance(study_path):
    """``itr.JNAME = 'design_'`` must rename the job directories and keep
    numbering them.

    Regression test: the job index was created with the prefix as it was in
    __init__, so a later change left it looking for 'job_'. It then
    recognised none of the directories it wrote, handed out job number 0
    every time, and each run silently overwrote the one before.
    """
    itr = _study_iterator(study_path)
    itr.JNAME = 'design_'
    itr.solve({'K': 0.2, 'T': 75})
    itr.solve({'K': 0.3, 'T': 75})
    itr.solve({'K': 0.4, 'T': 75})

    assert [p.name for p in itr.iterdir()] == ['design_0', 'design_1', 'design_2']
    assert [r['job'] for r in itr.job_index().records] == [
        'design_0', 'design_1', 'design_2' ]
    # every run is still there and still retrievable
    assert itr.results_for({'K': 0.3, 'T': 75})['energy'] == pytest.approx(22.5)


def test_jname_changed_in_a_subclass(study_path):
    class DesignIterator(SimulationIterator):
        JNAME = 'design_'

    wf = WorkFlow("Study")
    wf.add_action(MathEvaluation("energy", "K * T"))
    itr = DesignIterator(wf, work_area_path=str(study_path))
    itr.solve({'K': 0.2, 'T': 75})
    itr.solve({'K': 0.3, 'T': 75})

    assert [p.name for p in itr.iterdir()] == ['design_0', 'design_1']


def test_jname_default_is_unchanged(study_path):
    itr = _study_iterator(study_path)
    itr.solve({'K': 0.2, 'T': 75})
    itr.solve({'K': 0.3, 'T': 75})
    assert [p.name for p in itr.iterdir()] == ['job_0', 'job_1']


def test_renamed_jobs_are_added_to_in_a_later_session(study_path):
    """A second session with the same prefix continues the numbering
    rather than writing over what is already there."""
    first = _study_iterator(study_path)
    first.JNAME = 'design_'
    first.solve({'K': 0.2, 'T': 75})

    later = _study_iterator(study_path)
    later.JNAME = 'design_'
    later.solve({'K': 0.3, 'T': 75})

    assert [p.name for p in later.iterdir()] == ['design_0', 'design_1']
    assert later.results_for({'K': 0.2, 'T': 75})['energy'] == pytest.approx(15.0)


if __name__ == "__main__":
    # If run directly, just run some examples
    pytest.main([__file__])
