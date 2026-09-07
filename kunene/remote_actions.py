import grpc
import json
import os
import shutil
import tempfile
import threading
import uuid
import glob
from concurrent import futures
from pathlib import Path
import logging

from kunene.actions import WorkAction
from kunene.args import STATUS_PATH
from kunene.errors import SerializationError
from kunene import serialization
from kunene.protos import remote_actions_pb2
from kunene.protos import remote_actions_pb2_grpc

# Increase max message size to 50MB
MAX_MESSAGE_LENGTH = 50 * 1024 * 1024
OPTIONS = [
    ('grpc.max_send_message_length', MAX_MESSAGE_LENGTH),
    ('grpc.max_receive_message_length', MAX_MESSAGE_LENGTH),
]

logger = logging.getLogger(__name__)


def _mirror_remote_status(reporter, action_name, status):
    """
    Reduce a remote job's status dict to this RemoteAction's entry in the
    *local* status file: the fraction of the remote action currently
    running plus a message naming it. The GUI keeps watching one local
    file tree and stays ignorant of remote execution.
    """
    actions = status.get('actions') or {}
    running = [(n, a) for n, a in actions.items() if a.get('state') == 'running']

    fraction, message = None, None
    for n, a in running:
        if a.get('fraction') is not None:
            fraction = a['fraction']
            message = f"remote {n}: {a['message']}" if a.get('message') else f"remote {n}"
            break
    if message is None and running:
        message = f"remote {running[0][0]}"
    if message is None:
        done = sum(1 for a in actions.values() if a.get('state') == 'done')
        if actions:
            message = f"remote: {done} of {len(actions)} actions done"
        else:
            message = f"remote state '{status.get('state', '?')}'"
    reporter.action_state(action_name, 'running', fraction=fraction, message=message)


class _RemoteProgressPoller:
    """
    Daemon thread polling GetProgress on a second channel while the unary
    RunAction call blocks, mirroring the remote status into the local
    status file via the graph-provided reporter. Polling failures are
    logged and ignored -- progress must never break a remote run.
    """

    def __init__(self, reporter, action_name, server_address, job_id, interval=2.0):
        self.reporter = reporter
        self.action_name = action_name
        self.server_address = server_address
        self.job_id = job_id
        self.interval = interval
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        if self.reporter is None or not self.reporter.active:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop polling and wait for the thread, so no 'running' write can
        land after the graph marks the action done."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 5.0)
            self._thread = None

    def _loop(self):
        try:
            with grpc.insecure_channel(self.server_address, options=OPTIONS) as channel:
                stub = remote_actions_pb2_grpc.KuneneRemoteStub(channel)
                request = remote_actions_pb2.ProgressRequest(job_id=self.job_id)
                while not self._stop.wait(self.interval):
                    try:
                        resp = stub.GetProgress(request, timeout=self.interval)
                    except grpc.RpcError:
                        continue    # transient; RunAction reports hard failures
                    if not resp.found:
                        continue
                    try:
                        status = json.loads(resp.status_json.decode('utf-8'))
                    except (UnicodeDecodeError, ValueError):
                        continue
                    _mirror_remote_status(self.reporter, self.action_name, status)
        except Exception as err:
            logger.warning(f'Remote progress polling stopped: {err}')


