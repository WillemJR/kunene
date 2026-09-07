
import unittest
import numpy as np

import read_vtk

class TestVTK_Read( unittest.TestCase ):

    def setUp(self):
        fname = 'test.vtk'
        required_part_id = 3
        self.node_data, self.el_data = \
               read_vtk.read_part_mesh( fname, required_part_id,
                                        node_data_names = [ 'NODE_ID' ],
                                        el_data_names = [ 'ELEMENT_ID' ] )

    def test_nodes( self ):
        correct_loc = np.array( [[1., 0., 0.],
                                 [1., 1., 0.],
                                 [0., 0., 0.],
                                 [0., 1., 0.0]] )
        diff = self.node_data['coords'] - correct_loc 
        self.assertAlmostEqual( np.sum(abs( diff)), 0., places=6 )

    def test_els( self ):
        conn = np.array([2, 0, 1, 2, 3, 1] )
        conn_off = np.array(  [0, 3] )
        diff = self.el_data['cell_conns'] - conn 
        self.assertAlmostEqual( np.sum(abs( diff)), 0., places=1 )
        diff = self.el_data['cell_conn_offset'] - conn_off
        self.assertAlmostEqual( np.sum(abs( diff)), 0., places=1 )

    def test_data( self ):
        nids = np.array([5,6,7,8] )
        elids = np.array(  [12, 13] )
        diff = self.node_data['data'][ 'NODE_ID' ] - nids 
        self.assertAlmostEqual( np.sum(abs( diff)), 0., places=1 )
        diff = self.el_data['data'][ 'ELEMENT_ID' ] - elids
        self.assertAlmostEqual( np.sum(abs( diff)), 0., places=1 )

if __name__ == '__main__':
    unittest.main()


