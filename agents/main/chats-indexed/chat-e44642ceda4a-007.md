---
name: "what do you know about brain agents functionality of remote (part 8/8)"
type: chat_transcript
description: "Brain agent remote node functionality overview."
session_id: e44642ceda4a
agent: main
chunk_index: 7
total_chunks: 8
created_at: "2026-04-06T14:05:32.061525"
last_recalled: 2026-04-09
related:
  - file: chats-indexed/chat-e44642ceda4a-006.md
    type: same_topic
  - file: chats-indexed/chat-e44642ceda4a-000.md
    type: depends_on
  - file: chats-indexed/chat-e44642ceda4a-001.md
    type: depends_on
  - file: chats-indexed/chat-33fe27567224-003.md
    type: same_topic
  - file: chats-indexed/chat-33fe27567224-000.md
    type: same_topic
---

## New Approach

e remote nodes use WebSocket connections (port 8420 by default) and are registered in the `config.json` file under a `nodes` section. Let me check the current config.json and add our Mac mini M4 node properly:

**tool**: [Old execute_command result cleared]

**tool**: [Old read_file result cleared]

**tool**: [Old execute_command result cleared]

**tool**: [Old execute_command result cleared]

**tool**: [Old execute_command result cleared]

**assistant**: Perfect! Now I can see the nodes section exists. Let me add our Mac mini M4 node to the config.json file:

**tool**: [Old execute_command result cleared]

**assistant**: Excellent! The node has been added to config.json. Now let me verify it appears in the Brain Agent system:

**tool**: [Old execute_command result cleared]

**assistant**: Perfect! The node is now registered in the config.json file. Now let me restart the Brain Agent server to load the new node configuration and then verify it appears in the system:

**tool**: [Old execute_command result cleared]

**tool**: [Old execute_command result cleared]

**assistant**: Good! The Brain Agent server is running. Now let me verify that the Mac mini M4 node appears in the system:

**tool**: {"command": "cd /Users/alexander/Documents/dev/cctest && python3 << 'PYEOF'\nimport sys\nsys.path.insert(0, '/Users/alexander/Documents/dev/cctest')\n\n# Import the Brain Agent system\nfrom brain_agent import BrainAgent\n\n# Create agent instance\nagent = BrainAgent(config_path='config.json')\n\n# Check registered nodes\nprint(\"Registered Nodes:\")\nprint(\"=\" * 50)\nif hasattr(agent, 'nodes') and agent.nodes:\n    for node_name, node_info in agent.nodes.items():\n        print(f\"✓ {node_name}\")\n        print(f\"  Host: {node_info.get('hostname', 'N/A')}\")\n        print(f\"  Status: {node_info.get('status', 'unknown')}\")\n        print(f\"  Tags: {', '.join(node_info.get('tags', []))}\")\n        print(f\"  Allowed Tools: {', '.join(node_info.get('allowed_tools', []))}\")\n        print()\nelse:\n    print(\"No nodes registered or 'nodes' attribute not found\")\n    print(f\"Agent attributes: {dir(agent)}\")\nPYEOF\n", "exit_code": 1, "output": "Traceback (most recent call last):\n  File \"<stdin>\", line 5, in <module>\nModuleNotFoundError: No module named 'brain_agent'\n"}

**assistant**: Let me check the Brain Agent system structure:

**tool**: {"command": "ls -la /Users/alexander/Documents/dev/cctest/*.py | head -10", "exit_code": 0, "output": "-rw-r--r--  1 alexander  staff   24089 21 März 14:34 /Users/alexander/Documents/dev/cctest/adapters.py\n-rw-r--r--  1 alexander  staff   12772 17 März 11:58 /Users/alexander/Documents/dev/cctest/brain.py\n-rwxr-xr-x@ 1 alexander  staff  704182  5 Apr. 19:20 /Users/alexander/Documents/dev/cctest/claude_cli.py\n-rw-r--r--  1 alexander  staff   20221 28 März 18:56 /Users/alexander/Documents/dev/cctest/client.py\n-rw-r--r--  1 alexander  staff      23 31 März 14:06 /Users/alexander/Documents/dev/cctest/hello_world.py\n-rwxr-xr-x  1 alexander  staff    4903 28 März 08:56 /Users/alexander/Documents/dev/cctest/mcp_bridge.py\n-rw-r--r--@ 1 alexander  staff   24024 22 März 15:07 /Users/alexander/Documents/dev/cctest/node.py\n-rw-r--r--  1 alexander  staff   10111 20 März 17:38 /Users/alexander/Documents/dev/cctest/notifications.py\n-rw-r--r--@ 1 alexander  staff   11597  3 Apr. 13:44 /Users/alexander/Documents/dev/cctest/sdk_backend.py\n-rw-r--r--  1 alexander  staff   22822  5 Apr. 17:01 /Users/alexander/Documents/dev/cctest/sdk_sidecar.py\n"}

