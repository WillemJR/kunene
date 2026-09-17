import json
from enum import Enum

import numpy as np
import pytest

from kunene import Cleanup, action_spec, load_workflow, save_workflow
from kunene import serialization
from kunene.actions import MathEvaluation, WorkAction
from kunene.errors import SpecError
from kunene.graph_actions import DirectedGraph, WorkArea, WorkFlow
from kunene.simulation_iterator import SimulationIterator
from kunene.variables import FloatVariable, IntSetVariable


def make_graph():
    g = DirectedGraph('crash', asynch=True)
    a = g.add_action(MathEvaluation('a', 'K*2'))
    g.add_action(MathEvaluation('b', 'a+T'), parents=[a])
    return g


# ------------------------------------------------------------ one action


def test_leaf_action_records_its_arguments():
    m = MathEvaluation('m', 'a+b')
    assert m.to_spec() == {'type': 'MathEvaluation', 'name': 'm',
                           'args': {'cmd': 'a+b'}}


def test_leaf_action_roundtrip_solves_the_same():
    m = MathEvaluation('m', 'a+b')
    m2 = MathEvaluation.from_spec(m.to_spec())
    assert m2.name == m.name
    assert m2.solve({'a': 1, 'b': 2}) == 3


def test_only_arguments_actually_passed_are_recorded():
    # defaults stay out of the file, so a spec is short and picks up a
    # later change to a default
    assert 'copy_paths' not in MathEvaluation('m', 'x').to_spec()['args']


def test_every_action_class_is_registered():
    assert WorkAction._registry['MathEvaluation'] is MathEvaluation
    assert WorkAction._registry['DirectedGraph'] is DirectedGraph


# ------------------------------------------------------------ containers


def test_graph_roundtrip_keeps_structure_and_edges(tmp_path):
    g = make_graph()
    g2 = load_workflow(save_workflow(g, tmp_path / 'w.json'))
    assert g2.format_tree() == g.format_tree()
    assert g2.parent_list['b'] == ['a']
    assert g2.asynch is True
    assert g2.solve({'K': 0.2, 'T': 75}) == g.solve({'K': 0.2, 'T': 75})


def test_workflow_roundtrip_keeps_the_chain(tmp_path):
    f = WorkFlow('flow')
    f.add_action(MathEvaluation('x', '1+1'))
    f.add_action(MathEvaluation('y', 'x*3'))
    f2 = load_workflow(save_workflow(f, tmp_path / 'w.json'))
    assert [a.name for a in f2.sequence] == ['x', 'y']
    assert f2.solve({}) == {'x': 2, 'y': 6}


def test_work_area_roundtrip(tmp_path):
    wa = WorkArea(make_graph(), work_area_path='./run1',
                  cleanup=Cleanup(keep=['d3plot']))
    wa2 = load_workflow(save_workflow(wa, tmp_path / 'w.json'))
    assert wa2.format_tree() == wa.format_tree()
    assert str(wa2.work_area_path) == 'run1'
    assert wa2.cleanup.keep == ['d3plot']


def test_iterator_roundtrip(tmp_path):
    itr = SimulationIterator(make_graph(),
                             parameter_list=[FloatVariable('K', 0.2, lower_bound=0.0),
                                             FloatVariable('T', 75)],
                             max_workers=3, reuse_existing=True,
                             work_area_path=tmp_path / 'study')
    itr2 = load_workflow(save_workflow(itr, tmp_path / 'w.json'))
    assert itr2.format_tree() == itr.format_tree()
    assert itr2.max_workers == 3
    assert itr2.reuse_existing is True
    assert [v.name for v in itr2.parameter_list] == ['K', 'T']
    assert itr2.parameter_list[0].lower_bound == 0.0


def test_iterator_keeps_a_changed_job_prefix(tmp_path):
    itr = SimulationIterator(make_graph(), work_area_path=tmp_path / 'study')
    itr.JNAME = 'design_'
    itr2 = load_workflow(save_workflow(itr, tmp_path / 'w.json'))
    assert itr2.JNAME == 'design_'
    assert itr2._index.job_prefix == 'design_'


def test_clean_start_is_not_saved(tmp_path):
    # loading a spec must never be what deletes a results directory
    itr = SimulationIterator(make_graph(), work_area_path=tmp_path / 'study',
                             clean_start=True)
    assert 'clean_start' not in itr.to_spec()['args']


def test_repeated_roundtrips_do_not_grow_derived_names(tmp_path):
    # WorkArea/SimulationIterator derive their name from the graph; keeping
    # it in the spec would add a suffix on every save
    wa = WorkArea(make_graph())
    path = tmp_path / 'w.json'
    for _ in range(3):
        wa = load_workflow(save_workflow(wa, path))
    assert wa.name == 'crash_WorkArea'


def test_nested_containers_roundtrip(tmp_path):
    inner = WorkFlow('inner')
    inner.add_action(MathEvaluation('x', 'K+1'))
    outer = DirectedGraph('outer')
    outer.add_action(WorkArea(inner, work_area_path='./inner_run'))
    itr = SimulationIterator(outer, work_area_path=tmp_path / 'study')
    itr2 = load_workflow(save_workflow(itr, tmp_path / 'w.json'))
    assert itr2.format_tree() == itr.format_tree()
    assert itr2.format_work_dir() == itr.format_work_dir()


