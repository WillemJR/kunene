
import os
import contextlib
import json
import glob
import subprocess
import numpy as np
import pandas
import shutil

from pathlib import Path

from kunene.actions import WorkAction
from kunene.errors import KuneneError, MissingPathError, SolverError
from kunene.progress import FileProgressTail
from kunene.util import solver_progress
from kunene.rare import HistoryEvaluation
from kunene.util.openradios_reader import OpenRadiosKeywordReader
import kunene.variables as simvars

import kunene.args

import logging
logger = logging.getLogger(__name__)

from kunene.args import RADIOSS_ROOT_NAME
from kunene.args import RADIOSS_BASE_F_NAME # _0000
from kunene.args import RADIOSS_ENGINE_F_NAME # _0001


class RadiossCSVHistory(HistoryEvaluation):
    """ Extraction of Radioss history 

    Args:
        name (str) :
        cmd (str) :
        root_name (str) : root_name of radios input. E.g. 'dyna_action_inp' to read dyna_action_inpT01.csv
    Returns:
      hist (list): [ [time vals],  [vals] ]
    """


    def __init__( self, name, cmd, root_name=RADIOSS_ROOT_NAME ):
        super().__init__(name, cmd )
        self.args = json.loads( cmd )
        self.parent_simu = None
        self.root_name = root_name
        self.description = f'Radioss CSV history extraction of {self.args.get("quantity", "")}'


    def solve( self, val_dict=None ):

        fname = self.root_name+'T01.csv'
        if not Path(fname).exists():
            logger.error(f"Cannot open OpenRadioss CSV file. Did you set \'create_csv=True\' ")
            raise FileNotFoundError(f'RadiossCSVHistory: CSV file not found: {fname}')
        logger.info( f'RadiossCSVHistory reading \'{fname}\'' )
        df = pandas.read_csv( fname )

        time =  df[ 'time' ]
        val_key = self.args['quantity']
        vals = df[ val_key ]
        results = np.array( [ time, vals ]  )

        # this var 85 differs between databases-- cannot be used as fixed
        # the "var 85" is simply the offset in the csv file
        #print( df['TimeHistory1                             10851                                          var 85' ] )
        #print( df['TimeHistory1                             10851                                          var 88' ] )
        #print( df['TimeHistory1                             10851                                          var 91' ] )

        return results

class CSVNodeLocationHistory(HistoryEvaluation):
    """ Extraction of Radioss nodal location history 

    Shorter version which returns nodal locations

    """


    def __init__( self, name, node_id, root_name=RADIOSS_ROOT_NAME ):
        """ Extraction of Radioss nodal location history

        Args:
            name (str)
            node_id (str):
            root_name (str):
        """
        super().__init__(name, "" )
        self.node_id = node_id
        self.parent_simu = None
        self.root_name = root_name
        self.description = f'Radioss nodal location history for node {node_id}'


    def solve( self, val_dict=None ):
        """ Extraction of Radioss nodal location history

        Returns:
          hist (list): [ [time vals],  [3][vals] ]
        """

        df = pandas.read_csv( self.root_name+'T01.csv' )

        time =  df[ 'time' ]
        # "DATABASE_HISTORY_NODE                    682                                          var 22",

        # try using 'DATABASE_HISTORY_NODE'
        candidate_keys = df.filter( like='DATABASE_HISTORY_NODE' ).columns.tolist()
        sub_str = " %10d "%(self.node_id)
        candidate_keys = [k for k in candidate_keys if sub_str in k ]
        #print( ' --- candidate_keys A', candidate_keys )

        # try using 'TimeHistory'
        if not candidate_keys:
            candidate_keys = df.filter( like='TimeHistory1' ).columns.tolist()
            sub_str = " %10d "%(self.node_id)
            candidate_keys = [k for k in candidate_keys if sub_str in k ]
            #print( ' --- candidate_keys B', candidate_keys )


        val_x = df[ candidate_keys[0] ]
        val_y = df[ candidate_keys[1] ]
        val_z = df[ candidate_keys[2] ]

        return np.array( time ), np.array( (val_x, val_y, val_z) ) 

    def _dump(self,  val_dict=None ):
        h = val_dict[ self.name ]
        np.savez( self.name,  time=h[0], xyz=h[1] )


class CSVNodeLocation(CSVNodeLocationHistory):

    def __init__( self, name, node_id, step=-1, root_name=RADIOSS_ROOT_NAME ):
        super().__init__(name, node_id, root_name )
        self.step = step
        self.description = f'Radioss nodal location for node {node_id} at step {step}'

    def solve( self,  val_dict=None ):
        h = super().solve( val_dict )
        return h[1][:, self.step]

    def _dump(self,  val_dict=None ):
        pass


