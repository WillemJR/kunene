
from enum import Enum, Flag, auto

class JobType(Flag):
    """ OpenFOAM job execution stages """
    CREATE_MESH = auto()
    RUN_SIMULATION = auto()
    POST_PRO = auto()
    EXTRACT_VTK = auto()

class EvalType(Flag):
    """ Can be multiple types. Use 'if EvalType.NUMERICAL in self.type:' """
    NOT_SPECIFIED = auto()  
    FLOAT = auto()  
    NUMERICAL = auto()  # float or numpy array
    FILE = auto()
    IMAGE = auto()

class Location(Enum):
    UNKNOWN = 1
    CELL = 2
    NODAL = 3

ACTIONS_OUTPUT_PATH = 'actions_output.pkl'
ITER_VARIABLES_PATH = 'iter_variables.json'
STATUS_PATH = 'status.json'
JOBS_INDEX_PATH = 'jobs_index.json'
JOB_LOG_PATH = 'job.log'

# Files that record a run's outcome. Cleanup never removes these: deleting
# them would break reuse_existing/results_for (actions_output.pkl,
# iter_variables.json), the job index, a GUI polling the progress file, or
# the log a parallel job wrote instead of the terminal.
PROTECTED_FROM_CLEANUP = ( ACTIONS_OUTPUT_PATH,
                           ITER_VARIABLES_PATH,
                           STATUS_PATH,
                           JOBS_INDEX_PATH,
                           JOB_LOG_PATH )


class Cleanup:
    """
    Policy for removing files from a run directory once the graph has run.

    A study that keeps every solver's field output fills a disk quickly.
    Pass a ``Cleanup`` as the ``cleanup`` argument of ``WorkArea`` or
    ``SimulationIterator`` to have the bulky files removed after the run,
    keeping the ones you still want.

    The actions themselves declare which of their files are bulk output
    (``WorkAction._disposable_files()``), so ``remove`` normally needs no
    file names: ``keep`` subtracts from what those declarations select.

    Arguments:
        remove : what is eligible for deletion.

            * ``Cleanup.BULK`` (``'bulk'``, the default) -- the field
              output the actions declare as disposable (d3plot files,
              OpenRadioss animation files, VTK directories, ...). Decks,
              logs and small history files are not touched.
            * ``Cleanup.ALL`` (``'*'``) -- everything in the run
              directories except the protected files and ``keep``. A job
              directory also holds files nothing declared (copied-in
              inputs, solver scratch), so this deletes more than kunene
              knows about; use it deliberately.
            * a list of glob patterns (or a single pattern string) --
              exactly those, in every run directory.
        keep (list) : glob patterns that are never deleted. Always wins
            over ``remove``. E.g. ``keep=['d3plot']`` drops the state
            files but leaves the first plot behind.
        dry_run (bool) : log what would be deleted and delete nothing.
            Worth doing once for a new ``remove='*'`` policy.

    Example::

        SimulationIterator( graph, cleanup=Cleanup( keep=['d3plot'] ) )
    """

    BULK = 'bulk'
    ALL = '*'

    def __init__( self, remove=BULK, keep=None, dry_run=False ):
        if isinstance( remove, str ):
            # 'bulk' and '*' are modes; any other string is a single pattern
            self.remove = remove if remove in ( self.BULK, self.ALL ) else [ remove ]
        elif isinstance( remove, (list, tuple, set) ):
            self.remove = [ str(r) for r in remove ]
            if not self.remove:
                raise ValueError( 'Cleanup( remove=[] ) removes nothing; '
                                  'omit the cleanup argument instead.' )
        else:
            raise TypeError( f'Cleanup remove must be {Cleanup.BULK!r}, '
                             f'{Cleanup.ALL!r} or a list of glob patterns, '
                             f'got {remove!r}.' )

        if keep is None:
            self.keep = []
        elif isinstance( keep, str ):
            self.keep = [ keep ]
        else:
            self.keep = [ str(k) for k in keep ]

        self.dry_run = dry_run

    @classmethod
    def coerce( cls, value ):
        """
        Turn the ``cleanup`` argument of a work area into a ``Cleanup``.

        Accepts ``None``/``False`` (no cleanup), ``True`` (the default
        bulk policy), a pattern or list of patterns, or a ``Cleanup``.

        Returns:
            Cleanup or None
        """
        if value is None or value is False:
            return None
        if value is True:
            return cls()
        if isinstance( value, cls ):
            return value
        return cls( remove=value )

    def globs_for( self, action ):
        """
        Glob patterns this policy deletes for one action.

        Arguments:
            action (WorkAction) : the action whose run directory is
                being cleaned.
        Returns:
            list : glob patterns, relative to the action's run directory.
        """
        if self.remove == self.BULK:
            return list( action._disposable_files() )
        if self.remove == self.ALL:
            return [ '*' ]
        return list( self.remove )

    def __repr__( self ):
        return ( f'Cleanup( remove={self.remove!r}, keep={self.keep!r},'
                 f' dry_run={self.dry_run!r} )' )

DYNA_DFLT_CMD  = 'ls-dyna'
DYNA_BASE_FILE_NAME = 'dyna_action_inp'

RADIOSS_DFLT_FNAME = 'radioss_kunene_file.k'
RADIOSS_ROOT_NAME = 'rad_run_file'
RADIOSS_BASE_F_NAME = 'rad_run_file_0000'
RADIOSS_ENGINE_F_NAME = 'rad_run_file_0001'

