
import re
import os
from typing import Dict, List, Any, Optional, Union
from typing import List, Tuple, Optional
import numpy as np
from pathlib import Path

from kunene.args import Location
from kunene.errors import KuneneError, MissingPathError

import logging
logger = logging.getLogger(__name__)


class OpenFOAMPointsReader:
    """
    A class to read OpenFOAM points files and extract coordinate data.

    OpenFOAM points files typically contain:
    - Header information
    - Number of points
    - List of point coordinates in (x y z) format
    """

    def __init__(self, file_path: str):
        """
        Initialize the points reader.

        Args:
            file_path (str): Path to the OpenFOAM points file
        """
        self.file_path = file_path
        self.points = None
        self.header = {}

    def read_points(self) -> np.ndarray:
        """
        Read points from the OpenFOAM points file.

        Returns:
            np.ndarray: Array of shape (n_points, 3) containing x, y, z coordinates
        """
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"Points file not found: {self.file_path}")

        with open(self.file_path, 'r') as file:
            content = file.read()

        # Parse header information
        self._parse_header(content)

        # Find the number of points
        n_points = self._extract_point_count(content)

        # Extract point coordinates
        points = self._extract_coordinates(content, n_points)

        self.points = np.array(points)
        return self.points

    def _parse_header(self, content: str) -> None:
        """
        Parse the header section of the OpenFOAM file.

        Args:
            content (str): File content as string
        """
        # Extract FoamFile dictionary
        foam_file_match = re.search(r'FoamFile\s*\{([^}]*)\}', content, re.DOTALL)
        if foam_file_match:
            foam_file_content = foam_file_match.group(1)

            # Parse key-value pairs in the header
            for line in foam_file_content.split('\n'):
                line = line.strip()
                if line and not line.startswith('//'):
                    # Remove semicolons and split on whitespace
                    line = line.rstrip(';')
                    parts = line.split(None, 1)
                    if len(parts) == 2:
                        self.header[parts[0]] = parts[1].strip('"')

    def _extract_point_count(self, content: str) -> int:
        """
        Extract the number of points from the file content.

        Args:
            content (str): File content as string

        Returns:
            int: Number of points
        """
        # Look for the number before the opening parenthesis
        # This pattern matches: number followed by optional whitespace and opening paren
        count_pattern = r'(\d+)\s*\('
        match = re.search(count_pattern, content)

        if not match:
            raise ValueError("Could not find point count in the file")

        return int(match.group(1))

    def _extract_coordinates(self, content: str, n_points: int) -> List[Tuple[float, float, float]]:
        """
        Extract point coordinates from the file content.

        Args:
            content (str): File content as string
            n_points (int): Expected number of points

        Returns:
            List[Tuple[float, float, float]]: List of (x, y, z) coordinates
        """
        # Find the section with coordinates - look for the main coordinate block
        # Pattern: number followed by opening parenthesis
        start_pattern = r'(\d+)\s*\('
        start_match = re.search(start_pattern, content)

        if not start_match:
            raise ValueError("Could not find coordinate data start in the file")

        # Find the position after the number and opening parenthesis
        start_pos = start_match.end()

        # Find the matching closing parenthesis by counting parentheses
        paren_count = 1
        pos = start_pos
        coords_end = -1

        while pos < len(content) and paren_count > 0:
            if content[pos] == '(':
                paren_count += 1
            elif content[pos] == ')':
                paren_count -= 1
                if paren_count == 0:
                    coords_end = pos
                    break
            pos += 1

        if coords_end == -1:
            raise ValueError("Could not find coordinate data end in the file")

        # Extract the coordinate section
        coords_section = content[start_pos:coords_end]

        # Remove comments and clean the section
        lines = coords_section.split('\n')
        cleaned_lines = []
        for line in lines:
            # Remove comments (everything after //)
            comment_pos = line.find('//')
            if comment_pos != -1:
                line = line[:comment_pos]
            cleaned_lines.append(line.strip())

        coords_section = '\n'.join(cleaned_lines)

        # Extract individual coordinate triplets
        # More flexible pattern that handles various formats
        # Matches (x y z), (x,y,z), or variations with different spacing
        point_pattern = r'\(\s*([-+]?(?:\d*\.?\d+(?:[eE][-+]?\d+)?|\d+\.?))\s*[,\s]\s*([-+]?(?:\d*\.?\d+(?:[eE][-+]?\d+)?|\d+\.?))\s*[,\s]\s*([-+]?(?:\d*\.?\d+(?:[eE][-+]?\d+)?|\d+\.?))\s*\)'

        points = []
        for match in re.finditer(point_pattern, coords_section):
            try:
                x, y, z = float(match.group(1)), float(match.group(2)), float(match.group(3))
                points.append((x, y, z))
            except ValueError as e:
                logger.warning(f"Could not parse coordinate triplet: {match.group(0)}")
                continue

        # If the regex approach fails, try a more basic line-by-line approach
        if len(points) == 0:
            points = self._extract_coordinates_line_by_line(coords_section)

        if len(points) != n_points:
            logger.warning(f"Expected {n_points} points, but found {len(points)}")

        return points

    def _extract_coordinates_line_by_line(self, coords_section: str) -> List[Tuple[float, float, float]]:
        """
        Fallback method to extract coordinates line by line.

        Args:
            coords_section (str): The coordinate section of the file

        Returns:
            List[Tuple[float, float, float]]: List of (x, y, z) coordinates
        """
        points = []
        lines = coords_section.split('\n')

        for line in lines:
            line = line.strip()
            if not line or line.startswith('//'):
                continue

            # Look for parentheses
            if '(' in line and ')' in line:
                # Extract content between parentheses
                start = line.find('(')
                end = line.find(')', start)
                if start != -1 and end != -1:
                    coord_str = line[start+1:end]

                    # Split by space or comma and filter out empty strings
                    coords = [x.strip() for x in re.split(r'[,\s]+', coord_str) if x.strip()]

                    if len(coords) == 3:
                        try:
                            x, y, z = float(coords[0]), float(coords[1]), float(coords[2])
                            points.append((x, y, z))
                        except ValueError:
                            continue

        return points

    def get_bounds(self) -> Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]]:
        """
        Get the bounding box of all points.

        Returns:
            Tuple containing (x_min, x_max), (y_min, y_max), (z_min, z_max)
        """
        if self.points is None:
            raise ValueError("Points not loaded. Call read_points() first.")

        x_bounds = (self.points[:, 0].min(), self.points[:, 0].max())
        y_bounds = (self.points[:, 1].min(), self.points[:, 1].max())
        z_bounds = (self.points[:, 2].min(), self.points[:, 2].max())

        return x_bounds, y_bounds, z_bounds

    def get_point_count(self) -> int:
        """
        Get the number of points.

        Returns:
            int: Number of points
        """
        if self.points is None:
            raise ValueError("Points not loaded. Call read_points() first.")

        return len(self.points)

    def save_to_csv(self, output_path: str, include_header: bool = True) -> None:
        """
        Save points to a CSV file.

        Args:
            output_path (str): Output CSV file path
            include_header (bool): Whether to include column headers
        """
        if self.points is None:
            raise ValueError("Points not loaded. Call read_points() first.")

        import csv

        with open(output_path, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)

            if include_header:
                writer.writerow(['x', 'y', 'z'])

            for point in self.points:
                writer.writerow(point)

    def print_summary(self) -> None:
        """
        Print a summary of the loaded points data.
        """
        if self.points is None:
            logger.warning("No points loaded. Call read_points() first.")
            return

        logger.info("=== OpenFOAM Points Summary ===")
        logger.info(f"File: {self.file_path}")
        logger.info(f"Number of points: {len(self.points)}")

        if self.header:
            logger.info("\nHeader information:")
            for key, value in self.header.items():
                logger.info(f"  {key}: {value}")

        x_bounds, y_bounds, z_bounds = self.get_bounds()
        logger.info(f"\nBounding box:")
        logger.info(f"  X: [{x_bounds[0]:.6f}, {x_bounds[1]:.6f}]")
        logger.info(f"  Y: [{y_bounds[0]:.6f}, {y_bounds[1]:.6f}]")
        logger.info(f"  Z: [{z_bounds[0]:.6f}, {z_bounds[1]:.6f}]")

        logger.info(f"\nFirst 5 points:")
        for i, point in enumerate(self.points[:5]):
            logger.info(f"  Point {i}: ({point[0]:.6f}, {point[1]:.6f}, {point[2]:.6f})")







