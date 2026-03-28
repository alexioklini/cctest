#!/usr/bin/env python3
"""Brain Agent SDK Backend — proxies to lean sidecar for real-time streaming.

The sidecar (sdk_sidecar.py) is a separate process that never imports claude_cli,
which is required for anyio's subprocess I/O to stream properly.
"""

import json
import os
import socket

SIDECAR_URL = "127.0.0.1"
SIDECAR_PORT = 8421

# Provider env var presets
_PROVIDER_ENV_PRESETS = {
    "cliproxyapi": lambda p, model: {
        "ANTHROPIC_BASE_URL": p["base_url"].rstrip("/").removesuffix("/v1"),
        "ANTHROPIC_AUTH_TOKEN": p.get("api_key", "brain-agent"),
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "ANTHROPIC_SMALL_FAST_MODEL": model,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    },
    "omlx": lambda p, model: {
        "ANTHROPIC_BASE_URL": p["base_url"].rstrip("/").removesuffix("/v1"),
        "ANTHROPIC_AUTH_TOKEN": p.get("api_key", "brain"),
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "ANTHROPIC_SMALL_FAST_MODEL": model,
        "API_TIMEOUT_MS": "3000000",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    },
    "minimax": lambda p, model: {
        "ANTHROPIC_BASE_URL": p["base_url"].rstrip("/").removesuffix("/v1"),
        "ANTHROPIC_AUTH_TOKEN": p.get("api_key", ""),
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "ANTHROPIC_SMALL_FAST_MODEL": model,
        "API_TIMEOUT_MS": "3000000",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    },
}


