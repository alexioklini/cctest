# Image generation via Mistral Conversations API
# Cross-module deps imported lazily from brain at call time to avoid circular import
# (brain.py imports tool_generate_image near end of module evaluation).

import json
import os
import threading
import urllib.error
import urllib.request


def _ok(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


_MISTRAL_BASE = "https://api.mistral.ai"
_agent_id_lock = threading.Lock()
_cached_agent_id = None  # in-memory cache; persisted to config.json


def _mistral_api_key() -> str:
    """Resolve the Mistral provider API key from config."""
    import brain
    try:
        with open(brain.CONFIG_PATH) as f:
            cfg = json.load(f)
        for prov in cfg.get("providers", {}).values():
            if "mistral.ai" in prov.get("base_url", ""):
                keys = prov.get("api_keys") or []
                if isinstance(keys, list) and keys:
                    k = keys[0]
                    return k.get("key", "") if isinstance(k, dict) else str(k)
                return prov.get("api_key", "")
    except Exception:
        pass
    return ""


def _get_or_create_image_agent(api_key: str) -> str:
    """Return a cached Mistral image-generation agent ID, creating it on first call."""
    import brain
    global _cached_agent_id
    with _agent_id_lock:
        if _cached_agent_id:
            return _cached_agent_id

        # Try config.json first
        try:
            with open(brain.CONFIG_PATH) as f:
                cfg = json.load(f)
            stored = cfg.get("mistral_image_agent_id", "")
            if stored:
                _cached_agent_id = stored
                return stored
        except Exception:
            cfg = {}

        # Create agent on Mistral's side
        payload = json.dumps({
            "model": "mistral-medium-latest",
            "name": "Brain Image Generation Agent",
            "description": "Generates images from text prompts.",
            "instructions": "Generate images exactly as requested. Use the image_generation tool.",
            "tools": [{"type": "image_generation"}],
        }).encode()
        req = urllib.request.Request(
            f"{_MISTRAL_BASE}/v1/agents",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            agent_id = data.get("id", "")
            if not agent_id:
                raise RuntimeError(f"Unexpected response: {data}")
        except Exception as e:
            raise RuntimeError(f"Failed to create Mistral image agent: {e}")

        # Persist to config.json and in-memory cache
        try:
            cfg["mistral_image_agent_id"] = agent_id
            with open(brain.CONFIG_PATH, "w") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception:
            pass  # non-fatal — will re-create next restart

        _cached_agent_id = agent_id
        return agent_id


def tool_generate_image(args: dict) -> str:
    import brain
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return _err("generate_image: 'prompt' is required")

    aspect_ratio = args.get("aspect_ratio", "1:1")
    style = (args.get("style") or "").strip()

    full_prompt = prompt
    if style:
        full_prompt = f"{full_prompt}, style: {style}"
    if aspect_ratio and aspect_ratio != "1:1":
        full_prompt = f"{full_prompt}, aspect ratio {aspect_ratio}"

    api_key = _mistral_api_key()
    if not api_key:
        return _err(
            "generate_image: No Mistral API key found. "
            "Add a provider entry with base_url 'https://api.mistral.ai/v1' "
            "and an api_key to config.json."
        )

    try:
        agent_id = _get_or_create_image_agent(api_key)
    except Exception as e:
        return _err(str(e))

    # Start a conversation with the image agent
    conv_payload = json.dumps({
        "agent_id": agent_id,
        "inputs": full_prompt,
    }).encode()
    req = urllib.request.Request(
        f"{_MISTRAL_BASE}/v1/conversations",
        data=conv_payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            conv_data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        return _err(f"generate_image: Mistral API error {e.code}: {body}")
    except Exception as e:
        return _err(f"generate_image: request failed: {e}")

    # Extract file IDs from the response — handle both output and messages shapes
    file_ids: list[tuple[str, str]] = []
    for output in conv_data.get("outputs", []):
        for chunk in output.get("content", []) if isinstance(output.get("content"), list) else []:
            if chunk.get("type") == "tool_file":
                fid = chunk.get("file_id") or chunk.get("id", "")
                if fid:
                    file_ids.append((fid, chunk.get("file_name", f"image_{fid[:8]}.png")))
    if not file_ids:
        for msg in conv_data.get("messages", []):
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for chunk in content:
                if chunk.get("type") == "tool_file":
                    fid = chunk.get("file_id") or chunk.get("id", "")
                    if fid:
                        file_ids.append((fid, chunk.get("file_name", f"image_{fid[:8]}.png")))

    if not file_ids:
        return _err(
            f"generate_image: No image files returned by Mistral. "
            f"Response keys: {list(conv_data.keys())}"
        )

    # Resolve artifact save folder (same logic as tool_write_file)
    session_id = getattr(brain._thread_local, "current_session_id", None)
    agent = getattr(brain._thread_local, "current_agent", None) or brain._current_agent
    agent_id_local = agent.agent_id if agent else "main"

    if session_id and agent_id_local:
        folder = brain._get_artifact_session_folder(session_id)
        artifact_dir = os.path.join(brain.AGENTS_DIR, agent_id_local, "artifacts", folder)
    else:
        artifact_dir = os.path.join(brain.AGENTS_DIR, agent_id_local, "artifacts", "image_gen")
    os.makedirs(artifact_dir, exist_ok=True)

    saved: list[dict] = []
    for file_id, file_name in file_ids:
        if not file_name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            file_name += ".png"
        save_path = os.path.join(artifact_dir, file_name)

        dl_req = urllib.request.Request(
            f"{_MISTRAL_BASE}/v1/files/{file_id}/content",
            headers={"Authorization": f"Bearer {api_key}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(dl_req, timeout=60) as dl_resp:
                img_bytes = dl_resp.read()
        except Exception as e:
            return _err(f"generate_image: failed to download image {file_id}: {e}")

        with open(save_path, "wb") as f:
            f.write(img_bytes)

        brain._after_file_write(save_path, "created", agent_id_local)
        saved.append({"path": save_path, "size_kb": len(img_bytes) // 1024, "file_name": file_name})

    return _ok({
        "status": "generated",
        "images": saved,
        "prompt": full_prompt,
        "count": len(saved),
    })