class OpenFOAMFieldReader:
    def __init__(self, case_dir ):
        self.case_dir = case_dir

        self.points_reader   = None
        self.boundary_reader = None
        self.faces_reader    = None
        self.mesh_info = {}
    
        if os.path.exists(case_dir):
            pass
        else:
            logger.error(f"Case directory '{case_dir}' not found.")
            logger.error( "\\nExample usage:")
            logger.error( "1. Set case_dir to your OpenFOAM case directory")
            logger.error( "2. Set time_dir to the time step you want to analyze")
            logger.error( "3. Run the script")
            raise MissingPathError( f"Case directory '{case_dir}' not found.")
    
    def _read_openfoam_field(self, filepath):
        """
        Read OpenFOAM field file and extract data
        
        Args:
            filepath (str): Path to the OpenFOAM field file
            
        Returns:
            tuple: (field_type, field_data) where field_type is 'uniform' or 'nonuniform'
                   and field_data is either a float (uniform) or numpy array (nonuniform)
        """
        try:
            with open(filepath, 'r') as f:
                content = f.read()
            
            logger.info(f"Reading field file: {filepath}")
            
            # Extract basic file information
            self._extract_file_info(content)
            
            # Find the internalField section
            internal_start = content.find('internalField')
            if internal_start == -1:
                logger.warning(f"Warning: No internalField found in {filepath}")
                return None, None
            
            # Extract the field data section
            internal_section = content[internal_start:]
            semicolon_pos = internal_section.find(';')
            if semicolon_pos == -1:
                logger.warning(f"Warning: Could not find end of internalField in {filepath}")
                return None, None
                
            field_section = internal_section[:semicolon_pos]
            
            # Check if it's a nonuniform field
            if 'nonuniform' in field_section:
                return self._read_nonuniform_field(field_section)
            # Check if it's a uniform field
            elif 'uniform' in field_section:
                return self._read_uniform_field(field_section)
            
            
            else:
                logger.warning(f"Warning: Unknown field format in {filepath}")
                return None, None
                
        except Exception as e:
            logger.error(f"Error reading field file {filepath}: {e}")
            return None, None
    
    def _subdirectories( self ):
            case_path = Path(self.case_dir)
            if case_path.exists():
                time_dirs = [d.name for d in case_path.iterdir() 
                           if d.is_dir() and d.name.replace('.','').replace('-','').isdigit()]
                time_dirs.sort(key=float)
            return time_dirs
    
    def _extract_file_info(self, content):
        """Extract basic information from the OpenFOAM file header"""
        # Extract dimensions
        dim_match = re.search(r'dimensions\s*\[(.*?)\]', content)
        if dim_match:
            dimensions = dim_match.group(1).strip()
            logger.info(f"Field dimensions: [{dimensions}]")
        
        # Extract object name
        obj_match = re.search(r'object\s+(\w+)', content)
        if obj_match:
            object_name = obj_match.group(1)
            logger.info(f"Field object: {object_name}")
    
    def _read_uniform_field(self, field_section):
        """Read uniform field data (scalar or vector)"""
        # Try to match a vector first
        vector_match = re.search(r'uniform\s+\(\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s+([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s+([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*\)', field_section)
        if vector_match:
            try:
                x, y, z = float(vector_match.group(1)), float(vector_match.group(2)), float(vector_match.group(3))
                value = (x, y, z)
                logger.info(f"Uniform field vector value: {value}")
                return 'uniform', value
            except ValueError as e:
                logger.warning(f"Warning: Could not parse uniform vector value: {e}")
                return None, None
        
        # If not a vector, try to match a scalar
        scalar_match = re.search(r'uniform\s+([-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?)', field_section)
        if scalar_match:
            try:
                value = float(scalar_match.group(1))
                logger.info(f"Uniform field scalar value: {value}")
                return 'uniform', value
            except ValueError as e:
                logger.warning(f"Warning: Could not parse uniform scalar value: {e}")
                return None, None
        else:
            logger.warning("Warning: Could not find uniform value (scalar or vector)")
            return None, None
    
    def _read_nonuniform_field(self, field_section):
        """Read nonuniform field data (scalar or vector list)"""
        # Determine the type of list (scalar or vector)
        list_type_match = re.search(r'nonuniform\s+List<(scalar|vector)>\s*(\d+)', field_section)
        if not list_type_match:
            logger.warning("Warning: Could not determine list type (scalar/vector) in nonuniform field")
            return None, None
        
        data_type = list_type_match.group(1) # 'scalar' or 'vector'
        expected_size = int(list_type_match.group(2))
        logger.info(f"Nonuniform field type: {data_type}, expected size: {expected_size} values")
        
        # Find the data list
        list_start = field_section.find('(')
        list_end = field_section.rfind(')')
        
        if list_start == -1 or list_end == -1:
            logger.warning("Warning: Could not find field data list")
            return None, None
        
        # Extract values between parentheses
        values_str = field_section[list_start+1:list_end].strip()
        
        values = []
        if data_type == 'scalar':
            # Handle space-separated and newline-separated scalar values
            values_str = re.sub(r'\s+', ' ', values_str)  # Normalize whitespace
            value_strings = [x for x in values_str.split() if x.strip()]
            try:
                values = [float(x) for x in value_strings]
            except ValueError as e:
                logger.warning(f"Warning: Could not parse scalar field values: {e}")
                return None, None
        elif data_type == 'vector':
            # Find all vector triplets (e.g., (1.2 3.4 5.6))
            vector_pattern = r'\(\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s+([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s+([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*\)'
            for match in re.finditer(vector_pattern, values_str):
                try:
                    x, y, z = float(match.group(1)), float(match.group(2)), float(match.group(3))
                    values.append((x, y, z))
                except ValueError as e:
                    logger.warning(f"Warning: Could not parse vector triplet: {match.group(0)} - {e}")
                    return None, None
        
        values_array = np.array(values)
        
        logger.info(f"Read {len(values_array)} nonuniform {data_type} values")
        if expected_size and len(values_array) != expected_size:
            logger.warning(f"Warning: Expected {expected_size} values, got {len(values_array)}")
        
        return 'nonuniform', values_array
    
    def read_mesh_info(self, case_dir):
        """Read basic mesh information from polyMesh directory"""
        self.mesh_info = {}
        polymesh_dir = os.path.join(case_dir, 'constant', 'polyMesh')
        
        if not os.path.exists(polymesh_dir):
            logger.warning(f"Warning: polyMesh directory not found: {polymesh_dir}")
            return self.mesh_info
        
        logger.info(f"Reading mesh information from: {polymesh_dir}")
        
        # Read points
        points_file = os.path.join(polymesh_dir, 'points')
        if os.path.exists(points_file):
            self.points_reader = OpenFOAMPointsReader(points_file)
            self.points_reader.read_points()
            self.mesh_info['n_points'] = len(self.points_reader.points)

        # Initialize faces reader (but don't read yet to save time)
        faces_file = os.path.join(polymesh_dir, 'faces')
        if os.path.exists(faces_file):
            self.faces_reader = OpenFOAMFacesReader(case_dir, 'constant')

        # Read owner file to get number of cells and faces
        owner_file = os.path.join(polymesh_dir, 'owner')
        if os.path.exists(owner_file):
            n_faces, n_cells = self._analyze_owner_file(owner_file)
            self.mesh_info['n_faces'] = n_faces
            self.mesh_info['n_cells'] = n_cells
        
        # Read neighbour file
        neighbour_file = os.path.join(polymesh_dir, 'neighbour')
        if os.path.exists(neighbour_file):
            self.mesh_info['n_internal_faces'] = self._count_internal_faces(neighbour_file)
            if 'n_faces' in self.mesh_info:
                self.mesh_info['n_boundary_faces'] = self.mesh_info['n_faces'] - self.mesh_info['n_internal_faces']
        
        # Read boundary file
        boundary_file = os.path.join(polymesh_dir, 'boundary')
        if os.path.exists(boundary_file):
            self.mesh_info['boundary_patches'] = self._read_boundary_patches(boundary_file)
        
        # Print mesh summary
        logger.info("Mesh Information:")
        logger.info("-" * 30)
        for key, value in self.mesh_info.items():
            if key != 'boundary_patches':
                logger.info(f"  {key}: {value}")
        
        if 'boundary_patches' in self.mesh_info:
            logger.info("  Boundary patches:")
            for patch_name, patch_info in self.mesh_info['boundary_patches'].items():
                logger.info(f"    {patch_name}: {patch_info['nFaces']} faces")
        
        return self.mesh_info
    
    def _analyze_owner_file(self, owner_file):
        """Analyze owner file to get number of faces and cells"""
        try:
            with open(owner_file, 'r') as f:
                content = f.read()
            
            # Find the owner list
            list_start = content.find('(')
            list_end = content.rfind(')')
            
            if list_start != -1 and list_end != -1:
                owner_str = content[list_start+1:list_end].strip()
                owner_indices = [int(x) for x in owner_str.split()]
                n_faces = len(owner_indices)
                n_cells = max(owner_indices) + 1 if owner_indices else 0
                return n_faces, n_cells
            
        except Exception as e:
            logger.error(f"Warning: Could not read owner file: {e}")
        
        return None, None
    
    def _count_internal_faces(self, neighbour_file):
        """Count internal faces from neighbour file"""
        try:
            with open(neighbour_file, 'r') as f:
                content = f.read()
            
            # Find the neighbour list
            list_start = content.find('(')
            list_end = content.rfind(')')
            
            if list_start != -1 and list_end != -1:
                neighbour_str = content[list_start+1:list_end].strip()
                neighbour_indices = neighbour_str.split()
                return len(neighbour_indices)
            
        except Exception as e:
            logger.error(f"Warning: Could not read neighbour file: {e}")
        
        return None
    
    def _read_boundary_patches(self, boundary_file):
        """Read boundary patch information"""
        patches = {}
        try:
            with open(boundary_file, 'r') as f:
                content = f.read()
            
            # Simple parsing of boundary patches
            # Look for patch definitions
            patch_pattern = r'(\w+)\s*\{[^}]*nFaces\s+(\d+);[^}]*startFace\s+(\d+);[^}]*type\s+(\w+);[^}]*\}'
            matches = re.findall(patch_pattern, content, re.MULTILINE | re.DOTALL)
            
            for match in matches:
                patch_name, n_faces, start_face, patch_type = match
                patches[patch_name] = {
                    'nFaces': int(n_faces),
                    'startFace': int(start_face),
                    'type': patch_type
                }
            
        except Exception as e:
            logger.error(f"Warning: Could not read boundary file: {e}")
        
        return patches
    
    @staticmethod
    def analyze_field(field_type, field_data, time_value=None, mesh_info=None, debug=True):
        """
        Analyze field data and provide statistics
        
        Args:
            field_type (str): 'uniform' or 'nonuniform'
            field_data: Field data (float or numpy array)
            time_value (float): Time value for this field
            mesh_info (dict): Mesh information dictionary
            
        Returns:
            dict: Analysis results
        """
        analysis = {
            'field_type': field_type,
            'time': time_value
        }
        
        if field_type == 'uniform':
            analysis['temperature'] = field_data
            analysis['min_temp'] = field_data
            analysis['max_temp'] = field_data
            analysis['avg_temp'] = field_data
            analysis['std_temp'] = 0.0
            analysis['n_cells'] = mesh_info.get('n_cells', 'unknown') if mesh_info else 'unknown'
            
            if debug:
                logger.info(f"Uniform temperature field:")
                logger.info(f"  Temperature: {field_data:.6f} K")
                if mesh_info and 'n_cells' in mesh_info:
                    logger.info(f"  Applied to {mesh_info['n_cells']} cells")
            
        elif field_type == 'nonuniform':
            analysis['temperature'] = field_data
            analysis['min_temp'] = np.min(field_data)
            analysis['max_temp'] = np.max(field_data)
            analysis['avg_temp'] = np.mean(field_data)
            analysis['std_temp'] = np.std(field_data)
            analysis['n_values'] = len(field_data)
            
            if debug:
                logger.info(f"Nonuniform temperature field:")
                logger.info(f"  Number of values: {len(field_data)}")
                logger.info(f"  Temperature range: {analysis['min_temp']:.6f} to {analysis['max_temp']:.6f} K")
                logger.info(f"  Average temperature: {analysis['avg_temp']:.6f} K")
                logger.info(f"  Standard deviation: {analysis['std_temp']:.6f} K")
            
            # Check if field size matches mesh
                if mesh_info and 'n_cells' in mesh_info:
                    if len(field_data) == mesh_info['n_cells']:
                        logger.info(f"  ✓ Field size matches number of cells ({mesh_info['n_cells']})")
                    else:
                        logger.error(f"  ⚠ Field size ({len(field_data)}) != number of cells ({mesh_info['n_cells']})")
        
        return analysis
    
    
    def save_field_data(self, field_type, field_data, analysis, output_file):
        """Save field data to file"""
        try:
            with open(output_file, 'w') as f:
                f.write("# OpenFOAM Temperature Field Data\\n")
                f.write(f"# Field type: {field_type}\\n")
                if analysis.get('time') is not None:
                    f.write(f"# Time: {analysis['time']} s\\n")
                f.write(f"# Min temperature: {analysis['min_temp']:.6f} K\\n")
                f.write(f"# Max temperature: {analysis['max_temp']:.6f} K\\n")
                f.write(f"# Average temperature: {analysis['avg_temp']:.6f} K\\n")
                f.write(f"# Standard deviation: {analysis['std_temp']:.6f} K\\n")
                f.write("#\\n")
                
                if field_type == 'uniform':
                    f.write("# Uniform field - single value\\n")
                    f.write(f"{field_data:.10f}\\n")
                else:
                    f.write("# Cell_Index, Temperature(K)\\n")
                    for i, temp in enumerate(field_data):
                        f.write(f"{i}, {temp:.10f}\\n")
            
            logger.info(f"Field data saved to: {output_file}")
            
        except Exception as e:
            logger.error(f"Error saving field data: {e}")


    def _cell_to_point_interpolation(self, field_data: np.ndarray, method: str = 'average') -> np.ndarray:
        """
        Convert cell-centered field values to point values using interpolation.

        This method requires mesh connectivity information (owner, neighbour, faces).
        It computes point values by averaging the values from all cells that share that point.

        Args:
            field_data (np.ndarray): Cell-centered field values (length = n_cells)
            method (str): Interpolation method. Options:
                - 'average': Simple arithmetic average of surrounding cells
                - 'inverse_distance': Weighted by inverse distance (future enhancement)

        Returns:
            np.ndarray: Point values (length = n_points)

        Raises:
            ValueError: If mesh information is not loaded or field size doesn't match cells
        """
        # Validate inputs
        if self.points_reader is None or self.points_reader.points is None:
            raise ValueError("Points not loaded. Ensure read_mesh_info() has been called first.")

        if self.faces_reader is None or self.faces_reader.faces is None:
            # Try to read faces if not already loaded
            polymesh_dir = os.path.join(self.case_dir, 'constant', 'polyMesh')
            faces_file = os.path.join(polymesh_dir, 'faces')
            if os.path.exists(faces_file):
                logger.info("Loading faces for interpolation...")
                self.faces_reader = OpenFOAMFacesReader(self.case_dir, 'constant')
                self.faces_reader.read_faces()
            else:
                raise ValueError("Faces not loaded and faces file not found.")

        if 'n_cells' not in self.mesh_info:
            raise ValueError("Mesh information not complete. Call read_mesh_info() first.")

        if len(field_data) != self.mesh_info['n_cells']:
            raise ValueError(f"Field data size ({len(field_data)}) doesn't match number of cells ({self.mesh_info['n_cells']})")

        n_points = len(self.points_reader.points)
        n_cells = self.mesh_info['n_cells']

        logger.info(f"Interpolating from {n_cells} cells to {n_points} points...")

        # Read owner file to get cell-face connectivity
        owner_file = os.path.join(self.case_dir, 'constant', 'polyMesh', 'owner')
        if not os.path.exists(owner_file):
            raise FileNotFoundError(f"Owner file not found: {owner_file}")

        with open(owner_file, 'r') as f:
            content = f.read()

        # Parse owner data
        list_start = content.find('(')
        list_end = content.rfind(')')
        if list_start == -1 or list_end == -1:
            raise ValueError("Could not parse owner file")

        owner_str = content[list_start+1:list_end].strip()
        owner_data = np.array([int(x) for x in owner_str.split()])

        # Read neighbour file for internal faces
        neighbour_file = os.path.join(self.case_dir, 'constant', 'polyMesh', 'neighbour')
        neighbour_data = None
        if os.path.exists(neighbour_file):
            with open(neighbour_file, 'r') as f:
                content = f.read()
            list_start = content.find('(')
            list_end = content.rfind(')')
            if list_start != -1 and list_end != -1:
                neighbour_str = content[list_start+1:list_end].strip()
                neighbour_data = np.array([int(x) for x in neighbour_str.split()])

        # Build point-to-cells connectivity
        # For each point, store list of cells that use it
        point_to_cells = [set() for _ in range(n_points)]

        faces = self.faces_reader.faces

        # Process all faces
        for face_idx, face in enumerate(faces):
            cell_owner = owner_data[face_idx]

            # Add owner cell to all points in this face
            for point_idx in face:
                if point_idx < n_points:
                    point_to_cells[point_idx].add(cell_owner)

            # Add neighbour cell for internal faces
            if neighbour_data is not None and face_idx < len(neighbour_data):
                cell_neighbour = neighbour_data[face_idx]
                for point_idx in face:
                    if point_idx < n_points:
                        point_to_cells[point_idx].add(cell_neighbour)

        # Compute point values based on method
        point_values = np.zeros(n_points)

        if method == 'average':
            for point_idx in range(n_points):
                connected_cells = list(point_to_cells[point_idx])
                if len(connected_cells) > 0:
                    # Simple average of all connected cell values
                    point_values[point_idx] = np.mean([field_data[cell_idx] for cell_idx in connected_cells])
                else:
                    logger.warning(f"Point {point_idx} has no connected cells")
                    point_values[point_idx] = 0.0

        else:
            raise ValueError(f"Unknown interpolation method: {method}")

        logger.info(f"Interpolation complete. Point value range: [{np.min(point_values):.6f}, {np.max(point_values):.6f}]")

        return point_values


    def point_history( self, field_name, point_idx ):
        """
        """

        logger.info("OpenFOAM Temperature History Reader")
        logger.info("=" * 40)
        
        # Example usage
        #reader = OpenFOAMFieldReader( case_dir )
        
        # Check if case exists
        logger.info(f"\\nAnalyzing case: {self.case_dir}")
        
        # Read mesh information
        mesh_info = self.read_mesh_info(self.case_dir)
        logger.info('\n')
        
        subdirs = self._subdirectories()

        # Read temperature field at specific time
        idx, val_vec = 0, np.zeros( len(subdirs), dtype=float )
        logger.debug(f"Reading temperature subdirs: {subdirs}")
        for time_dir in subdirs:
            of_file = os.path.join(self.case_dir, time_dir, field_name)
            
            if os.path.exists(of_file):
                logger.info(f"Reading temperature field at t = {time_dir} s")
                
                # Read the field
                field_type, field_data = self._read_openfoam_field(of_file)
                
                if field_type == 'uniform':
                    val_vec[idx] = field_data
                    pass
                elif field_type == 'nonuniform':
                    val_vec[idx] = field_data[point_idx]
                idx = idx+1
            else:
                logger.error(f"Temperature file not found: {of_file}")
        
        return val_vec # no time values yet




    def field( self, field_name, time_dir = "50", location=Location.UNKNOWN ):
        """Main function to demonstrate the field reader

        # You can specify a case directory and time
        time_dir : Change this to the time you want to analyze
        """

        logger.info("\nOpenFOAM Field Reader")
        logger.info("=" * 40)

        if time_dir == -1 or time_dir == "-1":
            subdirs = self._subdirectories()
            if subdirs:
                time_dir = subdirs[-1]
                logger.info(f"Using latest time directory: {time_dir}")
            else:
                logger.error("No time directories found to determine the latest time.")
                return None, None
        
        if field_name == 'T' or \
           field_name == 'U' or \
           field_name == 'p':
            extract_location=Location.CELL
        #elif field_name == 'ZZZZZZ':
        #    extract_location=Location.NODAL
        else:
            raise KuneneError( f'Unknown location in file for {field_name}. Needs implementation in openfoam_reader.py.' )

        # Check if case exists
        if os.path.exists(self.case_dir):
            logger.info(f"\\nAnalyzing case: {self.case_dir}")
            
            # Read mesh information
            mesh_info = self.read_mesh_info(self.case_dir)
            logger.info('\n')
            
            # Read field at specific time
            of_file = os.path.join(self.case_dir, time_dir, field_name )
            
            if os.path.exists(of_file):
                logger.info(f"Reading temperature field at t = {time_dir} s")
                logger.info("-" * 50)
                
                # Read the field
                field_type, field_data = self._read_openfoam_field(of_file)
                
                #if field_type == 'uniform': == 'nonuniform':

                if location == Location.NODAL and extract_location == Location.CELL:
                    field_data = self._cell_to_point_interpolation(field_data)

                assert not(location == Location.CELL and extract_location == Location.NODAL), \
                    '*** ERROR Conversion NYI'

                return field_type, field_data

                    
            else:
                logger.error(f"Temperature file not found: {of_file}")
                logger.error("\\nAvailable time directories:")
                case_path = Path(self.case_dir)
                if case_path.exists():
                    time_dirs = [d.name for d in case_path.iterdir() 
                               if d.is_dir() and d.name.replace('.','').replace('-','').isdigit()]
                    time_dirs.sort(key=float)
                    for t in time_dirs[:10]:  # Show first 10
                        logger.error(f"  {t}")
                    if len(time_dirs) > 10:
                        logger.error(f"  ... and {len(time_dirs)-10} more")
                    return None, None
        
        else:
            logger.error(f"Case directory '{self.case_dir}' not found.")
            logger.error("\\nExample usage:")
            logger.error("1. Set case_dir to your OpenFOAM case directory")
            logger.error("2. Set time_dir to the time step you want to analyze")
            logger.error("3. Run the script")
            raise MissingPathError( f"Case directory '{self.case_dir}' not found.")


        return None, None





