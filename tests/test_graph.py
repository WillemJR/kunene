
from pathlib import Path

import kunene
import kunene.radioss_actions
import kunene.jinja_actions
from kunene.variables import FloatVariable
from kunene.graph_actions import DirectedGraph, WorkFlow, WorkArea, SimulationIterator
from kunene.actions import WorkAction, MathEvaluation


input_path = Path(__file__).parent.parent / "tests" / "spring.k"


# Example subclass of WorkAction
class ExampleNode(WorkAction):
    def solve(self, val_dict=None):
        # Example solve logic
        print( 'ExampleNode.solve', self.name, val_dict)
        input_val = val_dict.get(self.name, 0) if val_dict else 0
        return input_val + 1
import time
class ExampleNodeLong(WorkAction):
    def solve(self, val_dict=None):
        # Example solve logic
        print( 'vd', self.name, val_dict)
        input_val = val_dict.get(self.name, 0) if val_dict else 0
        time.sleep(3)
        return input_val + 1


def test_chain(): 
    # Create nodes
    node_a = ExampleNode("S1")
    node_b = ExampleNode("B")
    node_c = ExampleNode("C")

    # Create graph and add nodes
    graph = WorkFlow( 'WF', actions = [ node_a, node_b, node_c ] )

    # Evaluate starting from node A
    #result = graph.solve( {"S1": 5,'E':3 })
    result = graph.solve( {"V1": 5,'V2':3 })
    print("Final result:", result)

    assert result == {'V1': 5, 'V2': 3, 'S1': 1, 'B': 1, 'C': 1}

def test_area(): 
    input_path = Path(__file__).parent.parent / "tests" / "spring.k"

    head_a = ExampleNode("Head")
    node_b11 = ExampleNode("Branch_11")
    tail_a = ExampleNode("Tail")

    graph = DirectedGraph( 'aGraph' )
    graph.add_action( head_a )
    graph.add_action( node_b11, [head_a] )
    graph.add_action( tail_a, [node_b11 ] )

    area = WorkArea( graph, copy_paths=[input_path] )

    result = area.solve( {"V1": 5, "Unused": -1} )

    print("Final result:", result)

    #assert result == {'Head': 6, 'Unused': -1, 'Branch_11': 1, 'Branch_12': 1, 'Branch_21': 1, 'Tail': 1}
    
    area.rm_rundir()

def test_iter(): 
    head_a = ExampleNode("Head")
    node_b11 = ExampleNode("Branch_11")
    tail_a = ExampleNode("Tail")

    graph = DirectedGraph( 'aGraph' )
    graph.add_action( head_a )
    graph.add_action( node_b11, [head_a] )
    graph.add_action( tail_a, [node_b11 ] )

    itr = SimulationIterator( graph, copy_paths=[input_path]  )

    result = itr.solve( {"V1": 5, "Unused": -1} )
    print("Result:", result)
    assert result ==  {'V1': 5, 'Unused': -1, 'Head': 1, 'Branch_11': 1, 'Tail': 1}

    result = itr.solve( {"V1": 4, "Unused": -1} )
    print("Result:", result)
    assert result == {'V1': 4, 'Unused': -1, 'Head': 1, 'Branch_11': 1, 'Tail': 1}
    
    itr.rm_rundir()


def test_split_stream(): 
    head_a = ExampleNodeLong("Head")
    node_b11 = ExampleNodeLong("Branch_11")
    node_b12 = ExampleNode("Branch_12")
    node_b2 = ExampleNode("Branch_21")
    tail_a = ExampleNode("Tail")

    graph = DirectedGraph( 'aGraph' )
    graph.add_action( head_a )
    graph.add_action( node_b11, [head_a] )
    graph.add_action( node_b12, [node_b11] )
    graph.add_action( node_b2, [head_a] )
    graph.add_action( tail_a, [node_b12, node_b2] )

    result = graph.solve( {"V1": 5, "Unused": -1} )

    print("Final result:", result)

    assert result == {'V1': 5, 'Unused': -1, 'Head': 1, 'Branch_11': 1, 'Branch_12': 1, 'Branch_21': 1, 'Tail': 1}

