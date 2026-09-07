"""
Exception hierarchy for kunene.

All errors raised by kunene derive from KuneneError, so callers can
catch any workflow failure with a single ``except KuneneError``.
Previously these conditions called ``exit()``, which raises SystemExit:
that kills notebooks and long-running processes (e.g. the gRPC server in
``remote_actions``, whose handler catches Exception but not SystemExit).
"""


class KuneneError(Exception):
    """Base class for all kunene errors."""


class ActionNameError(KuneneError):
    """An action name is invalid, duplicated, or clashes with a variable name."""


class ParameterError(KuneneError):
    """A parameter/variable is missing, has no value, or cannot be resolved."""


class EvaluationError(KuneneError):
    """A MathEvaluation expression could not be evaluated."""


class SolverError(KuneneError):
    """An external solver run (LS-DYNA, OpenRadioss, OpenFOAM) failed."""


class MissingPathError(KuneneError, FileNotFoundError):
    """A required file or directory was not found."""


class DataNotFoundError(KuneneError):
    """Requested result data (e.g. a d3plot component or node id) is not available."""


class SerializationError(KuneneError):
    """A value cannot be encoded for (or decoded from) the remote connection."""


class AsyncActionError(KuneneError):
    """An action running asynchronously in a child process failed."""


class SpawnError(KuneneError):
    """A child process could not be started under the ``spawn`` start method,
    because something it needs is defined in the calling script."""
