
import logging
logger = logging.getLogger(__name__)

import json
from pathlib import Path
import subprocess

from kunene.errors import MissingPathError, SolverError
from kunene.progress import FileProgressTail
from kunene.util import solver_progress
from kunene.radioss_actions import RadiossAnalysis
from kunene.dyna_actions import DynaAnalysis

from kunene.args import RADIOSS_ROOT_NAME
from kunene.args import RADIOSS_BASE_F_NAME # _0000
from kunene.args import RADIOSS_ENGINE_F_NAME # _0001


#class DynaUsingRadioss(RadiossAnalysis,DynaAnalysis):
class RadiossUsingDynaInput(RadiossAnalysis):

    """ Runs an LS-DYNA deck using OpenRadioss. 
        A command/script running the LS-DYNA deck using OpenRadioss must be provided.
        The script should be usable as, for example, 'rad_dyna_inp zug_test3_RS.k'

        Args:
            name (str): A name for this action.
            cmd (str): path to the script executing OpenRadioss.
            input_path (str): LS-DYNA keyword file. (Possibly parameterized?)
            create_d3plot (bool): Creates a d3plot files using
                            vortex_radioss.animtod3plot.Anim_to_D3plot
    """

    def __init__( self, name, cmd, input_path,
                  create_d3plot=False,
                  create_vtk=False, to_vtk_cmd='anim_to_vtk_linux64_gf',
                  create_csv=False, to_csv_cmd='th_to_csv_linux64_gf',
                  keep=None):

        RadiossAnalysis.__init__( self, name, 
                                  starter_cmd=cmd,
                                  starter_input_path=input_path,
                                  engine_cmd=cmd+'_DUMMY_ENGINE',
                                  engine_input_path=input_path+'_DUMMY',
                                  create_d3plot=create_d3plot,
                                  create_vtk=create_vtk,
                                  to_vtk_cmd=to_vtk_cmd,
                                  create_csv=create_csv,
                                  to_csv_cmd=to_csv_cmd,
                                  keep=keep )

        self.input_file_path = input_path
        #DynaAnalysis.__init__(self, name, cmd, input_path=input_path )

    def solve( self,  val_dict=None ):
        if not Path( self.starter_input_path ).exists():
            raise MissingPathError( f'{self.starter_input_path} not in run directory. Likely not copied by Iterator.' )

        if val_dict is None: val_dict = {}

        val_dict = self._reduce_to_self_parameters( val_dict )

        with open( 'dyna_variables.json','w' ) as vf:
            json.dump( val_dict, vf )

        #start_file_name = RADIOSS_BASE_F_NAME+'.rad'
        start_file_name = RADIOSS_ROOT_NAME+'.k'

        #DynaAnalysis._replace_parameters( base_file_name )
        #breakpoint()
        DynaAnalysis._replace_parameters( val_dict, self.starter_input_path, start_file_name )

        # termination time from the written LS-DYNA deck; the OpenRadioss
        # wrapper prints NC=/T= progress lines to the starter stdout
        try:
            t_end = solver_progress.dyna_termination_time(
                Path( start_file_name ).read_text( errors='replace' ) )
        except OSError:
            t_end = None

        #shutil.copy( self.engine_input_path, RADIOSS_ENGINE_F_NAME+'.rad' )

        self._run_in_dir( start_file_name, t_end )

        if self.create_d3plot : self._create_d3plot_file(RADIOSS_ROOT_NAME)
        if self.create_vtk: self. _create_vtk_file( )
        if self.create_csv: self. _create_csv_file( )

        return True 


    def _run_in_dir( self, start_file_name, t_end=None ):
        """
        new_input:
        """

        out_file = open( RADIOSS_BASE_F_NAME+'.starter.stdout' , 'w' )
        err_file = open( RADIOSS_BASE_F_NAME+'.starter.stderr' , 'w')

        tail = FileProgressTail( self._progress_reporter, self.name,
                                 RADIOSS_BASE_F_NAME+'.starter.stdout',
                                 solver_progress.radioss_run_time, t_end )
        tail.start()
        try:
            #subprocess.run( self.starter_cmd + ' -i ' + start_file_name, shell=True, stdout=out_file, stderr=err_file )
            subprocess.run( self.starter_cmd + ' ' + start_file_name + ' 1', shell=True, stdout=out_file, stderr=err_file )
        finally:
            tail.stop()

        out_file.close()
        err_file.close()

        have_error_termination = False
        with open( RADIOSS_BASE_F_NAME+'.starter.stdout',  'r' ) as outfile:
            for line in outfile.readlines():
                if 'ERROR TERMINATION' in line:
                    have_error_termination = True
        #if have_error_termination is False:
        #  with open( RADIOSS_ENGINE_F_NAME+'.starter.stdout',  'r' ) as outfile:
        #    for line in outfile.readlines():
        #        if 'ERROR TERMINATION' in line:
        #            have_error_termination = True
        if have_error_termination is True:
            raise SolverError( f'OpenRadios run failed in \"{Path.cwd()}\"' )


    def parameters( self ):
        return DynaAnalysis.parameters(self)