class ScalarEvaluation(RadiossCSVHistory):
    """
    Evaluation of FEA results
    Returns: float
    """

    def __init__( self, name, cmd, root_name=RADIOSS_ROOT_NAME ):
        super().__init__(name, cmd, root_name )
        self.args = json.loads( cmd )
        self.parent_simu = None
        self.description = f'Radioss scalar evaluation of {self.args.get("quantity", "")} at step {self.args.get("step", "")}'

    def solve( self,  val_dict=None ):
        h = super().solve( val_dict )
        return h[1][ self.args['step'] ]

    def _dump(self,  val_dict=None ):
        pass




class RadiossAnalysisBase:

    def _create_d3plot_file( self, root_name ):
        try:
            from vortex_radioss.animtod3plot.Anim_to_D3plot import readAndConvert
        except ImportError as err:
            raise KuneneError( 'Install vortex_radioss.animtod3plot from \'https://www.vortex-cae.com/vortex-radioss\'.' ) from err

        try:
            # readAndConvert prints its progress; keep it out of the
            # terminal (and off a parallel study's progress bars) by
            # putting it in a log beside the other conversion logs.
            with open( 'd3plot_conversion.stdout', 'w' ) as out:
                with contextlib.redirect_stdout( out ):
                    readAndConvert( str( Path.cwd().joinpath( root_name) )  )
        except Exception as e:
            logger.error(f"Cannot convert OpenRadioss output to d3plot file: {e}")

        # Rename generated d3plot files
        # Pattern: filename.d3plot* -> d3plot*
        pattern = f"{root_name}.d3plot*"
        for file_path in glob.glob(pattern):
            # We only want to replace the prefix in the basename
            base_name = os.path.basename(file_path)
            if base_name.startswith(f"{root_name}."):
                new_name = base_name.replace(f"{root_name}.", "", 1)
                logger.info(f"Renaming {base_name} to {new_name}")
                os.rename(file_path, new_name)