class OpenFOAMBoundaryReader:
    """
    A class to read OpenFOAM boundary files and extract boundary patch information.

    OpenFOAM boundary files typically contain:
    - Header information
    - Number of boundary patches
    - Dictionary of boundary patches with their properties
    """

    def __init__(self, file_path: str):
        """
        Initialize the boundary reader.

        Args:
            file_path (str): Path to the OpenFOAM boundary file
        """
        self.file_path = file_path
        self.boundaries = {}
        self.header = {}
        self.n_patches = 0

    def read_boundary(self) -> Dict[str, Dict[str, Any]]:
        """
        Read boundary patches from the OpenFOAM boundary file.

        Returns:
            Dict[str, Dict[str, Any]]: Dictionary of boundary patches with their properties
        """
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"Boundary file not found: {self.file_path}")

        with open(self.file_path, 'r') as file:
            content = file.read()

        # Parse header information
        self._parse_header(content)

        # Extract number of patches
        self.n_patches = self._extract_patch_count(content)

        # Extract boundary patches
        self.boundaries = self._extract_boundaries(content)

        return self.boundaries

    def _parse_header(self, content: str) -> None:
        """
        Parse the header section of the OpenFOAM file.

        Args:
            content (str): File content as string
        """
        # Extract FoamFile dictionary
        foam_file_match = re.search(r'FoamFile\s*\{([^}]*)\}', content, re.DOTALL)
        if foam_file_match:
            foam_file_content = foam_file_match.group(1)

            # Parse key-value pairs in the header
            for line in foam_file_content.split('\n'):
                line = line.strip()
                if line and not line.startswith('//'):
                    # Remove semicolons and split on whitespace
                    line = line.rstrip(';')
                    parts = line.split(None, 1)
                    if len(parts) == 2:
                        self.header[parts[0]] = parts[1].strip('"')

    def _extract_patch_count(self, content: str) -> int:
        """
        Extract the number of boundary patches from the file content.

        Args:
            content (str): File content as string

        Returns:
            int: Number of boundary patches
        """
        # Look for the number before the opening parenthesis (after the header)
        # Skip the FoamFile section first
        foam_file_end = content.find('}')
        if foam_file_end != -1:
            content_after_header = content[foam_file_end+1:]
        else:
            content_after_header = content

        count_pattern = r'(\d+)\s*\('
        match = re.search(count_pattern, content_after_header)

        if not match:
            raise ValueError("Could not find boundary patch count in the file")

        return int(match.group(1))

    def _extract_boundaries(self, content: str) -> Dict[str, Dict[str, Any]]:
        """
        Extract boundary patch information from the file content.

        Args:
            content (str): File content as string

        Returns:
            Dict[str, Dict[str, Any]]: Dictionary of boundary patches
        """
        boundaries = {}

        # Find the main boundary section
        # Look for pattern: number followed by opening parenthesis
        start_pattern = r'(\d+)\s*\('
        start_match = re.search(start_pattern, content)

        if not start_match:
            raise ValueError("Could not find boundary section in the file")

        # Find the position after the number and opening parenthesis
        start_pos = start_match.end()

        # Find the matching closing parenthesis
        paren_count = 1
        pos = start_pos
        boundary_end = -1

        while pos < len(content) and paren_count > 0:
            if content[pos] == '(':
                paren_count += 1
            elif content[pos] == ')':
                paren_count -= 1
                if paren_count == 0:
                    boundary_end = pos
                    break
            pos += 1

        if boundary_end == -1:
            raise ValueError("Could not find boundary section end in the file")

        # Extract the boundary section
        boundary_section = content[start_pos:boundary_end]

        # Parse individual boundary patches
        boundaries = self._parse_boundary_patches(boundary_section)

        return boundaries

    def _parse_boundary_patches(self, boundary_section: str) -> Dict[str, Dict[str, Any]]:
        """
        Parse individual boundary patches from the boundary section.

        Args:
            boundary_section (str): The boundary section content

        Returns:
            Dict[str, Dict[str, Any]]: Dictionary of parsed boundary patches
        """
        boundaries = {}

        # Remove comments
        lines = boundary_section.split('\n')
        cleaned_lines = []
        for line in lines:
            comment_pos = line.find('//')
            if comment_pos != -1:
                line = line[:comment_pos]
            cleaned_lines.append(line)

        cleaned_content = '\n'.join(cleaned_lines)

        # Find boundary patch definitions
        # Pattern: patch_name followed by {patch_definition}
        patch_pattern = r'(\w+)\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}'

        for match in re.finditer(patch_pattern, cleaned_content):
            patch_name = match.group(1).strip()
            patch_content = match.group(2).strip()

            # Parse the patch properties
            patch_properties = self._parse_patch_properties(patch_content)
            boundaries[patch_name] = patch_properties

        return boundaries

    def _parse_patch_properties(self, patch_content: str) -> Dict[str, Any]:
        """
        Parse properties of a single boundary patch.

        Args:
            patch_content (str): Content of a single patch definition

        Returns:
            Dict[str, Any]: Dictionary of patch properties
        """
        properties = {}

        # Split into lines and process each line
        lines = patch_content.split('\n')

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Remove semicolon if present
            line = line.rstrip(';')

            # Handle different types of property definitions
            if self._is_key_value_line(line):
                key, value = self._parse_key_value_line(line)
                properties[key] = value

        return properties

    def _is_key_value_line(self, line: str) -> bool:
        """
        Check if a line contains a key-value pair.

        Args:
            line (str): Line to check

        Returns:
            bool: True if line contains key-value pair
        """
        # Skip lines that are only braces or empty
        if not line or line in ['{', '}']:
            return False

        # Look for pattern: word followed by value
        return bool(re.match(r'\s*\w+\s+.+', line))

    def _parse_key_value_line(self, line: str) -> tuple:
        """
        Parse a key-value line.

        Args:
            line (str): Line containing key-value pair

        Returns:
            tuple: (key, value) pair
        """
        # Split on first whitespace
        parts = line.split(None, 1)

        if len(parts) != 2:
            return parts[0], "" if len(parts) == 1 else ("", "")

        key = parts[0]
        value_str = parts[1].strip()

        # Parse the value based on its format
        value = self._parse_value(value_str)

        return key, value

    def _parse_value(self, value_str: str) -> Union[str, int, float, List[Any]]:
        """
        Parse a value string into appropriate Python type.

        Args:
            value_str (str): String representation of the value

        Returns:
            Union[str, int, float, List[Any]]: Parsed value
        """
        value_str = value_str.strip().strip('"').strip("'")

        # Try to parse as number
        try:
            if '.' in value_str or 'e' in value_str.lower():
                return float(value_str)
            else:
                return int(value_str)
        except ValueError:
            pass

        # Check if it's a list/array (parentheses or brackets)
        if (value_str.startswith('(') and value_str.endswith(')')) or \
           (value_str.startswith('[') and value_str.endswith(']')):
            return self._parse_list(value_str)

        # Return as string
        return value_str

    def _parse_list(self, list_str: str) -> List[Any]:
        """
        Parse a list/array string.

        Args:
            list_str (str): String representation of a list

        Returns:
            List[Any]: Parsed list
        """
        # Remove outer parentheses or brackets
        inner = list_str[1:-1].strip()

        if not inner:
            return []

        # Split by whitespace or comma
        elements = re.split(r'[,\s]+', inner)

        parsed_elements = []
        for element in elements:
            element = element.strip()
            if element:
                parsed_elements.append(self._parse_value(element))

        return parsed_elements

    def get_patch_names(self) -> List[str]:
        """
        Get list of all boundary patch names.

        Returns:
            List[str]: List of patch names
        """
        return list(self.boundaries.keys())

    def get_patch_types(self) -> Dict[str, str]:
        """
        Get dictionary mapping patch names to their types.

        Returns:
            Dict[str, str]: Dictionary of patch name -> type
        """
        patch_types = {}
        for name, properties in self.boundaries.items():
            patch_types[name] = properties.get('type', 'unknown')

        return patch_types

    def get_patch_info(self, patch_name: str) -> Optional[Dict[str, Any]]:
        """
        Get information for a specific patch.

        Args:
            patch_name (str): Name of the patch

        Returns:
            Optional[Dict[str, Any]]: Patch information or None if not found
        """
        return self.boundaries.get(patch_name)

    def get_patches_by_type(self, patch_type: str) -> Dict[str, Dict[str, Any]]:
        """
        Get all patches of a specific type.

        Args:
            patch_type (str): Type of patches to retrieve

        Returns:
            Dict[str, Dict[str, Any]]: Dictionary of patches of the specified type
        """
        patches = {}
        for name, properties in self.boundaries.items():
            if properties.get('type') == patch_type:
                patches[name] = properties

        return patches

    def print_summary(self) -> None:
        """
        Print a summary of the boundary file contents.
        """
        logger.info("=== OpenFOAM Boundary Summary ===")
        logger.info(f"File: {self.file_path}")
        logger.info(f"Number of patches: {len(self.boundaries)}")

        if self.header:
            logger.info("\nHeader information:")
            for key, value in self.header.items():
                logger.info(f"  {key}: {value}")

        logger.info(f"\nBoundary patches:")
        for name, properties in self.boundaries.items():
            patch_type = properties.get('type', 'unknown')
            nFaces = properties.get('nFaces', 'N/A')
            startFace = properties.get('startFace', 'N/A')
            logger.info(f"  {name}:")
            logger.info(f"    type: {patch_type}")
            logger.info(f"    nFaces: {nFaces}")
            logger.info(f"    startFace: {startFace}")

        # Summary by type
        types = {}
        for properties in self.boundaries.values():
            patch_type = properties.get('type', 'unknown')
            types[patch_type] = types.get(patch_type, 0) + 1

        logger.info(f"\nPatch types summary:")
        for patch_type, count in types.items():
            logger.info(f"  {patch_type}: {count}")