# Example usage
def test_graph(): 
    """
    S1     S2
     |
     B
     | |    
     |  CC
     |   |
     C   D
     |  |
     | |
     E
    """
    # Create nodes
    node_a = ExampleNode("S1")
    node_aa = ExampleNode("S2")
    node_b = ExampleNode("B")
    node_c = ExampleNode("C")
    node_cc = ExampleNode("CC")
    node_d = ExampleNode("D")
    node_e = ExampleNode("E")

    # Create graph and add nodes
    graph = DirectedGraph( 'aGraph' )
    graph.add_action(node_a)
    graph.add_action(node_e)
    graph.add_action(node_b)
    graph.add_action(node_d)
    graph.add_action(node_c)
    graph.add_action(node_cc)
    graph.add_action(node_aa)


    #Add edges
    graph.add_edge(node_a, node_b)
    graph.add_edge(node_cc, node_d)
    graph.add_edge(node_d, node_e)
    graph.add_edge(node_c, node_e)
    graph.add_edge(node_b, node_c)
    graph.add_edge(node_b, node_cc)
    graph.add_edge(node_b, node_c)

    # Evaluate starting from node A
    result = graph.solve( {"H": 1})
    print("Final result:", result)

    assert result == {'H': 1, 'S1': 1, 'B': 1, 'CC': 1, 'D': 1, 'C': 1, 'E': 1, 'S2': 1}
    assert node_e.results() == {'H': 1, 'S1': 1, 'B': 1, 'CC': 1, 'D': 1, 'C': 1, 'E': 1}
    assert node_aa.results() == {'H': 1, 'S2': 1}




def test_mdo(): 
    """ MDO has an iterator with multiple workareas
           head
        are1 area2
           tail
    """

    head_a = ExampleNode("Head")
    node_b11 = ExampleNode("Branch_11")
    node_b22 = ExampleNode("Branch_22")
    tail_a = ExampleNode("Tail")

    graph_main = DirectedGraph( 'Main' )

    graph_main.add_action( head_a )

    graph1 = DirectedGraph( 'SOLVER_1' )
    graph1.add_action( node_b11 )
    area1 = WorkArea( graph1, copy_paths=[input_path] )

    graph_main.add_action( area1, [head_a ] )

    graph2 = DirectedGraph( 'SOLVER_2' )
    graph2.add_action( node_b22 )
    area2 = WorkArea( graph2, copy_paths=[input_path] )

    graph_main.add_action( area2, [head_a ] )

    graph_main.add_action( tail_a, [area1, area2 ] )

    itr = SimulationIterator( graph_main, clean_start=True )

    result = itr.solve( {"V1": 5, "Unused": -1} )
    print("Result:", result)
    #assert result ==  {'Head': 6, 'Unused': -1, 'Branch_11': 1, 'Tail': 1}

    # TODO need to check duplicate names, duplicate nodes

    itr.rm_rundir()
    area1.rm_rundir()
    area2.rm_rundir()


def test_workarea_nested_in_iterator():
    """A WorkArea nested inside a SimulationIterator must create its
    directory *inside* the per-design job_N directory, not next to it.

    Regression test: the work-area path used to be baked in as an absolute
    path at construction time, so the WorkArea was created at the original
    cwd instead of inside the iterator's job directory.
    """
    root = Path.cwd()
    results_dir = root / 'IterResults'
    stray_dir = root / 'InnerSolver'   # the old, buggy location

    # Make sure a previous run cannot mask the result.
    import shutil
    if results_dir.exists(): shutil.rmtree(results_dir)
    if stray_dir.exists():   shutil.rmtree(stray_dir)

    #inner = DirectedGraph('InnerSolver')
    inner = WorkFlow('InnerSolver')
    inner.add_action( ExampleNode('Inner_ExaNode1') )
    inner.add_action( ExampleNode('Inner_ExaNode2') )
    area = WorkArea( inner )            # default (relative) work-area path

    outer = DirectedGraph('Outer')
    outer.add_action( area )
    outer.add_action( ExampleNode('Outer_ExaNode'), parents=[area] )

    itr = SimulationIterator( outer, work_area_path=results_dir, clean_start=True )

    # Print the action graph and the predicted work-directory structure.
    print()
    itr.describe_workflow()

    s = itr.solve( {"V1": 5} )
    print( 'Results:', s )

    # Results stay structured: the WorkArea's inner graph outputs are nested
    # under its name. (A MathEvaluation would flatten this view for its eval;
    # a plain ExampleNode does not need the inner values.)
    assert s == {'V1': 5,
                 'InnerSolver_WorkArea': {'V1': 5, 'Inner_ExaNode1': 1, 'Inner_ExaNode2': 1},
                 'Outer_ExaNode': 1}

    job0 = results_dir / 'job_0'
    assert job0.is_dir()
    # The work area lives *inside* the job directory ...
    assert (job0 / 'InnerSolver').is_dir()
    # ... and NOT next to the results directory (the old buggy location).
    assert not stray_dir.exists()

    itr.rm_rundir()


