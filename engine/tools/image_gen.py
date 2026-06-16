# Image generation via Mistral Conversations API
# Cross-module deps imported lazily from brain at call time to avoid circular import
# (brain.py imports tool_generate_image near end of module evaluation).

import json
import os
import re
import threading
import urllib.error
import urllib.request


def _ok(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


def _slug_from_prompt(prompt: str) -> str:
    """A short, filesystem-safe, content-reflecting base name derived from the
    image prompt — so a generated file is `ginger-cat-on-a-windowsill.png`, not
    Mistral's artificial `image_generated_0.png`. Lowercased, non-word chars
    dropped, spaces → hyphens, capped at ~50 chars on a word boundary. Falls
    back to `image` when the prompt yields nothing usable (e.g. all punctuation
    or non-latin that strips empty)."""
    safe = re.sub(r"[^\w\s-]", "", (prompt or "").strip().lower())
    safe = re.sub(r"\s+", "-", safe).strip("-")
    if len(safe) > 50:
        safe = safe[:50].rsplit("-", 1)[0] or safe[:50]
    return safe or "image"


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
    session_id = brain.get_request_context().current_session_id
    agent = brain.get_request_context().current_agent or brain._current_agent
    agent_id_local = agent.agent_id if agent else "main"

    if session_id and agent_id_local:
        folder = brain._get_artifact_session_folder(session_id)
        artifact_dir = os.path.join(brain.AGENTS_DIR, agent_id_local, "artifacts", folder)
    else:
        artifact_dir = os.path.join(brain.AGENTS_DIR, agent_id_local, "artifacts", "image_gen")
    os.makedirs(artifact_dir, exist_ok=True)

    # Speaking filename from the prompt instead of Mistral's artificial
    # `image_generated_0.png`. Keep the extension Mistral returned (default
    # .png). Multiple images get a `-2`, `-3`, … suffix; an on-disk collision
    # bumps the suffix too so a re-generate in the same session never clobbers.
    _base = _slug_from_prompt(prompt)
    saved: list[dict] = []
    for _idx, (file_id, _orig_name) in enumerate(file_ids):
        _ext = os.path.splitext(_orig_name)[1].lower()
        if _ext not in (".png", ".jpg", ".jpeg", ".webp"):
            _ext = ".png"
        file_name = f"{_base}{_ext}" if len(file_ids) == 1 else f"{_base}-{_idx + 1}{_ext}"
        save_path = os.path.join(artifact_dir, file_name)
        _n = 2
        while os.path.exists(save_path):
            file_name = f"{_base}-{_n}{_ext}"
            save_path = os.path.join(artifact_dir, file_name)
            _n += 1

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


# ─── Diagram rendering (Mermaid → SVG/PNG/PDF artifact) ──────────────────────
# A dedicated, powerful diagram tool: renders Mermaid source server-side via
# mermaid-cli (diagram_render/node_modules/.bin/mmdc, its own bundled Chromium)
# into a real artifact file. Unlike inline ```mermaid (chat-only display), this
# produces a FILE the agent can: download, embed in HTML (inline SVG / <img>),
# reference in Markdown (![](path)), or insert into a generated PDF/DOCX via
# write_document. Mermaid 11 covers flowchart, sequence, class, state, ER,
# gantt, pie, journey, gitgraph, mindmap, timeline, quadrant, sankey, C4,
# block, requirement, packet, and more.

_MMDC_REL = "diagram_render/node_modules/.bin/mmdc"
_DIAGRAM_FORMATS = ("svg", "png", "pdf")


def _mmdc_path() -> str:
    """Absolute path to the project-local mermaid-cli binary, or '' if absent."""
    import brain as brain
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    p = os.path.join(root, _MMDC_REL)
    return p if os.path.exists(p) else ""


def tool_render_diagram(args: dict) -> str:
    """Render a Mermaid diagram to a real image artifact (SVG/PNG/PDF).

    args: code (mermaid source, required), format (svg|png|pdf, default svg),
          title (optional, → filename + heading), theme (default|dark|forest|
          neutral), background (transparent|white, default white).
    Returns {status, path, file_name, format, embed:{html,markdown}} so the
    caller can embed the result in an HTML/MD/PDF/DOCX it is producing."""
    import subprocess
    import tempfile
    import brain as brain

    code = (args.get("code") or "").strip()
    if not code:
        return _err("render_diagram: 'code' (Mermaid source) is required")
    fmt = (args.get("format") or "svg").lower().lstrip(".")
    if fmt not in _DIAGRAM_FORMATS:
        return _err(f"render_diagram: format must be one of {_DIAGRAM_FORMATS}")
    theme = (args.get("theme") or "default").lower()
    if theme not in ("default", "dark", "forest", "neutral"):
        theme = "default"
    bg = (args.get("background") or "white").lower()
    if bg not in ("transparent", "white"):
        bg = "white"
    title = (args.get("title") or "").strip()

    mmdc = _mmdc_path()
    if not mmdc:
        return _err("render_diagram: mermaid-cli not installed. Run "
                    "`cd diagram_render && npm install` (one-time, per machine).")

    # Artifact folder (same resolution as tool_generate_image).
    session_id = brain.get_request_context().current_session_id
    agent = brain.get_request_context().current_agent or brain._current_agent
    agent_id_local = agent.agent_id if agent else "main"
    if session_id and agent_id_local:
        folder = brain._get_artifact_session_folder(session_id)
        artifact_dir = os.path.join(brain.AGENTS_DIR, agent_id_local, "artifacts", folder)
    else:
        artifact_dir = os.path.join(brain.AGENTS_DIR, agent_id_local, "artifacts", "diagrams")
    os.makedirs(artifact_dir, exist_ok=True)

    base = _slug_from_prompt(title) if title else _slug_from_prompt(code[:60])
    if not base or base == "image":
        base = "diagram"
    file_name = f"{base}.{fmt}"
    save_path = os.path.join(artifact_dir, file_name)
    _n = 2
    while os.path.exists(save_path):
        file_name = f"{base}-{_n}.{fmt}"
        save_path = os.path.join(artifact_dir, file_name)
        _n += 1

    # mmdc reads a source file; spill the code to a tempfile.
    tmp_in = None
    try:
        fd, tmp_in = tempfile.mkstemp(suffix=".mmd", prefix="brain-diagram-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)
        cmd = [mmdc, "-i", tmp_in, "-o", save_path,
               "-t", theme, "-b", bg]
        env = dict(os.environ)
        # Puppeteer needs a writable home for its cache in the launchd sandbox.
        env.setdefault("HOME", os.path.expanduser("~"))
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90, env=env)
        if proc.returncode != 0 or not os.path.exists(save_path):
            err = (proc.stderr or proc.stdout or "unknown").strip()
            # Mermaid syntax errors come back here — surface them so the model
            # can fix the diagram source and retry.
            return _err(f"render_diagram: render failed — {err[:500]}")
    except subprocess.TimeoutExpired:
        return _err("render_diagram: render timed out (90s)")
    except Exception as e:
        return _err(f"render_diagram: {type(e).__name__}: {e}")
    finally:
        if tmp_in:
            try:
                os.remove(tmp_in)
            except OSError:
                pass

    brain._after_file_write(save_path, "created", agent_id_local)
    size_kb = os.path.getsize(save_path) // 1024
    alt = title or "Diagramm"
    # Embedding hints so the model can drop the diagram into a document it builds.
    embed = {
        "markdown": f"![{alt}]({file_name})",
        "html": (f'<img src="{file_name}" alt="{alt}">' if fmt != "svg"
                 else f'<!-- inline the SVG file contents here, or: --> <img src="{file_name}" alt="{alt}">'),
    }
    return _ok({
        "status": "rendered",
        "path": save_path,
        "file_name": file_name,
        "format": fmt,
        "size_kb": size_kb,
        "embed": embed,
        "note": ("Diagram saved as an artifact. To embed it in a document you are "
                 "creating: Markdown → use the embed.markdown snippet; HTML → embed.html "
                 "(for SVG you may inline the file's contents for crispness); PDF/DOCX "
                 "via write_document → reference this file as an image."),
    })
