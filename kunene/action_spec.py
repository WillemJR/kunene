"""
Save and reload a kunene workflow as plain data.

A workflow is written as a JSON document naming each action's class and
the arguments it was constructed with; loading looks the class up in
``WorkAction._registry`` and calls it. Nothing in the file is executed, so
a spec can be written by a GUI, reviewed in a diff, or accepted from a
colleague -- unlike a pickled graph, which is code. It also survives a
kunene upgrade that a pickle would not: the file records constructor
arguments, not the attribute layout of the classes.

    from kunene import save_workflow, load_workflow

    save_workflow( itr, 'crash.kunene.json' )
    itr = load_workflow( 'crash.kunene.json' )

Argument values use the plain-data whitelist of
:mod:`kunene.serialization` extended with the kunene types that appear in
constructors: ``Variable``, ``Path``, ``Cleanup`` and the enums of
``kunene.args``. The gRPC whitelist itself is deliberately left alone: a
spec may carry a Variable, a wire payload may not.

Actions describe themselves through ``to_spec()``/``from_spec()``. The
implementation on ``WorkAction`` is generic -- it replays the recorded
constructor arguments -- so an action of your own needs nothing beyond
being importable. Only the containers, whose children arrive through
``add_action`` rather than through ``__init__``, override them.
"""

import inspect
import json
from datetime import datetime
from enum import Enum
from pathlib import Path

import kunene.args
from kunene import serialization
from kunene.args import Cleanup
from kunene.errors import SerializationError, SpecError
from kunene.variables import Variable

import logging
logger = logging.getLogger(__name__)


# Bumped when a change to the format stops this module reading files that
# earlier versions wrote.
SPEC_VERSION = 1

# Marks an encoded kunene object: { _TAG: 'FloatVariable', ... }
_TAG = '__kunene__'

# Enum classes that may appear in a constructor argument, by name. A spec can
# only name one of these: decoding never imports what the file asks for, which
# is what keeps loading a spec from being code execution. Third-party enums
# that kunene's own actions use are added by _ensure_enums(); an enum of your
# own goes in through register_enum().
_ENUMS = { 'EvalType': kunene.args.EvalType,
           'JobType': kunene.args.JobType,
           'Location': kunene.args.Location }

# Enums reached through an optional dependency: (module, attribute). Tried
# once, on the first encode or decode, and skipped where the dependency is
# not installed.
_OPTIONAL_ENUMS = ( ( 'lasso.dyna', 'FilterType' ), )

_optional_enums_loaded = False


def register_enum( enum_class, name=None ):
    """
    Allow an enum to be used as a constructor argument of an action.

    Only enums registered here can be written to a spec or read back from
    one, so that decoding a file never reaches for a class the file names.
    kunene registers its own (``EvalType``, ``JobType``, ``Location``) and
    those its actions take from lasso; register an enum of your own before
    saving or loading a workflow whose actions use it.

    Arguments:
        enum_class (type) : an Enum subclass.
        name (str) : the name written to the file. Defaults to the class
            name, and must be unique across registered enums.
    Raises:
        SpecError : the name is already registered to a different class.
    """
    if not ( isinstance( enum_class, type ) and issubclass( enum_class, Enum ) ):
        raise SpecError( f'{enum_class!r} is not an Enum class.' )
    name = name or enum_class.__name__
    known = _ENUMS.get( name )
    if known is not None and known is not enum_class:
        raise SpecError(
            f'Cannot register {enum_class.__module__}.{enum_class.__name__} as '
            f'{name!r}: that name is already registered to '
            f'{known.__module__}.{known.__name__}. Pass a different name.' )
    _ENUMS[name] = enum_class
    return enum_class


def _ensure_enums():
    """Register the enums that come from optional dependencies, once."""
    global _optional_enums_loaded
    if _optional_enums_loaded:
        return
    _optional_enums_loaded = True
    import importlib
    for mod_name, attr in _OPTIONAL_ENUMS:
        try:
            register_enum( getattr( importlib.import_module( mod_name ), attr ) )
        except ( ImportError, AttributeError, SpecError ) as err:
            logger.debug( f'Not registering enum {mod_name}.{attr}: {err}' )

# Action modules tried before a spec is decoded, so the classes kunene ships
# are in the registry without the caller importing them. Any that needs an
# optional dependency is skipped silently; an action from a skipped module
# then fails in action_from_spec with a message naming it.
_BUILTIN_ACTION_MODULES = (
    'kunene.actions',
    'kunene.graph_actions',
    'kunene.simulation_iterator',
    'kunene.jinja_actions',
    'kunene.dyna_actions',
    'kunene.radioss_actions',
    'kunene.radioss_using_dyna_inp',
    'kunene.openfoam_actions',
    'kunene.d3plot_actions',
    'kunene.vtk_actions',
    'kunene.remote_actions',
)


