import os
from abc import ABC, abstractmethod
import numpy as np

from kunene.args import EvalType

import logging
logger = logging.getLogger(__name__)

from abc import ABC, abstractmethod

from kunene.util.observer import Subject, notify_observers

from kunene.variables import Variable
from kunene.actions import WorkAction


class HistoryEvaluation(WorkAction):
    """
    Class to check if is history
    """

    @abstractmethod
    def __init__( self, name, cmd ):
        super().__init__(name, cmd )

    def _dump(self,  val_dict=None ):
        h = val_dict[ self.name ]
        np.save( self.name,  h )



class FunctionEvaluation(WorkAction):

    """
    """

    def __init__( self, name, func, *args, **kwargs  ):
        super().__init__(name, None )
        self.func= func
        self.args= args
        self.kwargs= kwargs

    def solve(self,  val_dict=None ):
        v = self.func( *self.args, **self.kwargs )
        return v