def _load_config() -> dict:
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(cfg_path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def build_provider_env(model: str) -> dict:
    """Build Claude Code env vars for the given model."""
    cfg = _load_config()
    providers = cfg.get("providers", {})
    matched_name, matched_prov = None, None

    for prov_name, prov_cfg in providers.items():
        if prov_cfg.get("default_model") == model:
            matched_name, matched_prov = prov_name, prov_cfg
            break
    if not matched_name:
        model_info = cfg.get("models", {}).get(model, {})
        hint = model_info.get("provider", "")
        if hint and hint in providers:
            matched_name, matched_prov = hint, providers[hint]
    if not matched_name:
        default = cfg.get("default_provider", "cliproxyapi")
        if default in providers:
            matched_name, matched_prov = default, providers[default]
    if not matched_prov:
        return {}

    preset_fn = _PROVIDER_ENV_PRESETS.get(matched_name.lower())
    if preset_fn:
        return preset_fn(matched_prov, model)

    base_url = matched_prov.get("base_url", "").rstrip("/").removesuffix("/v1")
    return {
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_AUTH_TOKEN": matched_prov.get("api_key", ""),
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "ANTHROPIC_SMALL_FAST_MODEL": model,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }


def is_sidecar_running() -> bool:
    """Check if the SDK sidecar is running."""
    try:
        s = socket.create_connection((SIDECAR_URL, SIDECAR_PORT), timeout=1)
        s.close()
        return True
    except (OSError, ConnectionRefusedError):
        return False


def proxy_sidecar_sse(payload: bytes, wfile, event_callback=None) -> dict:
    """Connect to sidecar, send query, proxy SSE to wfile, return metadata.

    Reads SSE events from the sidecar and writes them directly to the client's
    wfile for real-time streaming. Also parses events to extract metadata
    (text, tokens, cost, etc.) for DB storage.

    Returns dict with: text, sdk_session_id, tokens_in, tokens_out, cost, tools
    """
    result = {"text": "", "sdk_session_id": None, "tokens_in": 0,
              "tokens_out": 0, "cost": 0.0, "tools": []}

    sock = socket.create_connection((SIDECAR_URL, SIDECAR_PORT), timeout=300)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    # Send HTTP request
    http_req = (
        f"POST /query HTTP/1.1\r\n"
        f"Host: {SIDECAR_URL}:{SIDECAR_PORT}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n"
        f"\r\n"
    ).encode() + payload
    sock.sendall(http_req)

    # Read HTTP response headers
    header_buf = b""
    while b"\r\n\r\n" not in header_buf:
        b = sock.recv(1)
        if not b:
            break
        header_buf += b

    # Read SSE body — recv small chunks for low latency
    sock.settimeout(5)  # 5s timeout per recv to detect stalls
    full_text = ""
    tool_calls = []
    sse_buf = b""
    _client_gone = False

    while True:
        try:
            data = sock.recv(512)
        except socket.timeout:
            if _client_gone:
                break  # Client disconnected, stop proxying
            continue  # Keep waiting — sidecar may be executing tools
        except OSError:
            break
        if not data:
            break
        sse_buf += data

        # Forward complete SSE blocks to client
        while b"\n\n" in sse_buf:
            block_bytes, sse_buf = sse_buf.split(b"\n\n", 1)
            block_str = block_bytes.decode("utf-8", errors="replace").strip()

            # Parse SSE event
            evt_type = ""
            evt_data = None
            for line in block_str.split("\n"):
                if line.startswith("event: "):
                    evt_type = line[7:].strip()
                elif line.startswith("data: "):
                    try:
                        evt_data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        pass

            # Forward streaming events to client
            if evt_type in ("text_delta", "thinking_delta", "tool_call", "tool_result"):
                sse_out = block_bytes + b"\n\n"
                try:
                    wfile.write(sse_out)
                    wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    _client_gone = True
                    # Client gone — close sidecar connection and return immediately
                    try:
                        sock.close()
                    except Exception:
                        pass
                    if not result["text"] and full_text:
                        result["text"] = full_text
                    if not result["tools"] and tool_calls:
                        result["tools"] = tool_calls
                    return result
                if event_callback and evt_data:
                    event_callback(evt_type, evt_data)

            # Collect metadata from _result and stop reading
            if evt_type == "_result" and evt_data:
                result["text"] = evt_data.get("text", "")
                result["sdk_session_id"] = evt_data.get("sdk_session_id")
                result["tokens_in"] = evt_data.get("tokens_in", 0)
                result["tokens_out"] = evt_data.get("tokens_out", 0)
                result["cost"] = evt_data.get("cost", 0)
                result["tools"] = evt_data.get("tools", [])
                sock.close()
                # Use accumulated text if _result didn't provide it
                if not result["text"] and full_text:
                    result["text"] = full_text
                if not result["tools"] and tool_calls:
                    result["tools"] = tool_calls
                return result
            elif evt_type == "text_delta" and evt_data:
                full_text += evt_data.get("text", "")
            elif evt_type == "tool_call" and evt_data:
                tool_calls.append(evt_data)
            elif evt_type == "error" and evt_data:
                if event_callback:
                    event_callback("error", evt_data)

    sock.close()

    # Use accumulated text if _result didn't provide it
    if not result["text"] and full_text:
        result["text"] = full_text
    if not result["tools"] and tool_calls:
        result["tools"] = tool_calls

    return result


def query_sync(prompt: str, model: str, system_prompt: str = "",
               max_turns: int = 1) -> str | None:
    """Send a simple LLM query through the sidecar and return the text response.

    For background tasks that need an LLM call but no streaming.
    Does NOT import claude_cli — safe to call from anywhere.
    """
    provider_env = build_provider_env(model)
    if not provider_env:
        return None

    payload = json.dumps({
        "message": prompt,
        "model": model,
        "system_prompt": system_prompt,
        "provider_env": provider_env,
        "sdk_cfg": {"max_turns": max_turns, "permission_mode": "bypassPermissions"},
        "mcp_configs": {},
        "cwd": os.getcwd(),
    }).encode()

    try:
        sock = socket.create_connection((SIDECAR_URL, SIDECAR_PORT), timeout=300)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        http_req = (
            f"POST /query HTTP/1.1\r\n"
            f"Host: {SIDECAR_URL}:{SIDECAR_PORT}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            f"\r\n"
        ).encode() + payload
        sock.sendall(http_req)

        # Read headers
        header_buf = b""
        while b"\r\n\r\n" not in header_buf:
            b = sock.recv(1)
            if not b:
                break
            header_buf += b

        # Read SSE body — just collect text and wait for _result
        sock.settimeout(5)
        full_text = ""
        sse_buf = b""
        while True:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            sse_buf += data
            while b"\n\n" in sse_buf:
                block_bytes, sse_buf = sse_buf.split(b"\n\n", 1)
                block_str = block_bytes.decode("utf-8", errors="replace").strip()
                evt_type = ""
                evt_data = None
                for line in block_str.split("\n"):
                    if line.startswith("event: "):
                        evt_type = line[7:].strip()
                    elif line.startswith("data: "):
                        try:
                            evt_data = json.loads(line[6:])
                        except json.JSONDecodeError:
                            pass
                if evt_type == "text_delta" and evt_data:
                    full_text += evt_data.get("text", "")
                elif evt_type == "_result" and evt_data:
                    sock.close()
                    return evt_data.get("text", "") or full_text
                elif evt_type == "error" and evt_data:
                    sock.close()
                    return None
        sock.close()
        return full_text or None
    except Exception:
        return None
