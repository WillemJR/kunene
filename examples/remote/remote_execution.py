
import logging
logging.basicConfig(level=logging.WARNING)

import sys
import os
import time
import threading
import logging

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from kunene.actions import WorkAction
from kunene.remote_actions import RemoteAction, ServerAction

logging.basicConfig(level=logging.INFO)

# --- 1. Define Actions to run remotely ---

class AdderAction(WorkAction):
    """Adds two numbers and creates a result file."""
    def solve(self, val_dict=None):
        print(f"  [Remote] Executing AdderAction with inputs: {val_dict}")
        a = val_dict.get('a', 0)
        b = val_dict.get('b', 0)
        result = a + b
        
        # Create a file to demonstrate file retrieval
        with open('adder_result.txt', 'w') as f:
            f.write(f"The result of {a} + {b} is {result}\n")
            
        return result

class MultiplierAction(WorkAction):
    """Multiplies two numbers."""
    def solve(self, val_dict=None):
        print(f"  [Remote] Executing MultiplierAction with inputs: {val_dict}")
        a = val_dict.get('a', 1)
        b = val_dict.get('b', 1)
        return a * b

# --- 2. Server setup ---

def run_server():
    """
    This normally runs remotely.
    For the example a separate thread is used.
    """
    # Use port 50051 for the demo
    server = ServerAction(port=50051)
    
    # Register actions (Graphs) on the server
    server.add_graph("adder", AdderAction("Adder"), "Adds 'a' and 'b', returns result and 'adder_result.txt'")
    server.add_graph("multiplier", MultiplierAction("Multiplier"), "Multiplies 'a' and 'b'")
    
    server.start()
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop()

# --- 3. Main Execution ---

if __name__ == "__main__":
    # Start server in a separate thread for this demo
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    
    # Give server a moment to start
    time.sleep(1)
    
    print("\n--- Starting Client ---")
    
    # Create a generic RemoteAction client for discovery
    client = RemoteAction(name="discovery_client", server_address='localhost:50051')
    
    # 4. Discovery: Query available actions
    print("\nQuerying available actions from server...")
    available = client.available_actions()
    for name, desc in available.items():
        print(f"  - {name}: {desc}")
    
    # 5. Execute Actions
    data = {'a': 12, 'b': 5}
    
    # Case A: Execute the Adder (with file retrieval)
    print(f"\nExecuting 'adder' with data {data}...")
    adder_client = RemoteAction(
        name="run_adder",
        target_action_name="adder",
        server_address='localhost:50051',
        output_patterns=['adder_result.txt']
    )
    
    res_add = adder_client.solve(data)
    print(f"Adder Result: {res_add}")
    
    # Verify file reception
    if os.path.exists('adder_result.txt'):
        with open('adder_result.txt', 'r') as f:
            print(f"Received File Content: {f.read().strip()}")
        os.remove('adder_result.txt') # Cleanup
    
    # Case B: Execute the Multiplier
    print(f"\nExecuting 'multiplier' with data {data}...")
    mult_client = RemoteAction(
        name="run_multiplier",
        target_action_name="multiplier",
        server_address='localhost:50051'
    )
    
    res_mult = mult_client.solve(data)
    print(f"Multiplier Result: {res_mult}")
    
    print("\n--- Done ---")