def test_OpenFOAMBoundaryReader():
    """
    Example usage of the OpenFOAM boundary reader.
    """
    # Example usage
    boundary_file = "constant/polyMesh/boundary"  # Typical OpenFOAM boundary file location

    try:
        # Create reader instance
        reader = OpenFOAMBoundaryReader(boundary_file)

        # Read the boundary file
        boundaries = reader.read_boundary()

        # Print summary
        reader.print_summary()

        # Access specific patch information
        patch_names = reader.get_patch_names()
        logger.info(f"\nAll patch names: {patch_names}")

        # Get patch types
        patch_types = reader.get_patch_types()
        logger.info(f"\nPatch types: {patch_types}")

        # Get specific patch info
        if patch_names:
            first_patch = patch_names[0]
            patch_info = reader.get_patch_info(first_patch)
            logger.info(f"\nInfo for patch '{first_patch}':")
            for key, value in patch_info.items():
                logger.info(f"  {key}: {value}")

        # Get patches by type
        wall_patches = reader.get_patches_by_type('wall')
        if wall_patches:
            logger.info(f"\nWall patches: {list(wall_patches.keys())}")

    except FileNotFoundError:
        logger.error(f"Boundary file not found: {boundary_file}")
        logger.error("Make sure you're in the correct OpenFOAM case directory.")
    except Exception as e:
        logger.error(f"Error reading boundary file: {e}")

class OpenFOAMFacesReader:
    """
    A class to read and parse OpenFOAM faces files.
    The faces file contains connectivity information between faces and points.
    """

    def __init__(self, case_path: str, time_dir: str = "constant"):
        """
        Initialize the faces reader.

        Parameters:
        -----------
        case_path : str
            Path to the OpenFOAM case directory
        time_dir : str
            Time directory containing the mesh (usually "constant")
        """
        self.case_path = case_path
        self.time_dir = time_dir
        self.faces_file_path = os.path.join(case_path, time_dir, "polyMesh", "faces")
        self.faces = None

    def read_faces(self) -> List[List[int]]:
        """
        Read the faces file and return face-to-point connectivity.

        Returns:
        --------
        List[List[int]]: List of faces, where each face is a list of point indices
        """

        if not os.path.exists(self.faces_file_path):
            raise FileNotFoundError(f"Faces file not found: {self.faces_file_path}")

        logger.info(f"Reading faces from: {self.faces_file_path}")

        with open(self.faces_file_path, 'r') as f:
            content = f.read()

        # Remove comments and clean up
        content = self._remove_comments(content)

        # Find the start of the face list
        # Look for the number of faces followed by opening parenthesis
        pattern = r'(\d+)\s*\('
        match = re.search(pattern, content)

        if not match:
            raise ValueError("Could not find face count and opening parenthesis in faces file")

        num_faces = int(match.group(1))
        start_pos = match.end() - 1  # Position of opening parenthesis

        logger.info(f"Number of faces: {num_faces}")

        # Extract the face data
        faces = self._parse_face_list(content, start_pos, num_faces)

        self.faces = faces
        return faces

    def _remove_comments(self, content: str) -> str:
        """Remove C++ style comments from the content."""
        # Remove single line comments
        content = re.sub(r'//.*$', '', content, flags=re.MULTILINE)
        # Remove multi-line comments
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        return content

    def _parse_face_list(self, content: str, start_pos: int, num_faces: int) -> List[List[int]]:
        """
        Parse the face list from the content string.

        Parameters:
        -----------
        content : str
            The file content
        start_pos : int
            Starting position of the face list
        num_faces : int
            Expected number of faces

        Returns:
        --------
        List[List[int]]: List of faces
        """

        faces = []
        pos = start_pos + 1  # Skip opening parenthesis

        # Find the end of the list
        paren_count = 1
        end_pos = start_pos + 1

        while end_pos < len(content) and paren_count > 0:
            if content[end_pos] == '(':
                paren_count += 1
            elif content[end_pos] == ')':
                paren_count -= 1
            end_pos += 1

        # Extract the face data section
        face_data = content[pos:end_pos-1]

        # Parse individual faces
        face_pattern = r'(\d+)\s*\(\s*([\d\s]+)\s*\)'
        face_matches = re.findall(face_pattern, face_data)

        for face_match in face_matches:
            face_size = int(face_match[0])
            point_indices_str = face_match[1]

            # Parse point indices
            point_indices = [int(x) for x in point_indices_str.split()]

            if len(point_indices) != face_size:
                logger.warning(f"Face size mismatch. Expected {face_size}, got {len(point_indices)}")

            faces.append(point_indices)

        logger.info(f"Successfully parsed {len(faces)} faces")

        if len(faces) != num_faces:
            logger.warning(f"Face count mismatch. Expected {num_faces}, got {len(faces)}")

        return faces

    def get_face_statistics(self) -> dict:
        """
        Get statistics about the faces.

        Returns:
        --------
        dict: Dictionary containing face statistics
        """

        if self.faces is None:
            raise ValueError("Faces not loaded. Call read_faces() first.")

        face_sizes = [len(face) for face in self.faces]

        stats = {
            'total_faces': len(self.faces),
            'min_face_size': min(face_sizes),
            'max_face_size': max(face_sizes),
            'mean_face_size': np.mean(face_sizes),
            'face_size_distribution': {}
        }

        # Count faces by size
        unique_sizes, counts = np.unique(face_sizes, return_counts=True)
        for size, count in zip(unique_sizes, counts):
            stats['face_size_distribution'][int(size)] = int(count)

        return stats

    def get_face_by_index(self, face_index: int) -> List[int]:
        """
        Get a specific face by its index.

        Parameters:
        -----------
        face_index : int
            Index of the face (0-based)

        Returns:
        --------
        List[int]: List of point indices for the face
        """

        if self.faces is None:
            raise ValueError("Faces not loaded. Call read_faces() first.")

        if face_index < 0 or face_index >= len(self.faces):
            raise IndexError(f"Face index {face_index} out of range [0, {len(self.faces)-1}]")

        return self.faces[face_index]

    def find_faces_using_point(self, point_index: int) -> List[int]:
        """
        Find all faces that use a specific point.

        Parameters:
        -----------
        point_index : int
            Index of the point

        Returns:
        --------
        List[int]: List of face indices that use this point
        """

        if self.faces is None:
            raise ValueError("Faces not loaded. Call read_faces() first.")

        face_indices = []
        for i, face in enumerate(self.faces):
            if point_index in face:
                face_indices.append(i)

        return face_indices

    def export_faces_info(self, output_file: str = "faces_info.txt"):
        """
        Export face information to a text file.

        Parameters:
        -----------
        output_file : str
            Output file name
        """

        if self.faces is None:
            raise ValueError("Faces not loaded. Call read_faces() first.")

        stats = self.get_face_statistics()

        with open(output_file, 'w') as f:
            f.write("OpenFOAM Faces Information\n")
            f.write("=" * 40 + "\n\n")

            f.write(f"Total faces: {stats['total_faces']}\n")
            f.write(f"Face size range: {stats['min_face_size']} - {stats['max_face_size']}\n")
            f.write(f"Average face size: {stats['mean_face_size']:.2f}\n\n")

            f.write("Face size distribution:\n")
            for size, count in sorted(stats['face_size_distribution'].items()):
                percentage = (count / stats['total_faces']) * 100
                f.write(f"  {size} points: {count} faces ({percentage:.1f}%)\n")

            f.write(f"\nFirst 10 faces:\n")
            for i in range(min(10, len(self.faces))):
                f.write(f"Face {i}: {self.faces[i]}\n")

        logger.info(f"Face information exported to {output_file}")

