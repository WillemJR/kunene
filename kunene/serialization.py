"""
Restricted JSON serialization for remote execution.

Replaces pickle on the gRPC wire (``remote_actions``): unpickling network
data allows arbitrary code execution, whereas decoding this format can only
ever produce plain data. Only a whitelist of types is accepted:

- ``dict`` (string keys only), ``list`` and ``tuple`` (both decode as list)
- ``str``, ``int``, ``float``, ``bool``, ``None``
- numpy scalars (decoded as the matching Python ``int``/``float``/``bool``)
- numpy arrays of numeric or bool dtype, encoded as a tagged JSON object
  with the raw bytes base64-encoded; the dtype is validated against a
  whitelist on decode

Anything else (arbitrary objects, object-dtype arrays, non-string dict
keys) raises SerializationError, naming the offending path.
"""

import base64
import json

import numpy as np

from kunene.errors import SerializationError

# reserved key marking an encoded numpy array
NDARRAY_TAG = '__kunene_ndarray__'

# numpy dtype kinds accepted on encode and decode:
# bool, signed/unsigned int, float, complex
_ALLOWED_DTYPE_KINDS = 'biufc'


def _encode_ndarray( arr, path ):
    if arr.dtype.kind not in _ALLOWED_DTYPE_KINDS or arr.dtype.hasobject:
        raise SerializationError(
            f"Array at {path} has dtype '{arr.dtype}' which is not allowed. "
            f"Only numeric and bool dtypes can be sent." )
    a = np.ascontiguousarray( arr )
    return { NDARRAY_TAG: {
        'dtype': a.dtype.str,
        'shape': list( a.shape ),
        'data': base64.b64encode( a.tobytes() ).decode( 'ascii' ),
    } }


def _encode( obj, path='$' ):
    # bool before int: bool is a subclass of int
    if obj is None or isinstance( obj, ( bool, str, int, float ) ):
        return obj
    if isinstance( obj, np.bool_ ):
        return bool( obj )
    if isinstance( obj, np.integer ):
        return int( obj )
    if isinstance( obj, np.floating ):
        return float( obj )
    if isinstance( obj, np.ndarray ):
        return _encode_ndarray( obj, path )
    if isinstance( obj, ( list, tuple ) ):
        return [ _encode( v, f'{path}[{i}]' ) for i, v in enumerate( obj ) ]
    if isinstance( obj, dict ):
        out = {}
        for k, v in obj.items():
            if not isinstance( k, str ):
                raise SerializationError(
                    f"Dict key {k!r} at {path} is not a string. "
                    f"Only string keys can be sent." )
            if k == NDARRAY_TAG:
                raise SerializationError(
                    f"Dict key '{NDARRAY_TAG}' at {path} is reserved." )
            out[k] = _encode( v, f'{path}.{k}' )
        return out
    raise SerializationError(
        f"Value of type '{type(obj).__name__}' at {path} cannot be sent over "
        f"the remote connection. Allowed: dict, list, tuple, str, int, float, "
        f"bool, None, and numeric numpy arrays/scalars." )


def _decode_ndarray( spec, path ):
    try:
        dt = np.dtype( spec['dtype'] )
        shape = spec['shape']
        raw = base64.b64decode( spec['data'] )
    except ( KeyError, TypeError, ValueError ) as err:
        raise SerializationError( f"Malformed array encoding at {path}: {err}" ) from err
    if dt.kind not in _ALLOWED_DTYPE_KINDS or dt.hasobject:
        raise SerializationError(
            f"Array at {path} declares dtype '{dt}' which is not allowed." )
    try:
        # copy: frombuffer returns a read-only view of the decoded bytes
        return np.frombuffer( raw, dtype=dt ).reshape( shape ).copy()
    except ValueError as err:
        raise SerializationError( f"Malformed array encoding at {path}: {err}" ) from err


def _decode( obj, path='$' ):
    if isinstance( obj, dict ):
        if set( obj.keys() ) == { NDARRAY_TAG }:
            return _decode_ndarray( obj[NDARRAY_TAG], path )
        return { k: _decode( v, f'{path}.{k}' ) for k, v in obj.items() }
    if isinstance( obj, list ):
        return [ _decode( v, f'{path}[{i}]' ) for i, v in enumerate( obj ) ]
    return obj


def dumps( obj ):
    """
    Serialize obj to JSON bytes using the restricted type whitelist.

    Arguments:
        obj : the value to serialize (typically a val_dict or results dict).
    Returns:
        bytes : UTF-8 encoded JSON.
    Raises:
        SerializationError : if obj contains a type outside the whitelist.
    """
    return json.dumps( _encode( obj ) ).encode( 'utf-8' )


def loads( data ):
    """
    Deserialize JSON bytes produced by :func:`dumps`.

    Arguments:
        data (bytes) : UTF-8 encoded JSON.
    Returns:
        The decoded value; tagged numpy arrays are restored as ndarrays.
    Raises:
        SerializationError : if the payload is not valid JSON or contains a
            malformed/disallowed array encoding.
    """
    try:
        parsed = json.loads( data.decode( 'utf-8' ) )
    except ( UnicodeDecodeError, json.JSONDecodeError ) as err:
        raise SerializationError(
            'Payload is not valid JSON. A peer running an older, pickle-based '
            'version of kunene is not compatible with this version.' ) from err
    return _decode( parsed )