class RemoteAction(WorkAction):
    """
    Executes a registered WorkAction on a remote server via gRPC.
    Requires a target_action_name to execute a pre-registered action on the server.
    """
    def __init__(self, name, target_action_name=None, server_address=None, copy_paths=None, output_patterns=None,
                 progress_interval=2.0):
        """
        Args:
            name (str): Name of this action.
            target_action_name (str, optional): Name of a pre-registered action on the server.
            server_address (str): 'host:port' of the remote server.
            copy_paths (list): List of local file or directory paths to send to the remote.
                Files are sent using their basename. Directories are walked recursively and
                sent preserving their internal structure relative to the directory's parent.
            output_patterns (list): List of glob patterns for files to retrieve from remote.
            progress_interval (float): Seconds between GetProgress polls while the remote
                job runs; the remote status is mirrored into the local status file.
        """
        super().__init__(name)
        self.target_action_name = target_action_name
        self.server_address = server_address
        self.copy_paths = copy_paths or []
        self.output_patterns = output_patterns or []
        self.progress_interval = progress_interval
        self.description = f'Remote action executing {target_action_name} on {server_address}'

    def available_actions(self):
        """
        Queries the remote server for available registered actions.
        
        Returns:
            dict: A dictionary mapping action names to their descriptions.
        """
        try:
            with grpc.insecure_channel(self.server_address, options=OPTIONS) as channel:
                stub = remote_actions_pb2_grpc.KuneneRemoteStub(channel)
                resp = stub.GetAvailableActions(remote_actions_pb2.Empty())
                return {a.name: a.description for a in resp.actions}
        except grpc.RpcError as e:
            logger.error(f"Error querying available actions: {e}")
            raise

    def solve(self, val_dict=None):
        if not self.target_action_name:
            raise ValueError("target_action_name must be provided to execute a remote action.")

        # 1. Prepare Request
        req = remote_actions_pb2.ActionRequest()

        req.action_name = self.target_action_name
        req.target_action_name = self.target_action_name
        # client-chosen id lets a second channel poll GetProgress while the
        # unary RunAction call below blocks
        req.job_id = uuid.uuid4().hex

        # Restricted JSON, not pickle: unpickling network data would allow
        # arbitrary code execution on the peer. See kunene/serialization.py
        # for the allowed types.
        req.pickled_val_dict = serialization.dumps(val_dict)
        if self.output_patterns:
            req.output_patterns.extend(self.output_patterns)

        # Send copy_paths (files and directories) to remote
        for path in self.copy_paths:
            if os.path.isfile(path):
                with open(path, 'rb') as f:
                    content = f.read()
                req.input_files.append(remote_actions_pb2.File(name=os.path.basename(path), content=content))
            elif os.path.isdir(path):
                parent = os.path.dirname(os.path.abspath(path))
                for root, _, files in os.walk(path):
                    for file in files:
                        fpath = os.path.join(root, file)
                        rel = os.path.relpath(fpath, parent)
                        with open(fpath, 'rb') as f:
                            content = f.read()
                        req.input_files.append(remote_actions_pb2.File(name=rel, content=content))
            else:
                logger.warning(f"Path not found: {path}")

        # 2. Connect and Send
        poller = _RemoteProgressPoller(self._progress_reporter, self.name,
                                       self.server_address, req.job_id,
                                       interval=self.progress_interval)
        poller.start()
        try:
            with grpc.insecure_channel(self.server_address, options=OPTIONS) as channel:
                stub = remote_actions_pb2_grpc.KuneneRemoteStub(channel)
                logger.info(f"Sending action '{self.name}' to {self.server_address}...")
                resp = stub.RunAction(req)
        except grpc.RpcError as e:
            logger.error(f"gRPC error: {e}")
            raise
        finally:
            poller.stop()

        # 3. Process Response
        if not resp.success:
            raise RuntimeError(f"Remote action failed: {resp.error_message}")

        # Unpack results
        result = serialization.loads(resp.pickled_results)
        
        # Save output files
        for f_msg in resp.output_files:
            # We save them in the current directory (or we could specify a dir)
            # Assuming current working directory for now as per kunene conventions
            with open(f_msg.name, 'wb') as f:
                f.write(f_msg.content)
            logger.info(f"Received file: {f_msg.name}")

        return result