class RadiossAnalysis(WorkAction,RadiossAnalysisBase):

    """ Runs an openRadioss simulation. 

        Args:
            name (str): A name for this action.
            starter_cmd (str): path to OpenRadioss executable or command. Command may include arguments 'runradios -np 1 -nt 4' 
            starter_input_path (str): OpenRadioss keyword file. Possibly parameterized.
            engine_cmd (str): path to OpenRadioss executable or command. Command may include arguments 'runradios -np 1 -nt 4' 
            engine_input_path (str): OpenRadioss input file. 
            create_d3plot (bool): Creates a d3plot files using
                            vortex_radioss.animtod3plot.Anim_to_D3plot
            keep (list): glob patterns of this run's files that a work
                area's cleanup must never delete. See
                :class:`kunene.args.Cleanup`.
    """

    def __init__( self, name,
                  starter_cmd='openradioss_starter',
                  starter_input_path=kunene.args.RADIOSS_DFLT_FNAME,
                  engine_cmd='openradioss_engine',
                  engine_input_path=None,
                  create_d3plot=False,
                  create_vtk=False,
                  to_vtk_cmd='anim_to_vtk_linux64_gf',
                  create_csv=False,
                  to_csv_cmd='th_to_csv_linux64_gf',
                  keep=None ):

        assert starter_input_path is not None, 'No input OpenRadioss file specified. Specify the path.'
        assert engine_input_path is not None, 'No engine input OpenRadioss file specified. Specify the path.'

        super().__init__(name, starter_cmd, copy_paths=[], keep=keep )
        self.starter_input_path = starter_input_path
        self.starter_input_path = Path( self.starter_input_path ).name
        self.engine_input_path = engine_input_path
        self.engine_input_path = Path( self.engine_input_path ).name
        self.starter_cmd = starter_cmd
        self.engine_cmd = engine_cmd

        self.create_d3plot = create_d3plot
        self.create_vtk = create_vtk
        self.to_vtk_cmd = to_vtk_cmd
        self.create_csv = create_csv
        self.to_csv_cmd = to_csv_cmd

        self.evaluations = []
        self.description = f'OpenRadioss analysis using input file {starter_input_path}'


    def  _clean_dir( self, base_file ):
        r = subprocess.run( [f'rm  {base_file}*.vtk {base_file}*.csv'], shell=True )


    def _create_vtk_file( self ):
        logger.info( 'Converting openRadioss to vtk files' )

        out_file = open( 'vtk_conversion.stdout' , 'w' )
        err_file = open( 'vtk_conversion.stderr' , 'w')

        anim_files = glob.glob(RADIOSS_ROOT_NAME+'A*')

        #self._clean_dir( RADIOSS_BASE_F_NAME )

        for af in anim_files:
            #idx = '_' + af.split( RADIOSS_BASE_F_NAME )[1][1:] +'.vtk'
            idx = '_' + af.split( RADIOSS_ROOT_NAME )[1][1:] +'.vtk'
            cmd = ' ' + af + ' > ' + RADIOSS_ROOT_NAME + idx
            subprocess.run( args=[self.to_vtk_cmd+  cmd], shell=True, stdout=out_file, stderr=err_file )

        out_file.close()
        err_file.close()


    def _create_csv_file( self ):
        logger.info( 'Converting openRadioss to csv files' )

        out_file = open( 'csv_conversion.stdout' , 'w' )
        err_file = open( 'csv_conversion.stderr' , 'w')

        hist_files = glob.glob(RADIOSS_ROOT_NAME+'T*')
        for tf in hist_files:
            logger.info( f'Converting {tf}' )
            subprocess.run( args=['th_to_csv_linux64_gf '+ tf], shell=True, stdout=out_file, stderr=err_file )

        out_file.close()
        err_file.close()

    def solve( self,  val_dict=None ):

        if not Path( self.starter_input_path ).exists():
            raise MissingPathError( f'{self.starter_input_path} not in run directory. Likely not copied by Iterator.' )

        if val_dict is None: val_dict = {}

        val_dict = self._reduce_to_self_parameters( val_dict )

        with open( 'radioss_variables.json','w' ) as vf:
            json.dump( val_dict, vf )

        start_file_name = RADIOSS_BASE_F_NAME+'.rad'

        with OpenRadiosKeywordReader( self.starter_input_path ) as orkr:
            orkr.set_parameters( val_dict )
            orkr.write( start_file_name )

        shutil.copy( self.engine_input_path, RADIOSS_ENGINE_F_NAME+'.rad' )

        # stop time from the engine deck (/RUN card), used to report
        # percent-complete during the engine run
        try:
            t_end = solver_progress.radioss_termination_time(
                Path( RADIOSS_ENGINE_F_NAME+'.rad' ).read_text( errors='replace' ) )
        except OSError:
            t_end = None

        self._run_starter_in_dir( start_file_name )
        self._run_engine_in_dir( RADIOSS_ENGINE_F_NAME+'.rad', t_end )

        if self.create_d3plot : self._create_d3plot_file(RADIOSS_ROOT_NAME)
        if self.create_vtk: self. _create_vtk_file( )
        if self.create_csv: self. _create_csv_file( )

        return True 


    def _produced_files( self ):
        files = [
            'radioss_variables.json',
            RADIOSS_BASE_F_NAME + '.rad',
            RADIOSS_ENGINE_F_NAME + '.rad',
            RADIOSS_BASE_F_NAME + '.starter.stdout',
            RADIOSS_BASE_F_NAME + '.starter.stderr',
            RADIOSS_ENGINE_F_NAME + '.engine.stdout',
            RADIOSS_ENGINE_F_NAME + '.engine.stderr',
        ]
        if self.create_d3plot:
            files.append( 'd3plot*' )
            files.append( 'd3plot_conversion.stdout' )
        if self.create_vtk:
            files.append( RADIOSS_ROOT_NAME + '_*.vtk' )
        if self.create_csv:
            files.append( RADIOSS_ROOT_NAME + 'T01.csv' )
        return files

    def _disposable_files( self ):
        # the animation database and whatever was converted from it. The
        # decks, the starter/engine logs and the (small) time-history csv
        # are kept.
        files = [ RADIOSS_ROOT_NAME + 'A*', RADIOSS_ROOT_NAME + '*.rst' ]
        if self.create_d3plot:
            files.append( 'd3plot*' )
        if self.create_vtk:
            files.append( RADIOSS_ROOT_NAME + '_*.vtk' )
        return files

    def parameters(self):
        """Returns the variables defined in the Radioss input file.

        Returns:
            list: List of Variable instances.
        """
        if self._parameters_cache is not None:
            return self._parameters_cache

        file_to_open = Path(self.starter_input_path)

        if not file_to_open.exists():
            logger.warning(f"Cannot find input file for parameters(): {self.starter_input_path}")
            return []

        # Records where the variable came from, as the other solver actions
        # do. Without it Variable falls back to its class docstring, which
        # is what a caller printing the variables would then show.
        descr = f"From \'{self.starter_input_path}\'"

        variables = []
        with OpenRadiosKeywordReader(file_to_open) as orkr:
            for name, (ptype, value) in orkr.parameters().items():
                if ptype == 'REAL':
                    self._append_unique_parameter(variables, simvars.FloatVariable(name, float(value), description=descr))
                elif ptype == 'INTEGER':
                    self._append_unique_parameter(variables, simvars.IntSetVariable(name, int(value), description=descr))
                elif ptype in ('REAL_EXPR', 'INT_EXPR'):
                    self._append_unique_parameter(variables, simvars.UnknownVariable(name, value, description=descr))
                elif ptype == 'TEXT':
                    self._append_unique_parameter(variables, simvars.StrSetVariable(name, str(value), description=descr))
                else:
                    self._append_unique_parameter(variables, simvars.UnknownVariable(name, value, description=descr))
        self._parameters_cache = variables
        return variables

    def write_deck( self, dpath, variable_dict=None ):
        """ write FEA deck using given parameter values.

        e.g write_deck( {'E':210.0, 'SIG_Y':sigy} )
        """
        with OpenRadiosKeywordReader( self.starter_input_path ) as orkr:
            if variable_dict:
                orkr.set_parameters( variable_dict )
            orkr.write( dpath )


    def _describe_returncode(self, returncode, cmd):
        """Human-readable interpretation of a subprocess return code."""
        # A process killed by a signal is reported as a negative code by
        # subprocess, or as 128+signum by the shell (shell=True).
        signum = None
        if returncode < 0:
            signum = -returncode
        elif returncode > 128:
            signum = returncode - 128

        if signum is not None:
            try:
                import signal
                name = signal.Signals(signum).name
            except (ValueError, ImportError):
                name = f"signal {signum}"
            return f"terminated by {name} (exit code {returncode})"

        if returncode == 127:
            return f"command not found (exit code 127) - is '{cmd}' on PATH?"
        if returncode == 126:
            return "command found but not executable (exit code 126)"
        return f"exit code {returncode}"



    def _run_starter_in_dir( self, start_file_name ):
        """
        new_input:
        """

        out_file = open( RADIOSS_BASE_F_NAME+'.starter.stdout' , 'w' )
        err_file = open( RADIOSS_BASE_F_NAME+'.starter.stderr' , 'w')

        flag = subprocess.run( self.starter_cmd + ' -i ' + start_file_name, shell=True, stdout=out_file, stderr=err_file )

        out_file.close()
        err_file.close()

        if flag.returncode != 0:
            logger.error( f"OpenRadioss run in {os.getcwd()} failed: {self._describe_returncode(flag.returncode,self.starter_cmd)}" )
            logger.error(f"  command: {self.starter_cmd}")


        have_error_termination = False
        with open( RADIOSS_BASE_F_NAME+'.starter.stdout',  'r' ) as outfile:
            for line in outfile.readlines():
                if 'ERROR TERMINATION' in line:
                    have_error_termination = True
        if have_error_termination is True:
            raise SolverError( f'OpenRadios starter run failed in \"{Path.cwd()}\"' )




    def _run_engine_in_dir( self, engine_file_name, t_end=None ):
        """
        new_input:
        """

        out_file = open( RADIOSS_ENGINE_F_NAME+'.engine.stdout' , 'w' )
        err_file = open( RADIOSS_ENGINE_F_NAME+'.engine.stderr' , 'w')

        tail = FileProgressTail( self._progress_reporter, self.name,
                                 RADIOSS_ENGINE_F_NAME+'.engine.stdout',
                                 solver_progress.radioss_run_time, t_end )
        tail.start()
        try:
            flag = subprocess.run( self.engine_cmd + ' -i ' + engine_file_name, shell=True, stdout=out_file, stderr=err_file )
        finally:
            tail.stop()

        out_file.close()
        err_file.close()

        if flag.returncode != 0:
            logger.error( f"OpenRadioss run in {os.getcwd()} failed: {self._describe_returncode(flag.returncode,self.engine_cmd)}" )
            logger.error(f"  command: {self.engine_cmd}")



        have_error_termination = False
        with open( RADIOSS_ENGINE_F_NAME+'.engine.stdout',  'r' ) as outfile:
            for line in outfile.readlines():
                if 'ERROR TERMINATION' in line:
                    have_error_termination = True
        if have_error_termination is True:
            raise SolverError( f'OpenRadios engine run failed in \"{Path.cwd()}\"' )




