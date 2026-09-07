"""Removing bulk solver output from run directories after a run."""

from pathlib import Path
import pytest

from kunene.args import Cleanup
from kunene.actions import WorkAction, MathEvaluation
from kunene.graph_actions import WorkFlow, WorkArea
from kunene.simulation_iterator import SimulationIterator
from kunene.errors import SolverError


class FakeSolver( WorkAction ):
    """Writes the files a solver writes: a deck, a log, and a bulky plot
    database that is what a study fills its disk with."""

    def __init__( self, name, keep=None, fail=False ):
        super().__init__( name, keep=keep )
        self.fail = fail

    def solve( self, val_dict=None ):
        Path( 'deck.k' ).write_text( 'deck' )
        Path( 'run.stdout' ).write_text( 'log' )
        for suffix in ( '', '01', '02' ):
            Path( f'd3plot{suffix}' ).write_text( 'x' * 100 )
        Path( 'big.vtk' ).write_text( 'v' * 100 )
        if self.fail:
            raise SolverError( 'solver blew up' )
        return 3.0

    def _produced_files( self ):
        return [ 'deck.k', 'run.stdout', 'd3plot*', 'big.vtk' ]

    def _disposable_files( self ):
        return [ 'd3plot*', 'big.vtk' ]


def _flow( name='Study', keep=None, fail=False ):
    wf = WorkFlow( name )
    wf.add_action( FakeSolver( 'run', keep=keep, fail=fail ) )
    wf.add_action( MathEvaluation( 'energy', 'run * K' ) )
    return wf


def _names( directory ):
    return sorted( p.name for p in Path( directory ).iterdir() )


# ----------------------------------------------------------------------
# SimulationIterator

def test_no_cleanup_by_default( tmp_path ):
    it = SimulationIterator( _flow(), work_area_path=str( tmp_path / 'study' ) )
    it.solve( { 'K': 2.0 } )
    assert 'd3plot' in _names( tmp_path / 'study' / 'job_0' )


def test_bulk_cleanup_keeps_deck_log_and_outputs( tmp_path ):
    it = SimulationIterator( _flow(), work_area_path=str( tmp_path / 'study' ),
                             cleanup=True )
    res = it.solve( { 'K': 2.0 } )
    assert res['energy'] == 6.0

    left = _names( tmp_path / 'study' / 'job_0' )
    assert 'd3plot' not in left and 'd3plot01' not in left and 'big.vtk' not in left
    assert 'deck.k' in left and 'run.stdout' in left
    # what the results are read back with survives
    assert 'actions_output.pkl' in left and 'iter_variables.json' in left


def test_cleanup_runs_for_every_job( tmp_path ):
    it = SimulationIterator( _flow(), work_area_path=str( tmp_path / 'study' ),
                             cleanup=True )
    for k in ( 1.0, 2.0, 3.0 ):
        it.solve( { 'K': k } )
    for job in ( 'job_0', 'job_1', 'job_2' ):
        assert 'd3plot' not in _names( tmp_path / 'study' / job )


def test_keep_wins_over_remove( tmp_path ):
    it = SimulationIterator( _flow(), work_area_path=str( tmp_path / 'study' ),
                             cleanup=Cleanup( keep=['d3plot'] ) )
    it.solve( { 'K': 2.0 } )
    left = _names( tmp_path / 'study' / 'job_0' )
    assert 'd3plot' in left          # the first plot is kept
    assert 'd3plot01' not in left    # the state files are not
    assert 'big.vtk' not in left


def test_keep_declared_on_the_action( tmp_path ):
    it = SimulationIterator( _flow( keep=['big.vtk'] ),
                             work_area_path=str( tmp_path / 'study' ),
                             cleanup=True )
    it.solve( { 'K': 2.0 } )
    left = _names( tmp_path / 'study' / 'job_0' )
    assert 'big.vtk' in left
    assert 'd3plot' not in left


def test_explicit_pattern_list( tmp_path ):
    it = SimulationIterator( _flow(), work_area_path=str( tmp_path / 'study' ),
                             cleanup=Cleanup( remove=['*.vtk'] ) )
    it.solve( { 'K': 2.0 } )
    left = _names( tmp_path / 'study' / 'job_0' )
    assert 'big.vtk' not in left
    assert 'd3plot' in left          # not selected, so not touched


def test_remove_all_spares_the_protected_files( tmp_path ):
    it = SimulationIterator( _flow(), work_area_path=str( tmp_path / 'study' ),
                             cleanup=Cleanup( remove=Cleanup.ALL, keep=['deck.k'] ) )
    it.solve( { 'K': 2.0 } )
    # the run's own record is protected whatever the policy selects
    assert _names( tmp_path / 'study' / 'job_0' ) == [
        'actions_output.pkl', 'deck.k', 'iter_variables.json', 'status.json' ]


def test_failed_job_is_not_cleaned( tmp_path ):
    it = SimulationIterator( _flow( fail=True ), work_area_path=str( tmp_path / 'study' ),
                             cleanup=True )
    with pytest.raises( SolverError ):
        it.solve( { 'K': 2.0 } )
    # the deck and the plot files are what the failure is debugged with
    assert 'd3plot' in _names( tmp_path / 'study' / 'job_0' )