class KuneneService(remote_actions_pb2_grpc.KuneneRemoteServicer):
    def __init__(self, actions_registry=None):
        self.actions_registry = actions_registry or {}
        # running jobs by client-chosen id, so GetProgress can find the
        # job's work directory while the unary RunAction call blocks
        self._jobs = {}
        self._jobs_lock = threading.Lock()

    @staticmethod
    def _find_status_file(tmp_dir):
        """The job's status.json: at the work-dir root for a plain graph,
        nested deeper for WorkArea/SimulationIterator wrappers (then the
        shallowest one is the run-level status)."""
        root = Path(tmp_dir)
        direct = root / STATUS_PATH
        if direct.exists():
            return direct
        candidates = sorted(root.rglob(STATUS_PATH), key=lambda p: len(p.parts))
        return candidates[0] if candidates else None

    def GetProgress(self, request, context):
        resp = remote_actions_pb2.ProgressResponse()
        resp.found = False
        with self._jobs_lock:
            tmp_dir = self._jobs.get(request.job_id)
        if tmp_dir is None:
            return resp
        status_file = self._find_status_file(tmp_dir)
        if status_file is None:
            return resp
        try:
            # the file is plain JSON, written atomically by StatusReporter
            resp.status_json = status_file.read_bytes()
            resp.found = True
        except OSError:
            pass
        return resp

    def GetAvailableActions(self, request, context):
        resp = remote_actions_pb2.AvailableActionsResponse()
        for name, info in self.actions_registry.items():
            action_info = resp.actions.add()
            action_info.name = name
            action_info.description = info.get('description', '')
        return resp

    def RunAction(self, request, context):
        resp = remote_actions_pb2.ActionResponse()
        tmp_dir = tempfile.mkdtemp(prefix=f"kunene_remote_{request.action_name}_")
        original_cwd = os.getcwd()

        if request.job_id:
            with self._jobs_lock:
                self._jobs[request.job_id] = tmp_dir

        try:
            logger.info(f"Received action: {request.action_name}")
            
            # 1. Setup environment
            os.chdir(tmp_dir)
            
            # Write input files (preserving any directory structure in the name)
            for f_msg in request.input_files:
                parent = os.path.dirname(f_msg.name)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(f_msg.name, 'wb') as f:
                    f.write(f_msg.content)
            
            # 2. Resolve action
            if request.target_action_name:
                if request.target_action_name in self.actions_registry:
                    action = self.actions_registry[request.target_action_name]['graph']
                else:
                    resp.success = False
                    resp.error_message = f"Action '{request.target_action_name}' not found on server."
                    return resp
            else:
                resp.success = False
                resp.error_message = "target_action_name not provided."
                return resp
            
            try:
                val_dict = serialization.loads(request.pickled_val_dict)
            except SerializationError as e:
                resp.success = False
                resp.error_message = f"Could not decode val_dict: {e}"
                return resp

            # 3. Execute
            try:
                result = action.solve(val_dict)
                resp.pickled_results = serialization.dumps(result)
                resp.success = True
            except Exception as e:
                import traceback
                traceback.print_exc()
                resp.success = False
                resp.error_message = str(e)
                return resp

            # 4. Collect output files
            found_files = set()
            for pattern in request.output_patterns:
                matches = glob.glob(pattern, recursive=True)
                for match in matches:
                    if os.path.isfile(match):
                        found_files.add(match)

            for fname in found_files:
                if os.path.getsize(fname) > MAX_MESSAGE_LENGTH:
                    logger.warning(f"File {fname} too large to send back via gRPC")
                    continue
                    
                with open(fname, 'rb') as f:
                    content = f.read()
                resp.output_files.append(remote_actions_pb2.File(name=fname, content=content))
                    
        except Exception as e:
            logger.error(f"Server error: {e}")
            resp.success = False
            resp.error_message = f"Server internal error: {str(e)}"
        finally:
            if request.job_id:
                with self._jobs_lock:
                    self._jobs.pop(request.job_id, None)
            os.chdir(original_cwd)
            shutil.rmtree(tmp_dir)

        return resp


class ServerAction:
    """
    Starts a gRPC server to execute actions remotely.
    Can be configured with pre-registered actions (graphs).
    """
    def __init__(self, port=50051, max_workers=10):
        self.port = port
        self.max_workers = max_workers
        self.server = None
        self.actions_registry = {}

    def add_graph(self, name, graph, description=""):
        """
        Registers a graph (WorkAction) with a name and description.
        """
        self.actions_registry[name] = {'graph': graph, 'description': description}

    def start(self):
        self.server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=self.max_workers),
            options=OPTIONS
        )
        remote_actions_pb2_grpc.add_KuneneRemoteServicer_to_server(
            KuneneService(self.actions_registry),
            self.server
        )
        self.server.add_insecure_port(f'[::]:{self.port}')
        logger.info(f"Server starting on port {self.port}...")
        self.server.start()
        
    def wait_for_termination(self):
        if self.server:
            self.server.wait_for_termination()
            
    def stop(self):
        if self.server:
            self.server.stop(0)

class NamedServerAction(ServerAction):
    """
    Alias for ServerAction supporting named graphs.
    """
    pass