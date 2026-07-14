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

    # M2 (G7) — UNCONDITIONAL cloud-egress check.
    #
    # This tool ALWAYS posts to api.mistral.ai (_MISTRAL_BASE), no matter which
    # model the session runs on. That breaks the assumption every other guard
    # rests on: "local model ⇒ nothing leaves the machine". It does not — the
    # egress happens at the TOOL, not at the chat model. Session 5175bf8fdf70 sent
    # a family tree (incl. who is deceased) to Mistral from a LOCAL session, where
    # anonymisation never runs and the mapping-gated gate therefore sees nothing.
    #
    # So the mapping-based gate in the dispatcher is necessary but not sufficient
    # here: it protects anonymising sessions. This scan protects the rest.
    try:
        _pii_egress_refusal = brain._gdpr_scan_cloud_egress(
            full_prompt, tool_name="generate_image")
        if _pii_egress_refusal:
            return _pii_egress_refusal
    except Exception:
        pass  # scanner failure must not break image generation outright

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

_MMDC_CLI_REL = "diagram_render/node_modules/@mermaid-js/mermaid-cli/src/cli.js"
_DIAGRAM_FORMATS = ("svg", "png", "pdf")


def _working_node() -> str:
    """Find a Node binary that actually runs. The mmdc shebang is `env node`,
    which under launchd resolves to homebrew node — and homebrew node has been
    seen broken by a stale llhttp dylib (libllhttp.9.3 missing after a 9.4
    upgrade) → dyld error. So we pick an explicitly-working node: try the nvm
    node that built mmdc first, then any node on PATH, verifying each with
    `--version` before trusting it. Returns '' if none works."""
    import glob
    import subprocess
    candidates = []
    # nvm-managed nodes (newest first) — these are self-contained, not affected
    # by homebrew's dylib churn.
    nvm = os.path.expanduser("~/.nvm/versions/node")
    if os.path.isdir(nvm):
        candidates += sorted(glob.glob(os.path.join(nvm, "*", "bin", "node")), reverse=True)
    # then PATH node / common locations
    candidates += ["node", "/usr/local/bin/node", "/opt/homebrew/bin/node"]
    for c in candidates:
        try:
            r = subprocess.run([c, "--version"], capture_output=True, text=True, timeout=8)
            if r.returncode == 0 and r.stdout.strip().startswith("v"):
                return c
        except Exception:
            continue
    return ""


def _mmdc_invocation() -> list:
    """[node, cli.js] to run mermaid-cli with a known-good node, or [] if the
    cli or a working node is missing."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cli = os.path.join(root, _MMDC_CLI_REL)
    if not os.path.exists(cli):
        return []
    node = _working_node()
    if not node:
        return []
    return [node, cli]


def _mix_hex(hex_a: str, hex_b: str, t: float) -> str:
    """Linear blend of two #rrggbb colors (t=0 → a, t=1 → b). Used to derive a
    palette (light fills, mid tones) from the preset's two brand colors."""
    try:
        a = hex_a.lstrip("#"); b = hex_b.lstrip("#")
        ar, ag, ab = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
        br, bg_, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
        r = round(ar + (br - ar) * t); g = round(ag + (bg_ - ag) * t); bl = round(ab + (bb - ab) * t)
        return f"#{max(0,min(255,r)):02x}{max(0,min(255,g)):02x}{max(0,min(255,bl)):02x}"
    except Exception:
        return hex_b


def _mermaid_theme_config(style: dict) -> dict | None:
    """Build a Mermaid init config (theme='base' + themeVariables) from a doc-style
    preset so diagrams carry the document's BRAND colors + font instead of
    Mermaid's generic pastel defaults. Returns None when the preset has no usable
    colors (caller then falls back to the named built-in theme).

    Maps the preset's two brand colors (accent + heading) and body color/font
    onto the Mermaid theme variables that actually move the needle: node fills/
    borders/text, edge/line color, and a 6-step pie palette derived by blending
    heading→accent→white so slices stay on-brand and distinguishable.
    """
    colors = (style or {}).get("colors") or {}
    fonts = (style or {}).get("fonts") or {}
    accent = colors.get("accent")
    heading = colors.get("heading") or accent
    body = colors.get("body") or "#222222"
    if not accent and not heading:
        return None
    accent = accent or heading
    heading = heading or accent
    font = fonts.get("body") or "Helvetica"
    # Node fill: a light tint of the accent (blend toward white) so dark text reads.
    node_fill = _mix_hex(accent, "#ffffff", 0.82)
    node_fill2 = _mix_hex(accent, "#ffffff", 0.70)
    node_fill3 = _mix_hex(accent, "#ffffff", 0.90)
    # Pie/series palette: heading → accent → light, 6 steps, dark→light so a
    # legend reads top-to-bottom and adjacent slices stay distinct.
    pie = [
        heading,
        accent,
        _mix_hex(accent, "#ffffff", 0.35),
        _mix_hex(accent, "#ffffff", 0.58),
        _mix_hex(accent, "#ffffff", 0.78),
        _mix_hex(heading, "#ffffff", 0.50),
    ]
    tv = {
        "fontFamily": f"{font}, Helvetica, Arial, sans-serif",
        "fontSize": "16px",
        "primaryColor": node_fill,
        "primaryBorderColor": heading,
        "primaryTextColor": body,
        "secondaryColor": node_fill2,
        "secondaryBorderColor": accent,
        "secondaryTextColor": body,
        "tertiaryColor": node_fill3,
        "tertiaryBorderColor": accent,
        "tertiaryTextColor": body,
        "lineColor": accent,
        "textColor": body,
        "titleColor": heading,
        # Pie-specific knobs (Mermaid reads pieN + stroke/title vars).
        "pieStrokeColor": heading,
        "pieStrokeWidth": "1px",
        "pieOuterStrokeColor": heading,
        "pieOuterStrokeWidth": "1px",
        "pieTitleTextSize": "18px",
        "pieSectionTextColor": "#ffffff",
        "pieLegendTextSize": "15px",
    }
    for _i, _c in enumerate(pie, start=1):
        tv[f"pie{_i}"] = _c
    return {"theme": "base", "themeVariables": tv}


