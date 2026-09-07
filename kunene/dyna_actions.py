
import os
import numpy as np
import json
import subprocess
from pathlib import Path

import kunene.args
import kunene.variables as simvars
from kunene.actions import WorkAction
from kunene.errors import MissingPathError, SolverError
from kunene.graph_actions import WorkFlow
from kunene.progress import FileProgressTail
from kunene.util import solver_progress

import logging
logger = logging.getLogger(__name__)

import dynakw


class DynaAnalysis(WorkAction):

    """ This runs an LS-DYNA simulation.
        It will substitute the PARAMETER values with
        provided values.

        Args:
            name (str):
            cmd (str): path to ls-dyna executable or command
            input_path (str): parameterized keyword file
            keep (list): glob patterns of this run's files that a work
                area's cleanup must never delete, e.g. ``keep=['d3plot']``
                to keep the first plot when the state files are removed.
                See :class:`kunene.args.Cleanup`.
    """

    def __init__( self, name, cmd=kunene.args.DYNA_DFLT_CMD, input_path=None, keep=None ):

        assert input_path is not None, 'No input LS-DYNA file specified.'

        super().__init__(name, cmd, copy_paths=[], keep=keep )
        self.input_file_path = input_path
        self.description = f'LS-DYNA analysis using input file {input_path}'
        self.root_name= kunene.args.DYNA_BASE_FILE_NAME


    @staticmethod
    def _replace_parameters( val_dict, input_file_path,  base_file_name ):

        with dynakw.DynaKeywordReader( input_file_path ) as dkr:
            # Get existing parameters
            params = dkr.parameters()
            logger.info("\nExisting parameters:")
            for name, value in params.items():
                logger.info(f"  {name}: {value}")

            dkr.set_parameters( val_dict )

            # Get parameters after update
            params_updated = dkr.parameters()
            logger.info("\nUpdated parameters:")
            for name, value in params_updated.items():
                 logger.info(f"  {name}: {value}")

            dkr.write( base_file_name )


    def solve( self,  val_dict=None ):
        """ """

        if not Path( self.input_file_path ).exists():
            raise MissingPathError( f'{self.input_file_path} not in run directory. Likely not copied by Iterator.' )

        if val_dict is None: val_dict = {}

        val_dict = self._reduce_to_self_parameters( val_dict )

        with open( 'dyna_variables.json','w' ) as vf:
            json.dump( val_dict, vf )

        base_file_name = self.root_name+'.k'
        
        DynaAnalysis._replace_parameters( val_dict, self.input_file_path, base_file_name )

        # termination time from the written deck (parameters substituted),
        # used to report percent-complete during the run
        try:
            t_end = solver_progress.dyna_termination_time(
                Path( base_file_name ).read_text( errors='replace' ) )
        except OSError:
            t_end = None

        have_normal_termination = self._run_solver_in_dir( base_file_name, t_end )

        return have_normal_termination


    def _describe_returncode(self, returncode):
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
            return f"command not found (exit code 127) - is '{self.cmd}' on PATH?"
        if returncode == 126:
            return "command found but not executable (exit code 126)"
        return f"exit code {returncode}"


    def _run_solver_in_dir( self, base_file_name, t_end=None ):
        """
        """

        out_file = open( 'run_file.stdout' , 'w' )
        err_file = open( 'run_file.stderr' , 'w')

        # LS-DYNA command syntax: ls-dyna i=input.k
        run_cmd = f"{self.cmd} i={base_file_name}"

        tail = FileProgressTail( self._progress_reporter, self.name, 'run_file.stdout',
                                 solver_progress.dyna_run_time, t_end )
        tail.start()
        try:
            flag = subprocess.run( run_cmd, shell=True, stdout=out_file, stderr=err_file )
        finally:
            tail.stop()

        out_file.close()
        err_file.close()
        
        if flag.returncode != 0:
            logger.error( f"LS-DYNA run in {os.getcwd()} failed: {self._describe_returncode(flag.returncode)}" )
            logger.error(f"  command: {run_cmd}")


        have_normal_termination = False
        if Path('run_file.stdout').exists():
            with open( 'run_file.stdout', 'r' ) as outfile:
                 if 'Normal termination' in outfile.read():
                     have_normal_termination = True
                 outfile.seek(0)
                 if 'N o r m a l   t e r m i n a t i o n' in outfile.read():
                     have_normal_termination = True
        
        if not have_normal_termination and Path('d3hsp').exists():
             with open( 'd3hsp', 'r' ) as f:
                 if 'Normal termination' in f.read():
                     have_normal_termination = True
                 # below needs checking with ls-dyna. This is the way I and google remember it
                 f.seek(0)
                 if 'N o r m a l   t e r m i n a t i o n' in f.read():
                     have_normal_termination = True
             
             if not have_normal_termination:
                 # Check for explicit error
                 have_error = False
                 if Path('run_file.stdout').exists():
                     with open( 'run_file.stdout', 'r' ) as outfile:
                         # below needs checking with ls-dyna. This is the way I and google remember it
                         if 'E r r o r   t e r m i n a t i o n' in outfile.read():
                             have_error = True
                 
                 if have_error:
                      raise SolverError( f'LS-DYNA run failed in \"{os.getcwd()}\"' )
                 else:
                      logger.warning( f"LS-DYNA run in {os.getcwd()} finished but 'Normal termination' not found." )

        return have_normal_termination


    def _produced_files( self ):
        return [
            'dyna_variables.json',
            self.root_name + '.k',
            'run_file.stdout',
            'run_file.stderr',
            'd3plot*',
            'd3hsp',
        ]

    def _disposable_files( self ):
        # the plot and dump databases. The deck, d3hsp and the redirected
        # stdout/stderr stay: they are small and they are what a finished
        # run is read back with.
        return [ 'd3plot*', 'd3thdt*', 'd3dump*', 'runrsf*', 'binout*' ]

    def parameters( self ):
        """ Returns the variables defined in the template.
        The type and value of the variables are as in
        the LS-DYNA input deck.

        Returns
            list : List of Variables.
        """
        if self._parameters_cache is not None:
            return self._parameters_cache

        file_to_open = Path( self.input_file_path )

        if not file_to_open.exists():
            logger.warning( f"Cannot find input file for parameters(): {self.input_file_path}" )
            return []

        variables = []
        descr = f"From \'{self.input_file_path}\'"
        with dynakw.DynaKeywordReader( file_to_open ) as dkr:
            params = dkr.parameters()
            for name, value in params.items():
                if isinstance(value, float):
                    self._append_unique_parameter( variables, simvars.FloatVariable(name, value, description=descr) )
                elif isinstance(value, int):
                    self._append_unique_parameter( variables, simvars.IntSetVariable(name, value, description=descr) )
                elif isinstance(value, str):
                    self._append_unique_parameter( variables, simvars.StrSetVariable(name, value, description=descr) )
                else:
                    self._append_unique_parameter( variables, simvars.UnknownVariable(name, None, description=descr) )

        self._parameters_cache = variables
        return variables



