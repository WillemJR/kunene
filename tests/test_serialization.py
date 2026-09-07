
import pickle

import numpy as np
import pytest

from kunene import serialization
from kunene.errors import SerializationError


def test_roundtrip_scalars_and_containers():
    val = {
        'K': 0.2,
        'T': 75,
        'label': 'case_1',
        'flag': True,
        'nothing': None,
        'hist': [1.0, 2.0, 3.5],
        'nested': {'inner_action': 4.2, 'deeper': {'x': 1}},
    }
    assert serialization.loads(serialization.dumps(val)) == val


def test_roundtrip_numpy_arrays():
    val = {
        'time_hist': np.linspace(0.0, 1.0, 11),
        'image': np.arange(12, dtype=np.uint8).reshape(3, 4),
        'mask': np.array([True, False, True]),
    }
    out = serialization.loads(serialization.dumps(val))
    for k in val:
        assert isinstance(out[k], np.ndarray)
        assert out[k].dtype == val[k].dtype
        assert out[k].shape == val[k].shape
        assert np.array_equal(out[k], val[k])
    # decoded arrays must be writable (frombuffer views are not)
    out['image'][0, 0] = 99


def test_numpy_scalars_decode_as_python_types():
    val = {'a': np.float64(1.5), 'b': np.int32(7), 'c': np.bool_(True)}
    out = serialization.loads(serialization.dumps(val))
    assert out == {'a': 1.5, 'b': 7, 'c': True}
    assert type(out['a']) is float and type(out['b']) is int and type(out['c']) is bool


def test_tuples_decode_as_lists():
    out = serialization.loads(serialization.dumps({'pair': (1, 2.5)}))
    assert out == {'pair': [1, 2.5]}


def test_rejects_arbitrary_objects():
    class Payload:
        pass
    with pytest.raises(SerializationError):
        serialization.dumps({'obj': Payload()})
    with pytest.raises(SerializationError):
        serialization.dumps({'s': {1, 2}})


def test_rejects_non_string_dict_keys_and_reserved_key():
    with pytest.raises(SerializationError):
        serialization.dumps({1: 'a'})
    with pytest.raises(SerializationError):
        serialization.dumps({serialization.NDARRAY_TAG: 'a'})


def test_rejects_object_dtype_array():
    arr = np.array([object()], dtype=object)
    with pytest.raises(SerializationError):
        serialization.dumps({'a': arr})


def test_rejects_disallowed_dtype_on_decode():
    # a hand-crafted payload declaring an object dtype must be refused
    import json
    payload = json.dumps({'a': {serialization.NDARRAY_TAG: {
        'dtype': '|O', 'shape': [1], 'data': ''}}}).encode()
    with pytest.raises(SerializationError):
        serialization.loads(payload)


def test_rejects_pickle_payload():
    data = pickle.dumps({'K': 0.2})
    with pytest.raises(SerializationError):
        serialization.loads(data)


if __name__ == '__main__':
    test_roundtrip_scalars_and_containers()
    test_roundtrip_numpy_arrays()
    test_numpy_scalars_decode_as_python_types()
    test_tuples_decode_as_lists()
    test_rejects_arbitrary_objects()
    test_rejects_non_string_dict_keys_and_reserved_key()
    test_rejects_object_dtype_array()
    test_rejects_disallowed_dtype_on_decode()
    test_rejects_pickle_payload()
    print('all serialization tests passed')