# ------------------------------------------------------------ arg values


def test_variables_survive_as_variables():
    # the spec must keep the Variable, not the value _collect_arg_pars
    # substituted for it
    class Scaled(MathEvaluation):
        @MathEvaluation.allow_variables_as_arguments
        def __init__(self, name, factor=None):
            super().__init__(name, 'x')
            self.factor = factor

    s = Scaled('s', factor=FloatVariable('F', 3.0))
    assert s.factor == 3.0                      # value substituted on self
    s2 = Scaled.from_spec(s.to_spec())          # Variable kept in the spec
    assert s2._par_dict['factor'].name == 'F'


def test_set_valued_variable_roundtrip():
    v = IntSetVariable('n', 2, allowable=[1, 2, 3])
    v2 = action_spec.decode_args(action_spec.encode_args({'v': v}))['v']
    assert v2.allowable == {1, 2, 3}
    assert v2.value == 2


def test_cleanup_and_enums_roundtrip():
    from kunene.args import EvalType
    args = {'cleanup': Cleanup(remove=['*.d3plot'], dry_run=True),
            'data_type': EvalType.FLOAT | EvalType.NUMERICAL}
    out = action_spec.decode_args(action_spec.encode_args(args))
    assert out['cleanup'].remove == ['*.d3plot']
    assert out['cleanup'].dry_run is True
    assert out['data_type'] == EvalType.FLOAT | EvalType.NUMERICAL


def test_variable_description_is_not_written_when_it_is_the_docstring():
    spec = action_spec.encode_args({'v': FloatVariable('F', 1.0)})
    assert 'description' not in spec['v']['args']


def test_spec_is_plain_json(tmp_path):
    path = save_workflow(make_graph(), tmp_path / 'w.json')
    doc = json.loads(path.read_text())
    assert doc['kunene_spec'] == action_spec.SPEC_VERSION
    assert doc['workflow']['name'] == 'crash'


# --------------------------------------------------------------- failures


def test_unspecable_argument_is_reported_by_name():
    m = MathEvaluation('m', 'x')
    m._init_args['handle'] = object()
    with pytest.raises(SpecError, match='m.handle'):
        m.to_spec()


def test_unknown_action_type_names_itself():
    with pytest.raises(SpecError, match="'NoSuchAction'"):
        action_spec.action_from_spec({'type': 'NoSuchAction', 'name': 'n'})


def test_future_spec_version_is_refused(tmp_path):
    path = tmp_path / 'w.json'
    save_workflow(make_graph(), path)
    doc = json.loads(path.read_text())
    doc['kunene_spec'] = action_spec.SPEC_VERSION + 1
    path.write_text(json.dumps(doc))
    with pytest.raises(SpecError, match='cannot read'):
        load_workflow(path)


def test_a_file_that_is_not_a_spec_is_refused(tmp_path):
    path = tmp_path / 'other.json'
    path.write_text('{"something": 1}')
    with pytest.raises(SpecError, match='not a kunene workflow spec'):
        load_workflow(path)


def test_invalid_json_is_refused(tmp_path):
    path = tmp_path / 'bad.json'
    path.write_text('{not json')
    with pytest.raises(SpecError, match='not valid JSON'):
        load_workflow(path)


def test_reserved_key_in_a_dict_argument_is_refused():
    with pytest.raises(SpecError, match='reserved'):
        action_spec.encode_args({'d': {action_spec._TAG: 1}})


# ------------------------------------------------ arrays and foreign enums


def test_numpy_array_argument_comes_back_as_an_array():
    # an experimental curve handed to an action as a constructor argument:
    # written by serialization._encode, so it has to be read back by its
    # counterpart rather than left as the tagged dict
    curve = np.array([[0.0, 1.0, 2.0], [0.0, 3.4, 5.6]])
    out = action_spec.decode_args(action_spec.encode_args({'exp': curve}))
    assert isinstance(out['exp'], np.ndarray)
    assert out['exp'].ndim == 2
    assert np.array_equal(out['exp'], curve)


def test_arrays_nested_in_containers_come_back_as_arrays():
    args = {'curves': {'a': [np.arange(3), 2.0]}}
    out = action_spec.decode_args(action_spec.encode_args(args))
    assert isinstance(out['curves']['a'][0], np.ndarray)
    assert out['curves']['a'][1] == 2.0


class CurveCompare(MathEvaluation):
    """Stands in for an action that takes an experimental curve, the way
    CurveSimilarity does."""

    def __init__(self, name, experiment=None):
        super().__init__(name, 'x')
        self.experiment = experiment


def test_array_argument_survives_a_file_roundtrip(tmp_path):
    curve = np.linspace(0.0, 1.0, 5)
    g = DirectedGraph('g')
    g.add_action(CurveCompare('cmp', experiment=curve))
    g2 = load_workflow(save_workflow(g, tmp_path / 'w.json'))
    assert np.array_equal(g2.get_action('cmp').experiment, curve)


