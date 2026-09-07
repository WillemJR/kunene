
from pathlib import Path

import kunene.variables
from kunene.jinja_actions import JinjaReplace
from kunene.graph_actions import WorkFlow, SimulationIterator, DirectedGraph
from kunene.dyna_actions import DynaAnalysis
from kunene.radioss_actions import RadiossAnalysis

input_path = Path(__file__).parent.parent / "tests" / "spring.k"
radioss_starter = Path(__file__).parent.parent / "models" / "cube_TYPE7_0000.rad"

def test_float():
    fv = kunene.variables.FloatVariable( 'F', 1.0 )
    fv.value = 1.23

    fv = kunene.variables.FloatVariable( 'F', 4.0, upper_bound=5., lower_bound=4. )
    fv.value = 4.3

def test_intset():
    iv = kunene.variables.IntSetVariable( 'I', 1, [1,2,7] )

    iv.value = 7

def test_strset():
    iv = kunene.variables.StrSetVariable( 'I', 'foo', ['foo','fam'] )

    iv.value = 'fam'

def print_varlist( vl ):
    for v in vl:
        print( '\t', v)

def test_call():

    wf = WorkFlow("SpringWorkFlow")
    
    run_dyna = DynaAnalysis("RunSpring", input_path=str(input_path))
    wf.add_action(run_dyna)

    print( 'dyna vars', run_dyna.parameters() )
    print( 'dyna vars', next(iter(run_dyna.parameters())) )
    print( 'workflow vars' )
    print_varlist( wf.parameters() )

    jj =  JinjaReplace( name='SetVars', input_file_path='tests/par_tens.k' )
    wf.add_action( jj )

    print( 'JinjaReplace vars:', jj.parameters() )
    print( 'workflow vars:' )
    print_varlist( wf.parameters() )

    # Graph
    graph = DirectedGraph( 'aGraph' )
    graph.add_action( run_dyna )
    graph.add_action( jj )
    print( 'graph vars' )
    print_varlist( graph.parameters() )

    # SimulationIterator
    itr = SimulationIterator( graph, copy_paths=[input_path]  )
    print( 'SimulationIterator vars' )
    print_varlist( itr.parameters() )

    itr.rm_rundir() 

if __name__ == "__main__":
    #test_float()
    #test_intset()
    #test_strset()
    test_call()


def test_discovered_variables_say_where_they_came_from():
    """Every solver action records the file a variable was read from.

    Regression test: RadiossAnalysis.parameters() built its variables
    without a description, so Variable fell back to its class docstring and
    anything printing the discovered variables showed that docstring
    instead of the source file.
    """
    dyna = DynaAnalysis( 'RunSpring', input_path=str(input_path) )
    for v in dyna.parameters():
        assert v.description == f"From '{input_path}'"

    if not radioss_starter.exists():          # deck not in this checkout
        return
    rad = RadiossAnalysis( name='rad',
                           starter_input_path=str(radioss_starter),
                           engine_input_path='unused_0001.rad' )
    # parameters() reads the deck in the current directory
    import os
    cwd = os.getcwd()
    os.chdir( radioss_starter.parent )
    try:
        variables = rad.parameters()
    finally:
        os.chdir( cwd )

    assert variables, 'the deck defines /PARAMETER E'
    for v in variables:
        assert v.description == f"From '{radioss_starter.name}'"
        assert 'Arguments:' not in v.description     # not the class docstring