def test_dry_run_deletes_nothing( tmp_path ):
    it = SimulationIterator( _flow(), work_area_path=str( tmp_path / 'study' ),
                             cleanup=Cleanup( dry_run=True ) )
    it.solve( { 'K': 2.0 } )
    assert 'd3plot' in _names( tmp_path / 'study' / 'job_0' )


def test_results_and_reuse_survive_cleanup( tmp_path ):
    path = tmp_path / 'study'
    it = SimulationIterator( _flow(), work_area_path=str( path ), cleanup=True )
    it.solve( { 'K': 2.0 } )

    later = SimulationIterator( _flow(), work_area_path=str( path ),
                                reuse_existing=True )
    assert later.results_for( { 'K': 2.0 } )['energy'] == 6.0
    later.solve( { 'K': 2.0 } )
    assert later.reused_jobs                      # no second job was run
    assert not ( path / 'job_1' ).exists()


# ----------------------------------------------------------------------
# WorkArea, on its own and nested

def test_work_area_cleanup( tmp_path, monkeypatch ):
    monkeypatch.chdir( tmp_path )
    wa = WorkArea( _flow(), cleanup=True )
    wa.solve( { 'K': 2.0 } )
    left = _names( tmp_path / 'Study' )
    assert 'deck.k' in left
    assert 'd3plot' not in left and 'big.vtk' not in left


def test_work_area_without_cleanup_keeps_everything( tmp_path, monkeypatch ):
    monkeypatch.chdir( tmp_path )
    wa = WorkArea( _flow() )
    wa.solve( { 'K': 2.0 } )
    assert 'd3plot' in _names( tmp_path / 'Study' )


def test_nested_work_area_inherits_the_iterators_policy( tmp_path ):
    inner = WorkArea( _flow( 'Inner' ) )
    outer = WorkFlow( 'Outer' )
    outer.add_action( inner )

    it = SimulationIterator( outer, work_area_path=str( tmp_path / 'study' ),
                             cleanup=True )
    it.solve( { 'K': 2.0 } )

    job = tmp_path / 'study' / 'job_0'
    assert 'd3plot' not in _names( job / 'Inner' )   # reached inside the work area
    assert 'deck.k' in _names( job / 'Inner' )


def test_nested_work_area_keeps_its_own_policy( tmp_path ):
    inner = WorkArea( _flow( 'Inner' ), cleanup=Cleanup( keep=['d3plot*'] ) )
    outer = WorkFlow( 'Outer' )
    outer.add_action( inner )

    it = SimulationIterator( outer, work_area_path=str( tmp_path / 'study' ),
                             cleanup=True )
    it.solve( { 'K': 2.0 } )

    left = _names( tmp_path / 'study' / 'job_0' / 'Inner' )
    assert 'd3plot' in left and 'd3plot01' in left
    assert 'big.vtk' not in left


def test_remove_all_does_not_delete_a_nested_work_area( tmp_path ):
    inner = WorkArea( _flow( 'Inner' ) )
    outer = WorkFlow( 'Outer' )
    outer.add_action( inner )

    it = SimulationIterator( outer, work_area_path=str( tmp_path / 'study' ),
                             cleanup=Cleanup( remove=Cleanup.ALL, keep=['deck.k'] ) )
    it.solve( { 'K': 2.0 } )

    inner_dir = tmp_path / 'study' / 'job_0' / 'Inner'
    assert inner_dir.is_dir()
    assert _names( inner_dir ) == [ 'deck.k', 'status.json' ]


# ----------------------------------------------------------------------
# the policy object and the predicted directory listing

def test_cleanup_coerce():
    assert Cleanup.coerce( None ) is None
    assert Cleanup.coerce( False ) is None
    assert Cleanup.coerce( True ).remove == Cleanup.BULK
    assert Cleanup.coerce( ['*.vtk'] ).remove == ['*.vtk']
    # a lone pattern is one pattern, not a mode and not a string to iterate
    assert Cleanup.coerce( '*.vtk' ).remove == ['*.vtk']
    assert Cleanup.coerce( 'bulk' ).remove == Cleanup.BULK
    assert Cleanup.coerce( '*' ).remove == Cleanup.ALL
    policy = Cleanup( keep='d3plot' )
    assert Cleanup.coerce( policy ) is policy
    assert policy.keep == ['d3plot']


def test_cleanup_rejects_nonsense():
    with pytest.raises( TypeError ):
        Cleanup( remove=3 )
    with pytest.raises( ValueError ):
        Cleanup( remove=[] )


def test_work_dir_listing_marks_removed_files( tmp_path ):
    it = SimulationIterator( _flow(), work_area_path=str( tmp_path / 'study' ),
                             cleanup=Cleanup( keep=['d3plot*'] ) )
    tree = it.format_work_dir()
    assert 'big.vtk   (removed by cleanup)' in tree
    assert 'd3plot*\n' in tree or tree.rstrip().endswith( 'd3plot*' )
    assert 'deck.k   (removed by cleanup)' not in tree
    assert 'actions_output.pkl   (removed by cleanup)' not in tree