def test_OpenFOAMFacesReader():
    """
    Main function to demonstrate usage.
    """

    # Example usage - update with your OpenFOAM case path
    case_path = "/path/to/your/openfoam/case"  # Update this path

    if not os.path.exists(case_path):
        logger.error(f"Case path '{case_path}' does not exist.")
        logger.error("Please update the case_path variable with your OpenFOAM case directory.")
        return

    try:
        # Create reader instance
        reader = OpenFOAMFacesReader(case_path)

        # Read faces
        faces = reader.read_faces()

        # Get and display statistics
        stats = reader.get_face_statistics()
        logger.info("\n=== Face Statistics ===")
        logger.info(f"Total faces: {stats['total_faces']}")
        logger.info(f"Face size range: {stats['min_face_size']} - {stats['max_face_size']}")
        logger.info(f"Average face size: {stats['mean_face_size']:.2f}")

        logger.info("\nFace size distribution:")
        for size, count in sorted(stats['face_size_distribution'].items()):
            percentage = (count / stats['total_faces']) * 100
            logger.info(f"  {size} points: {count} faces ({percentage:.1f}%)")

        # Example: Show first few faces
        logger.info(f"\nFirst 5 faces:")
        for i in range(min(5, len(faces))):
            face = reader.get_face_by_index(i)
            logger.info(f"Face {i}: {face} (size: {len(face)})")

        # Example: Find faces using point 0
        faces_using_point_0 = reader.find_faces_using_point(0)
        logger.info(f"\nFaces using point 0: {len(faces_using_point_0)} faces")
        logger.info(f"First few: {faces_using_point_0[:5]}")

        # Export information
        reader.export_faces_info()

    except Exception as e:
        logger.error(f"Error: {e}")


