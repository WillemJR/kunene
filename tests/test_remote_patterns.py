import unittest
import threading
import time
import os
import shutil
import json
import tempfile
from pathlib import Path

import grpc
import numpy as np

from kunene.actions import WorkAction
from kunene.args import STATUS_PATH
from kunene.graph_actions import WorkFlow
from kunene.progress import FileProgressTail
from kunene.protos import remote_actions_pb2, remote_actions_pb2_grpc
from kunene.remote_actions import RemoteAction, ServerAction
from kunene.util import solver_progress

class FileGenTask(WorkAction):
    def solve(self, val_dict=None):
        # Create dummy files
        with open('data.txt', 'w') as f: f.write('data')
        with open('image.png', 'w') as f: f.write('image')
        with open('log.log', 'w') as f: f.write('log')
        return True

class EchoTask(WorkAction):
    """Returns its inputs plus a numpy array, exercising the restricted
    JSON serialization in both directions."""
    def solve(self, val_dict=None):
        return {'received': val_dict, 'curve': np.array([0.0, 0.5, 1.0])}

class SlowSolver(WorkAction):
    """Emulates a solver on the server: writes progress lines and reports
    fractions through the server-side graph's status file."""
    def solve(self, val_dict=None):
        log = Path('slow.stdout')
        tail = FileProgressTail(self._progress_reporter, self.name, log,
                                solver_progress.radioss_run_time, t_end=2.0,
                                interval=0.05)
        tail.start()
        try:
            for t in ('0.0000E+00', '1.0000E+00', '2.0000E+00'):
                with open(log, 'a') as f:
                    f.write(f' NC=     100 T= {t} DT= 1.0E-06\n')
                time.sleep(0.4)
        finally:
            tail.stop()
        return 1.0

class TestRemotePatterns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ServerAction(port=50052)
        cls.server.add_graph("file_gen", FileGenTask("gen"), "Generates dummy files")
        cls.server.add_graph("echo", EchoTask("echo"), "Echoes val_dict and a numpy array")
        cls.server.add_graph("slow", WorkFlow('SlowWF', actions=[SlowSolver('slow_solver')]),
                             "Slow solver reporting progress")
        cls.server_thread = threading.Thread(target=cls.server.start, daemon=True)
        cls.server_thread.start()
        time.sleep(1) # Wait for server

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_specific_pattern(self):
        remote = RemoteAction("rem1", "file_gen", 'localhost:50052', output_patterns=['data.txt'])
        remote.solve({})
        self.assertTrue(os.path.exists('data.txt'))
        self.assertFalse(os.path.exists('image.png'))
        self.assertFalse(os.path.exists('log.log'))
        # Cleanup
        if os.path.exists('data.txt'): os.remove('data.txt')

    def test_wildcard_pattern(self):
        remote = RemoteAction("rem2", "file_gen", 'localhost:50052', output_patterns=['*.png'])
        remote.solve({})
        self.assertFalse(os.path.exists('data.txt'))
        self.assertTrue(os.path.exists('image.png'))
        self.assertFalse(os.path.exists('log.log'))
        # Cleanup
        if os.path.exists('image.png'): os.remove('image.png')

    def test_multiple_patterns(self):
        remote = RemoteAction("rem3", "file_gen", 'localhost:50052', output_patterns=['*.txt', '*.log'])
        remote.solve({})
        self.assertTrue(os.path.exists('data.txt'))
        self.assertFalse(os.path.exists('image.png'))
        self.assertTrue(os.path.exists('log.log'))
        # Cleanup
        if os.path.exists('data.txt'): os.remove('data.txt')
        if os.path.exists('log.log'): os.remove('log.log')

    def test_json_serialization_roundtrip(self):
        remote = RemoteAction("rem_echo", "echo", 'localhost:50052')
        result = remote.solve({'K': 0.2, 'T': 75, 'label': 'case_1'})
        self.assertEqual(result['received'], {'K': 0.2, 'T': 75, 'label': 'case_1'})
        self.assertTrue(np.array_equal(result['curve'], np.array([0.0, 0.5, 1.0])))

    def test_unserializable_val_dict_raises(self):
        from kunene.errors import SerializationError
        remote = RemoteAction("rem_bad", "echo", 'localhost:50052')
        with self.assertRaises(SerializationError):
            remote.solve({'payload': object()})

    def test_get_progress_unknown_job(self):
        with grpc.insecure_channel('localhost:50052') as channel:
            stub = remote_actions_pb2_grpc.KuneneRemoteStub(channel)
            resp = stub.GetProgress(remote_actions_pb2.ProgressRequest(job_id='no_such_job'))
        self.assertFalse(resp.found)

    def test_remote_progress_is_mirrored_locally(self):
        """While the remote job runs, the local status file shows the remote
        solver's fraction on the RemoteAction's entry -- the GUI keeps
        watching one local tree, ignorant of remote execution."""
        tmp = tempfile.TemporaryDirectory()
        cwd = os.getcwd()
        os.chdir(tmp.name)
        # absolute: the in-process test server chdirs the process during
        # RunAction, so relative reads would look in the wrong directory
        local_status = Path(tmp.name) / STATUS_PATH
        try:
            remote = RemoteAction('rem_prog', 'slow', 'localhost:50052',
                                  progress_interval=0.05)
            wf = WorkFlow('LocalWF', actions=[remote])

            worker = threading.Thread(target=wf.solve, args=({},))
            worker.start()
            seen = []
            t0 = time.time()
            while worker.is_alive() and time.time() - t0 < 20:
                try:
                    st = json.loads(local_status.read_text())
                    entry = st['actions'].get('rem_prog', {})
                    msg = entry.get('message') or ''
                    if msg.startswith('remote'):
                        seen.append((entry.get('fraction'), msg))
                except (FileNotFoundError, json.JSONDecodeError):
                    pass
                time.sleep(0.05)
            worker.join()

            self.assertTrue(seen, 'no mirrored remote progress observed')
            self.assertTrue(any(f is not None for f, _ in seen))
            self.assertTrue(any('slow_solver' in m for _, m in seen))
            st = json.loads(local_status.read_text())
            self.assertEqual(st['actions']['rem_prog']['state'], 'done')
        finally:
            os.chdir(cwd)
            tmp.cleanup()

    def test_no_patterns(self):
        remote = RemoteAction("rem4", "file_gen", 'localhost:50052', output_patterns=[])
        remote.solve({})
        self.assertFalse(os.path.exists('data.txt'))
        self.assertFalse(os.path.exists('image.png'))
        self.assertFalse(os.path.exists('log.log'))

if __name__ == '__main__':
    unittest.main()