def test_workarea_nested_in_workarea():
    """A WorkArea whose graph contains another WorkArea must nest the inner
    work-area directory inside the outer one.

    Same regression as the iterator case: with paths baked in at construction
    time the inner WorkArea landed next to the outer one instead of inside it.
    """
    root = Path.cwd()
    outer_dir = root / 'OuterSolver'
    stray_dir = root / 'InnerSolver'   # the old, buggy location

    import shutil
    if outer_dir.exists(): shutil.rmtree(outer_dir)
    if stray_dir.exists(): shutil.rmtree(stray_dir)

    inner = DirectedGraph('InnerSolver')
    inner.add_action( ExampleNode('Inner_ExaNode') )
    inner_area = WorkArea( inner )     # default (relative) work-area path

    outer = DirectedGraph('OuterSolver')
    outer.add_action( inner_area )
    outer.add_action( ExampleNode('Outer_ExaNode'), parents=[inner_area] )
    outer_area = WorkArea( outer )     # default (relative) work-area path

    # Print the action graph and the predicted work-directory structure.
    print()
    outer_area.describe_workflow()

    s = outer_area.solve( {"V1": 5} )
    print( 'Results:', s )

    # Results stay structured: the inner WorkArea's outputs are nested under
    # its name rather than flattened into the outer result.
    assert s == {'V1': 5,
                 'InnerSolver_WorkArea': {'V1': 5, 'Inner_ExaNode': 1},
                 'Outer_ExaNode': 1}

    assert outer_dir.is_dir()
    # The inner work area lives *inside* the outer one ...
    assert (outer_dir / 'InnerSolver').is_dir()
    # ... and NOT next to the outer area (the old buggy location).
    assert not stray_dir.exists()

    outer_area.rm_rundir()


def test_invalid_action_names():
    """Action names must be valid Python identifiers so they can be used
    in MathEvaluation's eval(). Names with spaces, other punctuation, a
    leading digit, or Python keywords are rejected at construction."""
    import pytest
    from kunene.errors import ActionNameError

    # A space breaks the eval command (the example from the request).
    with pytest.raises(ActionNameError):
        ExampleNode('m__case_1__TE all')

    for bad in ['has space', 'has-dash', '1leading', 'a.b', 'class', '']:
        with pytest.raises(ActionNameError):
            ExampleNode(bad)

    # Valid identifiers are accepted.
    for ok in ['m__case_1__TE_all', 'Head', 'Branch_11', '_private']:
        assert ExampleNode(ok).name == ok


def test_matheval_flattens_nested_workarea():
    """A MathEvaluation can reference an action that lives inside a WorkArea
    by its name: results stay structured (nested), but MathEvaluation
    flattens its eval namespace so nested action outputs resolve by name."""
    root = Path.cwd()
    if (root / 'InnerSolver').exists():
        import shutil; shutil.rmtree(root / 'InnerSolver')

    inner = WorkFlow('InnerSolver')
    inner.add_action( ExampleNode('Inner_ExaNode') )    # returns 1
    area = WorkArea( inner )

    outer = DirectedGraph('Outer')
    outer.add_action( area )
    # References 'Inner_ExaNode', which is nested under the WorkArea's result.
    outer.add_action( MathEvaluation('metric', 'Inner_ExaNode + 10'), parents=[area] )

    s = outer.solve( {"V1": 5} )
    print( 'Results:', s )

    assert s['metric'] == 11
    # The WorkArea's outputs remain nested (not flattened into the top level).
    assert s['InnerSolver_WorkArea'] == {'V1': 5, 'Inner_ExaNode': 1}
    assert 'Inner_ExaNode' not in s

    area.rm_rundir()


if __name__ == "__main__":
    #test_workarea_nested_in_iterator()
    test_workarea_nested_in_workarea()
    #test_mdo()
    #test_split_stream()
    #test_area()
    #test_iter()
    #test_graph()
    #test_chain()