# ----------------------------------------------------------------- values


def encode_args( args, path='$' ):
    """
    Encode a mapping of constructor arguments as plain data.

    Arguments:
        args (dict) : argument name -> value.
        path (str) : location reported in an error message.
    Returns:
        dict : JSON-compatible.
    Raises:
        SpecError : a value cannot be written to a spec.
    """
    return { k: _encode_value( v, f'{path}.{k}' ) for k, v in args.items() }


def decode_args( args, path='$' ):
    """Reverse :func:`encode_args`."""
    return { k: _decode_value( v, f'{path}.{k}' ) for k, v in args.items() }


def _encode_value( v, path ):
    if isinstance( v, Variable ):
        return { _TAG: type( v ).__name__,
                 'args': encode_args( _args_from_signature( v, path ), path ) }
    if isinstance( v, Cleanup ):
        return { _TAG: 'Cleanup',
                 'args': encode_args( _args_from_signature( v, path ), path ) }
    if isinstance( v, Enum ):
        _ensure_enums()
        name = type( v ).__name__
        # by identity, not by name: a foreign enum that happens to share a
        # name with a registered one must not be written as that one
        if _ENUMS.get( name ) is not type( v ):
            raise SpecError(
                f'{path}: enum {type(v).__module__}.{name} cannot be written '
                f'to a spec. Call kunene.action_spec.register_enum( {name} ) '
                f'before saving a workflow that uses it.' )
        return { _TAG: name, 'value': v.value }
    if isinstance( v, Path ):
        return { _TAG: 'Path', 'value': str( v ) }
    if isinstance( v, ( set, frozenset ) ):
        # an allowable-value set; ordered so the file is stable across runs
        return { _TAG: 'set',
                 'value': [ _encode_value( x, f'{path}[]' )
                            for x in sorted( v, key=repr ) ] }
    if isinstance( v, dict ):
        for reserved in ( _TAG, serialization.NDARRAY_TAG ):
            if reserved in v:
                raise SpecError( f'{path}: dict key {reserved!r} is reserved.' )
        return { k: _encode_value( x, f'{path}.{k}' ) for k, x in v.items() }
    if isinstance( v, ( list, tuple ) ):
        return [ _encode_value( x, f'{path}[{i}]' ) for i, x in enumerate( v ) ]
    try:
        # plain data only: str, numbers, bool, None, numpy arrays
        return serialization._encode( v, path )
    except SerializationError as err:
        raise SpecError(
            f'{path}: a value of type {type(v).__name__!r} cannot be written '
            f'to a spec. An action holding a live handle, an open file or a '
            f'lambda can only be built in Python.' ) from err


def _decode_value( v, path ):
    if isinstance( v, list ):
        return [ _decode_value( x, f'{path}[{i}]' ) for i, x in enumerate( v ) ]
    if not isinstance( v, dict ):
        return v
    if serialization.NDARRAY_TAG in v:
        if set( v ) != { serialization.NDARRAY_TAG }:
            raise SpecError(
                f'{path}: an encoded array carries nothing but '
                f'{serialization.NDARRAY_TAG!r}; this one also has '
                f'{sorted( set( v ) - { serialization.NDARRAY_TAG } )}.' )
        # written by serialization._encode on the way out; decoded by its
        # counterpart, which validates the dtype and rebuilds the ndarray
        try:
            return serialization._decode( v, path )
        except SerializationError as err:
            raise SpecError( f'{path}: {err}' ) from err
    if _TAG not in v:
        return { k: _decode_value( x, f'{path}.{k}' ) for k, x in v.items() }

    kind = v[_TAG]
    if kind == 'Path':
        return Path( v['value'] )
    if kind == 'set':
        return { _decode_value( x, f'{path}[]' ) for x in v['value'] }
    _ensure_enums()
    if kind in _ENUMS:
        try:
            return _ENUMS[kind]( v['value'] )
        except ValueError as err:
            raise SpecError( f'{path}: {v["value"]!r} is not a {kind}.' ) from err
    if kind == 'Cleanup':
        return Cleanup( **decode_args( v.get( 'args', {} ), path ) )

    from kunene import variables
    var_class = getattr( variables, kind, None )
    if isinstance( var_class, type ) and issubclass( var_class, Variable ):
        return var_class( **decode_args( v.get( 'args', {} ), path ) )

    raise SpecError( f'{path}: unknown tagged value {kind!r} in spec.' )