def test_a_malformed_array_encoding_is_refused():
    with pytest.raises(SpecError, match='encoded array'):
        action_spec.decode_args({'a': {serialization.NDARRAY_TAG: {}, 'x': 1}})


def test_the_array_tag_is_reserved_as_a_dict_key():
    with pytest.raises(SpecError, match='reserved'):
        action_spec.encode_args({'d': {serialization.NDARRAY_TAG: 1}})


class _Colour(Enum):
    RED = 'red'


def test_an_unregistered_enum_says_how_to_register_it():
    with pytest.raises(SpecError, match='register_enum'):
        action_spec.encode_args({'c': _Colour.RED})


def test_a_registered_enum_roundtrips():
    action_spec.register_enum(_Colour)
    try:
        out = action_spec.decode_args(action_spec.encode_args({'c': _Colour.RED}))
        assert out['c'] is _Colour.RED
    finally:
        action_spec._ENUMS.pop('_Colour', None)


def test_registering_a_name_twice_is_refused():
    class _Colour(Enum):            # a different class, same name
        BLUE = 'blue'
    action_spec.register_enum(globals()['_Colour'])
    try:
        with pytest.raises(SpecError, match='already registered'):
            action_spec.register_enum(_Colour)
    finally:
        action_spec._ENUMS.pop('_Colour', None)


def test_lasso_filter_type_is_registered():
    # d3plot actions take element_type as a lasso.dyna.FilterType
    lasso_dyna = pytest.importorskip('lasso.dyna')
    ft = lasso_dyna.FilterType
    out = action_spec.decode_args(action_spec.encode_args({'element_type': ft.SHELL}))
    assert out['element_type'] is ft.SHELL


def test_kunene_enums_still_roundtrip_by_name():
    from kunene.args import EvalType
    out = action_spec.decode_args(action_spec.encode_args({'t': EvalType.IMAGE}))
    assert out['t'] is EvalType.IMAGE


# ---------------------------------------------------------- live attributes


class SelfBounded(MathEvaluation):
    """Imposes its own bounds without taking them as arguments."""

    def __init__(self, name, cmd):
        super().__init__(name, cmd, lower_bound=0.0, upper_bound=1.0)


def test_bounds_are_not_written_when_there_are_none():
    assert MathEvaluation('m', 'x').to_spec()['args'] == {'cmd': 'x'}


def test_bounds_given_to_the_constructor_are_written():
    m = MathEvaluation('m', 'x', lower_bound=0.0, upper_bound=10.0)
    assert m.to_spec()['args']['lower_bound'] == 0.0
    assert m.to_spec()['args']['upper_bound'] == 10.0


def test_a_bound_set_after_construction_is_written():
    # what a GUI does when a user edits a node's bounds
    m = MathEvaluation('m', 'x')
    m.lower_bound = 3.0
    assert m.to_spec()['args']['lower_bound'] == 3.0


def test_a_bound_edited_after_construction_overrides_the_recorded_one():
    m = MathEvaluation('m', 'x', upper_bound=10.0)
    m.upper_bound = 99.0
    assert m.to_spec()['args']['upper_bound'] == 99.0


def test_a_bound_cleared_after_construction_is_dropped():
    m = MathEvaluation('m', 'x', lower_bound=0.0)
    m.lower_bound = None
    assert 'lower_bound' not in m.to_spec()['args']


def test_bounds_survive_a_file_roundtrip(tmp_path):
    g = DirectedGraph('g')
    n = g.add_action(MathEvaluation('n', 'x'))
    n.lower_bound, n.upper_bound = -1.0, 1.0
    g2 = load_workflow(save_workflow(g, tmp_path / 'w.json'))
    assert g2.get_action('n').lower_bound == -1.0
    assert g2.get_action('n').upper_bound == 1.0


def test_bounds_a_class_imposes_on_itself_stay_out_of_the_file():
    # the constructor cannot take them back, and re-running it restores them
    b = SelfBounded('b', 'x')
    spec = b.to_spec()
    assert 'lower_bound' not in spec['args']
    restored = SelfBounded.from_spec(spec)
    assert (restored.lower_bound, restored.upper_bound) == (0.0, 1.0)


def test_only_the_bound_the_class_exposes_is_written():
    class HalfExposed(MathEvaluation):
        def __init__(self, name, cmd, lower_bound=None):
            super().__init__(name, cmd, lower_bound=lower_bound)

    h = HalfExposed('h', 'x')
    h.lower_bound, h.upper_bound = 1.0, 5.0
    args = h.to_spec()['args']
    assert args['lower_bound'] == 1.0
    assert 'upper_bound' not in args        # from_spec could not pass it


def test_copy_paths_is_not_replayed_from_the_instance():
    # a graph extends a child's copy_paths in add_action; replaying the live
    # value would double the entries on every round trip
    g = DirectedGraph('g')
    g.add_action(MathEvaluation('m', 'x', copy_paths=['a.k']))
    assert 'copy_paths' not in g.to_spec()['args']