_MERMAID_DIAGRAM_KEYWORDS = (
    "graph", "flowchart", "sequencediagram", "classdiagram", "statediagram",
    "erdiagram", "gantt", "pie", "journey", "gitgraph", "mindmap", "timeline",
    "quadrantchart", "requirementdiagram", "c4context", "sankey", "xychart",
    "block-beta", "architecture-beta",
)


def looks_like_mermaid(code: str) -> bool:
    """True if a code block's body opens with a Mermaid diagram keyword — so a
    bare ```gantt / flowchart block (no ```mermaid fence) is still recognised.
    Skips leading `%%` directive/comment lines before testing the first real line."""
    for raw in (code or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue
        head = re.split(r'[\s:]', line, 1)[0].lower()
        return head in _MERMAID_DIAGRAM_KEYWORDS
    return False


def render_mermaid_file(code: str, *, out_path: str, full_style: dict | None = None,
                        fmt: str = "png", theme: str | None = None,
                        background: str = "white", scale: float = 4.0,
                        width: int = 2000, explicit_theme: bool = False) -> str | None:
    """Render Mermaid `code` to `out_path` via mermaid-cli. Returns out_path on
    success, None on any failure (caller falls back). Shared by tool_render_diagram
    and the write_document auto-embed path so both render identically (same brand
    theming, high-DPI raster, working-node PATH fix)."""
    import subprocess
    import tempfile
    mmdc = _mmdc_invocation()
    if not mmdc:
        return None
    _mm_config = None
    if not explicit_theme:
        _mm_config = _mermaid_theme_config(full_style or {})
    theme = (theme or "default").lower()
    if theme not in ("default", "dark", "forest", "neutral"):
        theme = "default"
    if background not in ("transparent", "white"):
        background = "white"
    tmp_in = tmp_conf = None
    try:
        fd, tmp_in = tempfile.mkstemp(suffix=".mmd", prefix="brain-diagram-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)
        cmd = mmdc + ["-i", tmp_in, "-o", out_path, "-b", background]
        if _mm_config:
            import json as _json
            fd2, tmp_conf = tempfile.mkstemp(suffix=".json", prefix="brain-mmconf-")
            with os.fdopen(fd2, "w", encoding="utf-8") as cf:
                _json.dump(_mm_config, cf)
            cmd += ["-c", tmp_conf]
        else:
            cmd += ["-t", theme]
        if fmt in ("png", "pdf"):
            cmd += ["-s", str(scale), "-w", str(width)]
        env = dict(os.environ)
        env.setdefault("HOME", os.path.expanduser("~"))
        _nodedir = os.path.dirname(mmdc[0]) if mmdc and os.path.sep in mmdc[0] else ""
        if _nodedir:
            env["PATH"] = _nodedir + os.pathsep + env.get("PATH", "")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90, env=env)
        if proc.returncode != 0 or not os.path.exists(out_path):
            return None
        return out_path
    except Exception:
        return None
    finally:
        for _tmp in (tmp_in, tmp_conf):
            if _tmp:
                try:
                    os.remove(_tmp)
                except OSError:
                    pass


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
    # Default PNG (not SVG): a rendered SVG can NOT be embedded into the PDF or
    # DOCX writers (reportlab rejects SVG; python-docx add_picture can't read it)
    # and there is no reliable server-side SVG→PNG converter here (fitz mangles
    # Mermaid's CSS/foreignObject text; mermaid-cli won't re-ingest a finished
    # SVG; cairosvg/rsvg not installed). A high-DPI PNG embeds everywhere — PDF,
    # DOCX and HTML — so it is the safe universal default. SVG stays available
    # for HTML-only reports where vector zoom matters (explicit format=svg).
    fmt = (args.get("format") or "png").lower().lstrip(".")
    if fmt not in _DIAGRAM_FORMATS:
        return _err(f"render_diagram: format must be one of {_DIAGRAM_FORMATS}")
    # Theme/background: explicit arg wins; else inherit from the named doc style's
    # mermaid block so diagrams match the document they'll be embedded in.
    # Load the FULL preset (not just its mermaid sub-block) so we can derive a
    # brand themeVariables config from colors+fonts — the generic built-in themes
    # (default/neutral/forest) ignore brand color entirely, which is why default
    # diagrams look cheap. A preset is resolved even when none is named, so report
    # diagrams pick up the corporate look by default (mirrors write_document's
    # _resolve_default_style); pass style="" / a non-existent name to opt out.
    _style_mermaid = {}
    _full_style = {}
    try:
        from engine.tools.file_tools import _load_doc_style, _resolve_default_style
        _style_name = args.get("style")
        if _style_name is None:
            _style_name = _resolve_default_style("")
        if _style_name:
            _full_style = _load_doc_style(_style_name) or {}
            _style_mermaid = _full_style.get("mermaid", {}) or {}
    except Exception:
        _full_style = {}
        _style_mermaid = {}
    theme = (args.get("theme") or _style_mermaid.get("theme") or "default").lower()
    if theme not in ("default", "dark", "forest", "neutral"):
        theme = "default"
    bg = (args.get("background") or _style_mermaid.get("background") or "white").lower()
    if bg not in ("transparent", "white"):
        bg = "white"
    title = (args.get("title") or "").strip()
    # Raster resolution: mmdc defaults to scale=1 → small, blurry, unusable PNGs
    # (esp. wide org charts). Since PNG is now the default and lands in printed
    # PDFs/DOCX, render at high DPI by default so the image is crisp on screen,
    # in print, and reusable in documents. `scale` (1–5) is the device-pixel-
    # ratio multiplier; `width` is the base CSS width before scaling. SVG is
    # vector → unaffected by both.
    try:
        scale = float(args.get("scale") or 4)
    except (TypeError, ValueError):
        scale = 4
    scale = max(1.0, min(scale, 5.0))
    try:
        width = int(args.get("width") or 2000)
    except (TypeError, ValueError):
        width = 2000
    width = max(400, min(width, 6000))

    mmdc = _mmdc_invocation()
    if not mmdc:
        return _err("render_diagram: mermaid-cli not available — either it's not "
                    "installed (run `cd diagram_render && npm install`) or no working "
                    "Node was found (homebrew node may be broken by a dylib upgrade).")

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

    # Brand theming: when NO explicit theme arg was given and the preset carries
    # colors, render with a derived themeVariables config (theme='base') so nodes/
    # edges/pie slices use the document's brand palette + font. An explicit theme
    # arg (default/dark/forest/neutral) wins and uses mmdc's -t built-in instead.
    _mm_config = None
    if not args.get("theme"):
        _mm_config = _mermaid_theme_config(_full_style)

    # mmdc reads a source file; spill the code to a tempfile.
    tmp_in = None
    tmp_conf = None
    try:
        fd, tmp_in = tempfile.mkstemp(suffix=".mmd", prefix="brain-diagram-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)
        cmd = mmdc + ["-i", tmp_in, "-o", save_path, "-b", bg]
        if _mm_config:
            import json as _json
            fd2, tmp_conf = tempfile.mkstemp(suffix=".json", prefix="brain-mmconf-")
            with os.fdopen(fd2, "w", encoding="utf-8") as cf:
                _json.dump(_mm_config, cf)
            cmd += ["-c", tmp_conf]
        else:
            cmd += ["-t", theme]
        # High-DPI raster (PNG); also widen PDF. SVG ignores these (vector).
        if fmt in ("png", "pdf"):
            cmd += ["-s", str(scale), "-w", str(width)]
        env = dict(os.environ)
        # Puppeteer needs a writable home for its cache in the launchd sandbox.
        env.setdefault("HOME", os.path.expanduser("~"))
        # Force the chosen node's own bin dir to the FRONT of PATH so any child
        # `node`/`npm` mmdc spawns also uses the working node, not broken
        # homebrew node (the libllhttp.9.3 dyld error).
        _nodedir = os.path.dirname(mmdc[0]) if mmdc and os.path.sep in mmdc[0] else ""
        if _nodedir:
            env["PATH"] = _nodedir + os.pathsep + env.get("PATH", "")
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
        for _tmp in (tmp_in, tmp_conf):
            if _tmp:
                try:
                    os.remove(_tmp)
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
    note = ("Diagram saved as an artifact. To embed it in a document you are "
            "creating: Markdown → use the embed.markdown snippet; HTML → embed.html "
            "(for SVG you may inline the file's contents for crispness); PDF/DOCX "
            "via write_document → reference this file as an image.")
    if fmt == "svg":
        # The model explicitly overrode the PNG default. Warn loudly: SVG embeds
        # in HTML only — the PDF/DOCX writers can't place it (they emit a "render
        # as PNG" placeholder instead). Re-render with format=png for those.
        note += (" WARNING: this is an SVG — it embeds in HTML ONLY. write_document "
                 "CANNOT embed an SVG into a PDF or DOCX; if this diagram goes into a "
                 "PDF/DOCX report, call render_diagram again with format=png.")
    return _ok({
        "status": "rendered",
        "path": save_path,
        "file_name": file_name,
        "format": fmt,
        "size_kb": size_kb,
        "embed": embed,
        "note": note,
    })