**assistant**: Let me check the node.py file to understand how nodes are managed:

**tool**: {"command": "grep -n \"class.*Node\" /Users/alexander/Documents/dev/cctest/node.py | head -10", "exit_code": 0, "output": "266:class NodeClient:\n"}

**assistant**: Let me check the server.py to see how nodes are registered:

**tool**: {"command": "grep -n \"macmini-m4-node\\|NodeClient\\|register_node\" /Users/alexander/Documents/dev/cctest/server.py | head -20", "exit_code": 0, "output": ""}

**assistant**: Let me check the server.py file to understand how it loads nodes:

**tool**: {"command": "grep -n \"nodes\" /Users/alexander/Documents/dev/cctest/server.py | head -20", "exit_code": 0, "output": "34:# --- Node Manager (in-memory registry for remote nodes) ---\n42:    \"\"\"Load nodes config from config.json.\"\"\"\n47:        return config.get(\"nodes\", {})\n52:def _save_node_config(nodes: dict):\n53:    \"\"\"Save nodes config to config.json.\"\"\"\n60:        config[\"nodes\"] = nodes\n70:    nodes_cfg = _load_node_config()\n72:        for name, cfg in nodes_cfg.items():\n1418:        elif path == \"/v1/nodes\":\n1419:            self._handle_nodes_list()\n1420:        elif path.startswith(\"/v1/nodes/poll\"):\n1600:        elif path == \"/v1/nodes\":\n1601:            self._handle_nodes_action()\n1602:        elif path == \"/v1/nodes/result\":\n1604:        elif path == \"/v1/nodes/execute\":\n2973:                                 \"schedule_list\", \"schedule_history\", \"list_nodes\",\n4057:        nodes = []\n4117:                    nodes.append({\n4153:        for node in nodes:\n4178:            \"nodes\": nodes,\n"}

**assistant**: Perfect! The server.py has a Node Manager. Let me check the list_nodes tool to see if it will show our node:

**tool**: {"command": "grep -A 30 \"def _handle_nodes_list\" /Users/alexander/Documents/dev/cctest/server.py", "exit_code": 0, "output": "    def _handle_nodes_list(self):\n        \"\"\"GET /v1/nodes — list all nodes with status.\"\"\"\n        nodes = []\n        with _node_lock:\n            for token, info in _node_registry.items():\n                cfg = info.get(\"config\", {})\n                nodes.append({\n                    \"name\": info[\"name\"],\n                    \"description\": cfg.get(\"description\", \"\"),\n                    \"token\": token,\n                    \"status\": info[\"status\"],\n                    \"paused\": cfg.get(\"paused\", False),\n                    \"hostname\": info.get(\"hostname\", \"\"),\n                    \"os\": info.get(\"os\", \"\"),\n                    \"tags\": cfg.get(\"tags\", []),\n                    \"allowed_tools\": cfg.get(\"allowed_tools\", []),\n                    \"max_concurrent\": cfg.get(\"max_concurrent\", 5),\n                    \"command_timeout\": cfg.get(\"command_timeout\", 300),\n                    \"last_heartbeat\": info.get(\"last_heartbeat\"),\n                    \"cpu_percent\": info.get(\"cpu_percent\"),\n                    \"mem_used_gb\": info.get(\"mem_used_gb\"),\n                    \"mem_total_gb\": info.get(\"mem_total_gb\"),\n                    \"disk_free_gb\": info.get(\"disk_free_gb\"),\n                    \"uptime_seconds\": info.get(\"uptime_seconds\"),\n                    \"active_commands\": info.get(\"active_commands\", 0),\n                    \"total_commands\": info.get(\"total_commands\", 0),\n                    \"connected_since\": info.get(\"connected_since\"),\n                })\n        self._send_json({\"nodes\": nodes})\n\n    def _handle_node_poll(self):\n"}

**assistant**: Great! Now let me check the list_nodes tool to see if it will show our node. Let me also check if the node needs to connect to the server. Looking at the remote-nodes.md file, I see that nodes connect to the server via WebSocket on port 8420. Let me check if the node is configured to connect: