#!/bin/bash

echo "Installing Brain Agent Node on macOS..."

# Create necessary directories
echo "Creating directories..."
ssh brain@192.168.4.65 "mkdir -p ~/brain_agent_node/{bin,config,logs}"

# Install Python if not present
echo "Checking Python installation..."
ssh brain@192.168.4.65 "if ! command -v python3 &> /dev/null; then echo 'Python not found, installing...'; /usr/bin/ruby -e \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/master/install)\"; brew install python; else echo 'Python already installed'; fi"

# Install pip if not present
echo "Checking pip installation..."
ssh brain@192.168.4.65 "if ! command -v pip3 &> /dev/null; then echo 'pip not found, installing...'; curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py; python3 get-pip.py; rm get-pip.py; else echo 'pip already installed'; fi"

# Install required Python packages
echo "Installing Python packages..."
ssh brain@192.168.4.65 "pip3 install --upgrade pip && pip3 install requests psutil websockets"

# Create node configuration
echo "Creating configuration..."
ssh brain@192.168.4.65 "cat > ~/brain_agent_node/config/node_config.json << 'CONFIG_EOF'
{
    \"node_id\": \"macmini-m4-node\",
    \"node_name\": \"Mac Mini M4 Node\",
    \"host\": \"192.168.4.65\",
    \"port\": 8765,
    \"tags\": [\"macos\", \"compute\", \"m4\"],
    \"allowed_tools\": [\"execute_command\", \"read_file\", \"write_file\", \"list_directory\"],
    \"main_server\": \"192.168.4.XXX\",
    \"auth_token\": \"generate_secure_token_here\"
}
CONFIG_EOF"

# Create node service script
echo "Creating node service..."
ssh brain@192.168.4.65 "cat > ~/brain_agent_node/bin/node_service.py << 'SERVICE_EOF'
#!/usr/bin/env python3
import json
import socket
import threading
import subprocess
import os
import sys
import time
import psutil
import websockets
import asyncio

class BrainAgentNode:
    def __init__(self, config_path):
        with open(os.path.expanduser(config_path)) as f:
            self.config = json.load(f)
        self.node_id = self.config['node_id']
        self.host = self.config['host']
        self.port = self.config['port']
        self.main_server = self.config['main_server']
        self.auth_token = self.config['auth_token']
        
    async def connect_to_main(self):
        uri = f\"ws://{self.main_server}:8766/node/register\"
        try:
            async with websockets.connect(uri) as websocket:
                await websocket.send(json.dumps({
                    'action': 'register',
                    'node_id': self.node_id,
                    'auth_token': self.auth_token,
                    'config': self.config
                }))
                response = await websocket.recv()
                print(f\"Registration response: {response}\")
                return True
        except Exception as e:
            print(f\"Connection failed: {e}\")
            return False
    
    def start(self):
        print(f\"Starting Brain Agent Node {self.node_id}...\"
        asyncio.get_event_loop().run_until_complete(self.connect_to_main())
        # Keep node running
        while True:
            time.sleep(60)

if __name__ == \"__main__\":
    node = BrainAgentNode(\"~/brain_agent_node/config/node_config.json\")
    node.start()
SERVICE_EOF"

ssh brain@192.168.4.65 "chmod +x ~/brain_agent_node/bin/node_service.py"

echo "Installation complete!"
echo "To start the node, run: python3 ~/brain_agent_node/bin/node_service.py"
