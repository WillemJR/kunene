"""
Actions the examples share.

They live in a module of their own, rather than in the example scripts,
so that a job running in a child process can import them: on a platform
without fork (Windows) the child is a fresh interpreter that receives the
graph as a pickle and needs to find each action's class by module name.
"""

import time

from kunene.actions import WorkAction


class SleepySolver(WorkAction):
    """Stands in for a solver action, without needing a solver.

    It reports how far it has got with report_progress(), which is what
    makes the per-job progress bars move. The solver actions do the same
    from the solver's own output.
    """

    STEPS = 10
    seconds = 2.0       # how long the stand-in 'solver' pretends to run

    def solve(self, val_dict=None):
        for step in range(self.STEPS):
            time.sleep(self.seconds / self.STEPS)
            self.report_progress((step + 1) / self.STEPS,
                                 f'step {step + 1} of {self.STEPS}')
        return val_dict['K'] ** 0.5