def test_OpenFOAMFieldReader( case_dir = "heatTransferBlock", time_dir = "50" ):
    """Main function to demonstrate the field reader

    # You can specify a case directory and time
    case_dir : Change this to your case directory
    time_dir : Change this to the time you want to analyze
    """

    logger.info("OpenFOAM Temperature Field Reader")
    logger.info("=" * 40)
    
    # Example usage
    reader = OpenFOAMFieldReader( case_dir )
    
    # Check if case exists
    if os.path.exists(case_dir):
        logger.info(f"\\nAnalyzing case: {case_dir}")
        
        # Read mesh information
        mesh_info = reader.read_mesh_info(case_dir)
        logger.info()
        
        # Read temperature field at specific time
        T_file = os.path.join(case_dir, time_dir, "T")
        
        if os.path.exists(T_file):
            logger.info(f"Reading temperature field at t = {time_dir} s")
            logger.info("-" * 50)
            
            # Read the field
            field_type, field_data = reader._read_openfoam_field(T_file)
            
            if field_type is not None:
                # Analyze the field
                analysis = OpenFOAMFieldReader.analyze_field(
                    field_type, field_data, float(time_dir), mesh_info, debug=True
                )
                
                logger.info()
                
                # Create plots for nonuniform fields
                if field_type == 'nonuniform':
                    plot_file = f"temperature_field_t_{time_dir}.png"
                    reader.plot_temperature_distribution(
                        field_type, field_data, float(time_dir), plot_file
                    )
                
                # Save data
                data_file = f"temperature_data_t_{time_dir}.csv"
                reader.save_field_data(field_type, field_data, analysis, data_file)
                
        else:
            logger.info(f"Temperature file not found: {T_file}")
            logger.info("\\nAvailable time directories:")
            case_path = Path(case_dir)
            if case_path.exists():
                time_dirs = [d.name for d in case_path.iterdir() 
                           if d.is_dir() and d.name.replace('.','').replace('-','').isdigit()]
                time_dirs.sort(key=float)
                for t in time_dirs[:10]:  # Show first 10
                    logger.info(f"  {t}")
                if len(time_dirs) > 10:
                    logger.info(f"  ... and {len(time_dirs)-10} more")

    else:
        logger.error(f"Case directory '{case_dir}' not found.")
        logger.error("\\nExample usage:")
        logger.error("1. Set case_dir to your OpenFOAM case directory")
        logger.error("2. Set time_dir to the time step you want to analyze")
        logger.error("3. Run the script")

    logger.info("\\n" + "=" * 40)
    logger.info("Analysis complete!")



def test_OpenFOAMPointsReader():
    """
    Example usage of the OpenFOAM points reader.
    """
    # Example usage
    points_file = "constant/polyMesh/points"  # Typical OpenFOAM points file location

    try:
        # Create reader instance
        reader = OpenFOAMPointsReader(points_file)

        # Read the points
        points = reader.read_points()

        # Print summary
        reader.print_summary()

        # Access points data
        logger.info(f"\nPoints shape: {points.shape}")
        logger.info(f"Data type: {points.dtype}")

        # Save to CSV (optional)
        # reader.save_to_csv("points.csv")
        # print("Points saved to points.csv")

    except FileNotFoundError:
        logger.error(f"Points file not found: {points_file}")
        logger.error("Make sure you're in the correct OpenFOAM case directory.")
    except Exception as e:
        logger.error(f"Error reading points file: {e}")



if __name__ == "__main__":
    test_OpenFOAMBoundaryReader()
    test_OpenFOAMPointsReader()
    test_OpenFOAMFieldReader()
    test_OpenFOAMFacesReader()
    v = FOAM_T_Max( "heatTransferBlock", "50" )
    print(f"max {v}")