def _args_from_signature( obj, path ):
    """
    Recover the constructor arguments of a small helper object (a
    ``Variable``, a ``Cleanup``) by reading the attributes its ``__init__``
    parameters name.

    These are not actions, so they carry no recorded ``_init_args``; they
    are few, stable, and store every argument under its own name, which
    makes reading them back off the signature reliable and keeps this
    module from growing a serialiser per class.
    """
    sig = inspect.signature( type( obj ).__init__ )
    doc = ( type( obj ).__doc__ or '' ).strip()
    out = {}
    for pname, p in sig.parameters.items():
        if pname == 'self' or p.kind in ( p.VAR_POSITIONAL, p.VAR_KEYWORD ):
            continue
        if not hasattr( obj, pname ):
            raise SpecError(
                f'{path}: {type(obj).__name__} does not store its '
                f'{pname!r} argument, so it cannot be written to a spec.' )
        value = getattr( obj, pname )
        # the constructor fills an omitted description from the class
        # docstring; recording that would put the docstring in the file
        if pname == 'description' and value == doc:
            continue
        if p.default is not p.empty and _same_as_default( value, p.default ):
            continue
        out[pname] = value
    return out


def _same_as_default( value, default ):
    try:
        return bool( value == default )
    except Exception:
        # numpy arrays and anything else with an odd __eq__: keep the value
        return False


# ---------------------------------------------------------------- actions


def _import_builtin_actions():
    """Put the action classes kunene ships into the registry. Modules whose
    optional dependencies are missing are skipped."""
    import importlib
    for mod in _BUILTIN_ACTION_MODULES:
        try:
            importlib.import_module( mod )
        except ImportError as err:
            logger.debug( f'Not registering actions from {mod}: {err}' )


def action_from_spec( d, path='$' ):
    """
    Build one action from its spec.

    Arguments:
        d (dict) : ``{'type': ..., 'name': ..., 'args': {...}}``.
    Returns:
        WorkAction
    Raises:
        SpecError : the spec names a class this kunene does not know.
    """
    from kunene.actions import WorkAction

    if not isinstance( d, dict ) or 'type' not in d:
        raise SpecError( f'{path}: expected an action spec with a \'type\' key.' )

    type_name = d['type']
    action_class = WorkAction._registry.get( type_name )
    if action_class is None:
        _import_builtin_actions()
        action_class = WorkAction._registry.get( type_name )
    if action_class is None:
        known = ', '.join( sorted( WorkAction._registry ) )
        raise SpecError(
            f'{path}: unknown action type {type_name!r}. Import the module '
            f'that defines it before loading the workflow. Known types: '
            f'{known}.' )
    return action_class.from_spec( d )


# ------------------------------------------------------------------ files


def workflow_spec( action ):
    """
    The full spec document for a workflow, as a dict.

    Arguments:
        action (WorkAction) : any action; usually the top-level one (a
            ``SimulationIterator``, a ``WorkArea`` or a graph).
    Returns:
        dict
    """
    import kunene
    return { 'kunene_spec': SPEC_VERSION,
             'kunene_version': kunene.__version__,
             'created': datetime.now().isoformat( timespec='seconds' ),
             'workflow': action.to_spec() }


def save_workflow( action, path ):
    """
    Write a workflow to a JSON file.

    What is saved is the *definition* -- the actions, their arguments and
    the edges between them -- not the results of a run, which belong to the
    results directory (``actions_output.pkl`` and the job index).

    Arguments:
        action (WorkAction) : the workflow to save.
        path (str|Path) : file to write.
    Returns:
        Path : the file written.
    Raises:
        SpecError : an argument of some action cannot be written.
    """
    path = Path( path )
    path.write_text( json.dumps( workflow_spec( action ), indent=2 ) + '\n' )
    logger.info( f'Wrote workflow {action.name} to {path}' )
    return path


def load_workflow( path ):
    """
    Rebuild a workflow written by :func:`save_workflow`.

    Arguments:
        path (str|Path) : the spec file.
    Returns:
        WorkAction : ready to ``solve()``.
    Raises:
        SpecError : the file is not a workflow spec, was written by a
            newer format, or names an action this kunene does not know.
    """
    path = Path( path )
    try:
        doc = json.loads( path.read_text() )
    except json.JSONDecodeError as err:
        raise SpecError( f'{path} is not valid JSON: {err}' ) from err

    if not isinstance( doc, dict ) or 'kunene_spec' not in doc:
        raise SpecError( f'{path} is not a kunene workflow spec.' )

    version = doc['kunene_spec']
    if version != SPEC_VERSION:
        raise SpecError(
            f'{path} was written in spec format {version}, which this kunene '
            f'(format {SPEC_VERSION}) cannot read.' )

    return action_from_spec( doc['workflow'], path=str( path ) )
