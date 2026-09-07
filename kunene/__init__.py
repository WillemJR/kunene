__version__ = '1.3.0'

from kunene.errors import (
    KuneneError,
    ActionNameError,
    ParameterError,
    EvaluationError,
    SolverError,
    MissingPathError,
    DataNotFoundError,
    SerializationError,
    AsyncActionError,
    SpawnError,
)

from kunene.args import Cleanup

import logging
logging.getLogger(__name__).addHandler(logging.NullHandler())

