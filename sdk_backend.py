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
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "ANTHROPIC_SMALL_FAST_MODEL": model,
        "API_TIMEOUT_MS": "3000000",
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

    # Resolve actual API model ID for provider-scoped keys
    model_info = cfg.get("models", {}).get(model, {})
    api_model = model_info.get("base_model_id", model)

    preset_fn = _PROVIDER_ENV_PRESETS.get(matched_name.lower())
    if preset_fn:
        return preset_fn(matched_prov, api_model)

    base_url = matched_prov.get("base_url", "").rstrip("/").removesuffix("/v1")
    return {
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_AUTH_TOKEN": matched_prov.get("api_key", ""),
        "ANTHROPIC_MODEL": api_model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": api_model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": api_model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": api_model,
        "ANTHROPIC_SMALL_FAST_MODEL": api_model,
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


def proxy_sidecar_sse(payload: bytes, wfile, event_callback=None, raw_socket=None) -> dict:
    """Start a query on the sidecar REST API and poll for events.

    1. POST /query with payload → get query_id
    2. Poll GET /events/{query_id}?after=N every 50ms
    3. Forward each event to the client immediately via raw_socket or wfile

    Returns dict with: text, sdk_session_id, tokens_in, tokens_out, cost, tools
    """
    import time
    import urllib.request

    result = {"text": "", "sdk_session_id": None, "tokens_in": 0,
              "tokens_out": 0, "cost": 0.0, "tools": []}
    base = f"http://{SIDECAR_URL}:{SIDECAR_PORT}"

    # Step 1: Start query
    try:
        req = urllib.request.Request(f"{base}/query", data=payload,
                                     headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=10)
        query_id = json.loads(resp.read()).get("query_id")
    except Exception as e:
        return {"text": "", "error": str(e)}

    if not query_id:
        return {"text": "", "error": "No query_id returned"}

    # Step 2: Poll for events
    full_text = ""
    tool_calls = []
    after = 0
    _client_gone = False
    start = time.monotonic()

    while time.monotonic() - start < 300:  # 5 min timeout
        try:
            url = f"{base}/events/{query_id}?after={after}"
            resp = urllib.request.urlopen(url, timeout=10)
            data = json.loads(resp.read())
        except Exception:
            time.sleep(0.1)
            continue

        events = data.get("events", [])
        done = data.get("done", False)

        # Forward each event to the client
        for ev in events:
            evt_type = ev.get("event", "")
            evt_data = ev.get("data", {})

            if evt_type in ("text_delta", "thinking_delta", "tool_call", "tool_result"):
                sse_out = f"event: {evt_type}\ndata: {json.dumps(evt_data)}\n\n".encode()
                if not _client_gone:
                    try:
                        if raw_socket:
                            raw_socket.sendall(sse_out)
                        else:
                            wfile.write(sse_out)
                            wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        _client_gone = True
                if event_callback:
                    event_callback(evt_type, evt_data)

            if evt_type == "_result" and evt_data:
                result["text"] = evt_data.get("text", "") or full_text
                result["sdk_session_id"] = evt_data.get("sdk_session_id")
                result["tokens_in"] = evt_data.get("tokens_in", 0)
                result["tokens_out"] = evt_data.get("tokens_out", 0)
                result["cost"] = evt_data.get("cost", 0)
                result["tools"] = evt_data.get("tools", []) or tool_calls
                return result
            elif evt_type == "text_delta" and evt_data:
                full_text += evt_data.get("text", "")
            elif evt_type == "tool_call" and evt_data:
                tool_calls.append(evt_data)
            elif evt_type == "error" and evt_data:
                if event_callback:
                    event_callback("error", evt_data)

        after = data.get("next", after)

        if done:
            break

        # Send keepalive comment to prevent browser buffering
        if not _client_gone:
            try:
                if raw_socket:
                    raw_socket.sendall(b": keepalive\n\n")
                else:
                    wfile.write(b": keepalive\n\n")
                    wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                _client_gone = True

        # Poll interval: 50ms for responsiveness
        time.sleep(0.05)

    if not result["text"] and full_text:
        result["text"] = full_text
    if not result["tools"] and tool_calls:
        result["tools"] = tool_calls
    return result


def query_sync(prompt: str, model: str, system_prompt: str = "",
               max_turns: int = 1, tool_defs: list[dict] | None = None,
               server_url: str = "http://127.0.0.1:8420",
               agent_id: str = "main", session_id: str | None = None,
               cancel_fn=None, sdk_session_id: str | None = None,
               return_metadata: bool = False):
    """Send a query through the sidecar REST API and return the text response.

    Uses the same REST polling approach as proxy_sidecar_sse:
    POST /query → poll GET /events/{id}?after=N

    Does NOT import claude_cli — safe to call from anywhere.
    """
    import time
    import urllib.request

    provider_env = build_provider_env(model)
    if not provider_env:
        return None

    body = {
        "message": prompt,
        "model": model,
        "system_prompt": system_prompt,
        "provider_env": provider_env,
        "sdk_cfg": {"max_turns": max_turns, "permission_mode": "bypassPermissions"},
        "mcp_configs": {},
        "cwd": os.getcwd(),
    }
    if tool_defs:
        body["tool_defs"] = tool_defs
        body["server_url"] = server_url
        body["agent_id"] = agent_id
        if session_id:
            body["session_id"] = session_id
    if sdk_session_id:
        body["sdk_session_id"] = sdk_session_id

    payload = json.dumps(body).encode()
    base = f"http://{SIDECAR_URL}:{SIDECAR_PORT}"

    def _result(text, meta=None):
        if return_metadata:
            return meta or {"text": text}
        return text

    try:
        # Start query
        req = urllib.request.Request(f"{base}/query", data=payload,
                                     headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=10)
        query_id = json.loads(resp.read()).get("query_id")
        if not query_id:
            return None

        # Poll for events
        full_text = ""
        after = 0
        start = time.monotonic()
        while time.monotonic() - start < 300:
            if cancel_fn and cancel_fn():
                try:
                    urllib.request.urlopen(f"{base}/cancel/{query_id}", timeout=5)
                except Exception:
                    pass
                return None

            try:
                resp = urllib.request.urlopen(
                    f"{base}/events/{query_id}?after={after}", timeout=10)
                data = json.loads(resp.read())
            except Exception:
                time.sleep(0.2)
                continue

            for ev in data.get("events", []):
                evt_type = ev.get("event", "")
                evt_data = ev.get("data", {})
                if evt_type == "text_delta" and evt_data:
                    full_text += evt_data.get("text", "")
                elif evt_type == "_result" and evt_data:
                    text = evt_data.get("text", "") or full_text
                    return _result(text, {
                        "text": text,
                        "sdk_session_id": evt_data.get("sdk_session_id"),
                        "tokens_in": evt_data.get("tokens_in", 0),
                        "tokens_out": evt_data.get("tokens_out", 0),
                        "cost": evt_data.get("cost", 0),
                        "tools": evt_data.get("tools", []),
                    })
                elif evt_type == "error":
                    return None

            after = data.get("next", after)
            if data.get("done"):
                break
            time.sleep(0.2)

        return _result(full_text, {"text": full_text}) if full_text else None
    except Exception:
        return None
