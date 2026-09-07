import jinja2

from pathlib import Path
from jinja2 import meta
from kunene.actions import WorkAction
from kunene.errors import ParameterError
import kunene.args
import kunene.variables

import logging
logger = logging.getLogger(__name__)

"""
The file is for cases where variables / parameters are defined inside the input decks
using the jinja format '{{VAR1}}}'.
"""

class JinjaReplace(WorkAction):
    """
    Action that uses Jinja2 to replace parameters in a file with values.

    The parameters in the file are indicated using double curly braces, e.g., '{{VAR1}}'.
    This action reads a template file, substitutes the parameters with provided values,
    and writes the result to an output file.

    Example:
        A line in the input file:
        '1, 7.8000E-06, {{E}}, 0.3, {{SIG_Y}},  0.0, 0.0, 0.0'
        
        With values E=210.0e9 and SIG_Y=200.0e6, becomes:
        '1, 7.8000E-06, 210.0e9, 0.3, 200.0e6,  0.0, 0.0, 0.0'

    Args:
        name (str): The name of the action.
        input_file_path (str): Path to the input file. This is the template file marked up using jinja delimiters
        output_file_path (str, optional): Path where the processed file will be written. 
            Defaults to kunene.args.RADIOSS_DFLT_FNAME.
        val_format (str, optional): Format string for floating point values (e.g., "%10.3g"). 
            Defaults to "%10.3g".
    """

    @WorkAction.allow_variables_as_arguments
    def __init__( self, name, input_file_path,
                  output_file_path=kunene.args.RADIOSS_DFLT_FNAME, val_format="%10.3g",
                  copy_paths=None, keep=None ):
        WorkAction.__init__(self, name, copy_paths=copy_paths, keep=keep)

        self.input_file_path = input_file_path
        self.output_file_path = output_file_path
        if self.output_file_path is None:
             self.output_file_path = input_file_path
        self.val_format = val_format

        self.description = f'Jinja template substitution of {input_file_path} into {output_file_path}'
        self.par_names = None
        self.par_vals = None

        self._get_parameters()

        file_to_use = self._find_input_file()
        if file_to_use is not None:
            file_to_use = Path(file_to_use).resolve()
            jj_environment = jinja2.Environment(
                loader=jinja2.FileSystemLoader(file_to_use.parent)
            )
            self.template = jj_environment.get_template(file_to_use.name)
        else:
            self.template = None


    def _produced_files( self ):
        return [ self.output_file_path ]

    def _parameter_names( self ):
        """ Returns the set of parameter names found in the template.

        Returns:
            set: Set of parameter names.
        """
        return self.par_names

    def parameters( self ):
        """ Returns the variables defined in the template.
        The type and value of the variables are unknown.

        Returns
            list : List of type UnknownVariable.
        """
        if self._parameters_cache is not None:
            return self._parameters_cache
        descr = f"From \'{self.input_file_path}\'"
        var_list = []
        for pn in self.par_names:
            self._append_unique_parameter( var_list, kunene.variables.UnknownVariable(pn, None, description=descr) )
        self._parameters_cache = var_list
        return var_list

    def _find_input_file( self ):
        """Locate the input file, checking self.input_file_path first,
        then self.copy_paths for an entry with the same filename.

        Returns:
            Path or None: path to the file, or None if not found.
        """
        input_path = Path( self.input_file_path )
        if input_path.exists():
            return input_path

        target_name = input_path.name
        for cp in self.copy_paths:
            cp_path = Path(cp)
            if cp_path.is_file() and cp_path.name == target_name:
                return cp_path
            elif cp_path.is_dir():
                candidate = cp_path / target_name
                if candidate.exists():
                    return candidate

        return None

    def _get_parameters( self ):
        """
        Extract all undefined variables from a jinja template file.

        Tries self.input_file_path first; if that cannot be opened,
        searches self.copy_paths for an entry with the same filename.
        Updates self.par_names and self.undeclared_par_names.
        """
        file_to_use = self._find_input_file()

        if file_to_use is None:
            logger.warning( f"template file not found: {self.input_file_path}" )
            self.par_names = set()
            self.undeclared_par_names = []
            return

        with open(file_to_use, 'r', encoding='utf-8') as file:
            template_source = file.read()

        self.env = jinja2.Environment()
        ast = self.env.parse(template_source)

        # get undeclared variables (parameters that need to be provided)
        undeclared_vars = jinja2.meta.find_undeclared_variables(ast)

        ast = self.env.parse(template_source)

        variables = set()

        for node in ast.find_all(jinja2.nodes.Name):
            if node.ctx == 'load':  # Loading a variable (not setting)
                variables.add(node.name)

        self.par_names = variables
        self.undeclared_par_names = sorted(list(undeclared_vars))


    def _check_var_dict( self, variable_dict_in, val_format=None ):
        """
        Validates and formats the input variable dictionary.

        Ensures all required parameters are present in the input dictionary.
        Formats floating point values according to val_format.

        Args:
            variable_dict_in (dict): Dictionary of variable names and values.
            val_format (str, optional): Format string for float values.

        Returns:
            dict: A new dictionary with formatted values and ensured keys.
        """
        if variable_dict_in is None : variable_dict_in = {}

        new_vd = variable_dict_in.copy()
        
        for k in new_vd.keys():
            if k not in self.par_names:
                #print( f' *** WARNING Variable \'{k}\' not used in file \'{self.input_file_path}\'.' )
                pass
        for i,k in enumerate(self.par_names):
            if k not in variable_dict_in.keys() :
                if self.par_vals is not None:
                    v = self.par_vals[i]
                else:
                    raise ParameterError( f'Simulation needs parameter \'{k}\' value declared.' ) # in __init__
                new_vd[k] = v
            
        if val_format is not None: # should be string of certain length in a radioss/dyna file
          for k in new_vd.keys():
            # if k not in self.par_names: # does not matter
            v = new_vd[k]
            if isinstance( v, int ):
                pass
            elif isinstance( v, str ):
                pass
            elif isinstance( v, float ):
                new_vd[k] = val_format%(v)

        return new_vd

    @WorkAction.assign_variables_values_to_members
    def solve(self, val_dict=None):
        """
        Executes the replacement action.

        Args:
            val_dict (dict, optional): Dictionary of variable values.

        Returns:
            bool: True if successful.
        """
        render_dict = self._check_var_dict(val_dict, val_format=self.val_format)
        content = self.template.render( render_dict )
        
        with open( self.output_file_path, 'w') as f:
            f.write(content)
        return True
