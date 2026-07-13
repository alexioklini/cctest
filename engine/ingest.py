"""engine/ingest.py — document/ingest pipeline (refactor E3).

Extracted from brain.py. Owns the four cohesive classes that parse documents
to text, chunk them, persist them as `<stem>__NNN.md` chunk files, and auto-ingest
watched folders:

  - `DocumentParser`  — format-detect → plain-text/markdown. The office/tabular
    parsers (`parse_docx/xlsx/pptx`) are thin shims over the unified
    `engine.doc_convert._do_extract` pipeline; the rest (pdf/txt/md/html/csv/
    image/svg/url) are self-contained stdlib/Pillow/fitz parsers.
  - `DocumentChunker` — paragraph/sentence/word chunking with section-header
    preservation + overlap. Pure (re + stdlib only).
  - `IngestManager`   — parse → chunk → write `<stem>__NNN.md` (stem = the
    original source filename, extension dropped) with frontmatter under
    `agents/<agent>/[projects/<proj>/]ingested/`; list/delete.
  - `IngestWatcher`   — background poll loop over agent.json `ingest_watch` +
    project.json `watch_folders`, mtime/size-diffing into an `ingest_registry.json`.

Dependency seams:
  - `engine.doc_convert._do_extract` — already in engine/; imported DIRECTLY
    (one-way, no cycle) inside the DocumentParser office shims.
  - brain runtime symbols (`AGENTS_DIR`, `_qmd_debounced_embed`, `_yaml_escape`,
    `_parse_frontmatter`) — reached via LAZY `import brain as _brain` inside the
    methods that need them. Top-level `import brain` would cycle (brain imports
    this module for the re-export).

NOT moved: the `_ingest_watcher` module-level singleton stays in brain.py — it
is `None` there and assigned externally (`server.py: engine._ingest_watcher =
engine.IngestWatcher()`), which sets the brain-module attribute. Re-exporting a
mutable singleton binding would desync it.
"""

import datetime
import fnmatch
import hashlib
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import urllib.request


# ─── Document Ingestion Engine ────────────────────────────────────────

class DocumentParser:
    """Parse various document formats to plain text."""

    # Must stay in sync with the parsers dict in parse() below. Used by the
    # async upload queue for the CHEAP pre-stage rejection (unsupported types
    # fail synchronously with the same wording parse() would raise, so the
    # client's reason translation keeps working without an extraction round).
    # Audio/video → transcribed via the shared STT pipeline (parse_audio).
    # Kept as its own set so the dispatch table and this gate can't drift.
    AUDIO_EXTS = frozenset({
        ".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac",
        ".mp4", ".mov", ".webm",
    })

    SUPPORTED_EXTS = frozenset({
        ".pdf", ".docx", ".txt", ".md", ".html", ".htm", ".xlsx", ".xls",
        ".pptx", ".eml", ".msg", ".csv", ".tsv",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
    }) | AUDIO_EXTS

    @staticmethod
    def parse_pdf(path: str) -> str:
        """Parse PDF to markdown. Thin shim over the unified doc_convert
        pipeline (pymupdf4llm/fitz → OCR for image-only scans) so project
        ingestion extracts text EXACTLY like chat attachments (read_document)
        and input-folder mining — same single choke point. Previously this
        called bare fitz.get_text(), which returned nothing for scanned PDFs
        and never reached OCR, so a scanned PDF failed the whole project
        import. caps=False = full fidelity (no row/cell cap), matching the
        read_document path."""
        from engine.doc_convert import _do_extract
        text, _backend, err = _do_extract(path, caps=False)
        if err:
            raise RuntimeError(err)
        return text

    @staticmethod
    def parse_docx(path: str) -> str:
        """Parse DOCX to markdown. Thin shim over the unified doc_convert
        pipeline (markitdown-first → _extract_docx fallback) so there is a
        single DOCX implementation shared with read_document and project
        mining."""
        from engine.doc_convert import _do_extract
        text, _backend, err = _do_extract(path)
        if err:
            raise RuntimeError(err)
        return text

    @staticmethod
    def parse_txt(path: str) -> str:
        """Parse plain text file."""
        with open(path, "r", errors="replace") as f:
            return f.read()

    @staticmethod
    def parse_md(path: str) -> str:
        """Parse markdown file (keep as-is)."""
        with open(path, "r", errors="replace") as f:
            return f.read()

    @staticmethod
    def parse_html(content: str) -> str:
        """Strip HTML tags and extract text content."""
        from html.parser import HTMLParser

        class _TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self._parts: list[str] = []
                self._skip = False
                self._skip_tags = {"script", "style", "nav", "header", "footer", "noscript"}

            def handle_starttag(self, tag, attrs):
                if tag in self._skip_tags:
                    self._skip = True
                elif tag in ("p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
                    self._parts.append("\n")
                    if tag.startswith("h"):
                        level = int(tag[1])
                        self._parts.append("#" * level + " ")

            def handle_endtag(self, tag):
                if tag in self._skip_tags:
                    self._skip = False
                elif tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6"):
                    self._parts.append("\n")

            def handle_data(self, data):
                if not self._skip:
                    self._parts.append(data)

        extractor = _TextExtractor()
        extractor.feed(content)
        text = "".join(extractor._parts)
        # Clean up whitespace
        lines = [line.strip() for line in text.split("\n")]
        cleaned = "\n".join(lines)
        # Collapse multiple blank lines
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned.strip()

    @staticmethod
    def parse_xlsx(path: str, sheet: str | None = None) -> str:
        """Parse XLSX to markdown tables. Thin shim over the unified
        doc_convert pipeline (markitdown-first → _extract_xlsx fallback) —
        single XLSX implementation shared with read_document and project
        mining. caps=False = full fidelity (no row cap)."""
        from engine.doc_convert import _do_extract
        text, _backend, err = _do_extract(path, caps=False, sheet=sheet)
        if err:
            raise RuntimeError(err)
        return text

    @staticmethod
    def parse_pptx(path: str, slides: str | None = None) -> str:
        """Parse PPTX to markdown. Thin shim over the unified doc_convert
        pipeline (markitdown-first → _extract_pptx fallback) — single PPTX
        implementation shared with read_document and project mining."""
        from engine.doc_convert import _do_extract
        text, _backend, err = _do_extract(path, slides=slides)
        if err:
            raise RuntimeError(err)
        return text

    @staticmethod
    def parse_email(path: str) -> str:
        """Parse .eml/.msg to markdown. Thin shim over the unified doc_convert
        pipeline (stdlib _extract_eml / _extract_msg) so email attachments
        ingest into a project exactly like they read in chat. Without this,
        .eml/.msg hit the 'Unsupported format' ValueError → HTTP 400."""
        from engine.doc_convert import _do_extract
        text, _backend, err = _do_extract(path)
        if err:
            raise RuntimeError(err)
        return text

    @staticmethod
    def parse_audio(path: str) -> str:
        """Transcribe an audio/video file to markdown via the SHARED STT path.

        Same choke point web_fetch's audio branch, the Übersetzen tab and the
        transcribe_audio tool use (`transcribe_and_translate` → model resolver
        + cost logging, purpose='transcribe'; local Whisper default = $0), so
        an .mp3 dropped into a project mines like any other document instead of
        being rejected as an unsupported format."""
        from server_lib.translate import media as _media
        res = _media.transcribe_and_translate(path, target_lang="")
        text = (res.get("transcript") or "").strip()
        if not text:
            # Empty transcript = silent/undecodable audio. Return nothing so the
            # caller's "no content extracted" path reports it like any other
            # empty document (rather than storing a header-only chunk).
            return ""
        dur = float(res.get("duration_s") or 0)
        # No markdown header here: DocumentChunker treats the first heading as
        # the chunk `title`, so a "## Transcript" line would title every audio
        # doc "## Transcript" instead of the filename.
        header = [
            f"**Audio:** {os.path.basename(path)}",
            f"**Duration:** {int(dur // 60)}:{int(dur % 60):02d} min",
            f"**Language:** {res.get('language') or '?'}",
            "",
        ]
        return "\n".join(header) + text + "\n"

    @staticmethod
    def parse_csv(path: str) -> str:
        """Parse CSV/TSV to markdown table."""
        import csv
        delimiter = "\t" if path.lower().endswith(".tsv") else ","
        with open(path, "r", newline="", errors="replace") as f:
            reader = csv.reader(f, delimiter=delimiter)
            rows = [row for row in reader]
        if not rows:
            return "*(empty file)*"
        max_cols = max(len(r) for r in rows)
        for r in rows:
            while len(r) < max_cols:
                r.append("")
        header = "| " + " | ".join(rows[0]) + " |"
        sep = "| " + " | ".join("---" for _ in range(max_cols)) + " |"
        lines = [header, sep]
        for r in rows[1:]:
            lines.append("| " + " | ".join(r) + " |")
        return "\n".join(lines)

    @staticmethod
    def parse_image(path: str) -> str:
        """Parse image metadata using Pillow. Returns metadata text."""
        try:
            from PIL import Image
        except ImportError:
            raise ImportError("Install Pillow for image support: pip3 install Pillow")
        img = Image.open(path)
        width, height = img.size
        fmt = img.format or os.path.splitext(path)[1].lstrip(".")
        mode = img.mode
        info_parts = [
            f"**Image:** {os.path.basename(path)}",
            f"**Dimensions:** {width} x {height}",
            f"**Format:** {fmt}",
            f"**Mode:** {mode}",
        ]
        # Extract EXIF if available
        try:
            exif = img.getexif()
            if exif:
                for tag_id, value in list(exif.items())[:10]:
                    try:
                        from PIL.ExifTags import TAGS
                        tag_name = TAGS.get(tag_id, str(tag_id))
                        info_parts.append(f"**{tag_name}:** {value}")
                    except Exception:
                        pass
        except Exception:
            pass
        img.close()
        return "\n".join(info_parts)

    @staticmethod
    def parse_svg(path: str) -> str:
        """Parse SVG to extract text elements and metadata."""
        from xml.etree import ElementTree
        tree = ElementTree.parse(path)
        root = tree.getroot()
        ns = {"svg": "http://www.w3.org/2000/svg"}
        parts = [f"**SVG:** {os.path.basename(path)}"]
        # Get dimensions
        w = root.get("width", "")
        h = root.get("height", "")
        vb = root.get("viewBox", "")
        if w and h:
            parts.append(f"**Dimensions:** {w} x {h}")
        if vb:
            parts.append(f"**ViewBox:** {vb}")
        # Extract title and desc
        for tag in ("title", "desc"):
            el = root.find(f"svg:{tag}", ns) or root.find(tag)
            if el is not None and el.text:
                parts.append(f"**{tag.capitalize()}:** {el.text.strip()}")
        # Extract all text elements
        texts = []
        for text_el in list(root.iter(f"{{{ns['svg']}}}text")) + list(root.iter("text")):
            t = "".join(text_el.itertext()).strip()
            if t:
                texts.append(t)
        if texts:
            parts.append(f"\n**Text content:**")
            for t in texts:
                parts.append(f"- {t}")
        return "\n".join(parts)

    @staticmethod
    def parse_url(url: str) -> str:
        """Fetch URL and parse HTML to text."""
        req_headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        req = urllib.request.Request(url, headers=req_headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            encoding = resp.headers.get("Content-Encoding", "")
            if encoding == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            charset = resp.headers.get_content_charset() or "utf-8"
            html = raw.decode(charset, errors="replace")
        return DocumentParser.parse_html(html)

    @staticmethod
    def parse(path_or_url: str) -> tuple[str, str]:
        """Auto-detect format and parse. Returns (text, source_type)."""
        if path_or_url.startswith(("http://", "https://")):
            return DocumentParser.parse_url(path_or_url), "url"
        ext = os.path.splitext(path_or_url)[1].lower()
        parsers = {
            ".pdf": ("pdf", DocumentParser.parse_pdf),
            ".docx": ("docx", DocumentParser.parse_docx),
            ".txt": ("txt", DocumentParser.parse_txt),
            ".md": ("md", DocumentParser.parse_md),
            ".html": ("html", lambda p: DocumentParser.parse_html(open(p, "r", errors="replace").read())),
            ".htm": ("html", lambda p: DocumentParser.parse_html(open(p, "r", errors="replace").read())),
            ".xlsx": ("xlsx", DocumentParser.parse_xlsx),
            ".xls": ("xlsx", DocumentParser.parse_xlsx),
            ".pptx": ("pptx", DocumentParser.parse_pptx),
            ".eml": ("email", DocumentParser.parse_email),
            ".msg": ("email", DocumentParser.parse_email),
            ".csv": ("csv", DocumentParser.parse_csv),
            ".tsv": ("csv", DocumentParser.parse_csv),
            ".png": ("image", DocumentParser.parse_image),
            ".jpg": ("image", DocumentParser.parse_image),
            ".jpeg": ("image", DocumentParser.parse_image),
            ".gif": ("image", DocumentParser.parse_image),
            ".webp": ("image", DocumentParser.parse_image),
            ".bmp": ("image", DocumentParser.parse_image),
            ".svg": ("svg", DocumentParser.parse_svg),
        }
        parsers.update({e: ("audio", DocumentParser.parse_audio)
                        for e in DocumentParser.AUDIO_EXTS})
        if ext not in parsers:
            raise ValueError(f"Unsupported format: {ext}. Supported: {', '.join(parsers.keys())}")
        source_type, parser_fn = parsers[ext]
        return parser_fn(path_or_url), source_type


class DocumentChunker:
    """Split text into overlapping chunks with section header preservation."""

    @staticmethod
    def chunk(text: str, chunk_size: int = 1500, chunk_overlap: int = 200,
              min_chunk_size: int = 100) -> list[dict]:
        """Split text into chunks. chunk_size/overlap are in ~tokens (chars/4 approximation).
        Returns list of {text, index, total, header}."""
        # Convert token counts to char approximation
        max_chars = chunk_size * 4
        overlap_chars = chunk_overlap * 4
        min_chars = min_chunk_size * 4

        # Split into paragraphs
        paragraphs = re.split(r'\n\s*\n', text)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

        chunks: list[dict] = []
        current_parts: list[str] = []
        current_len = 0
        last_header = ""

        def _flush():
            nonlocal current_parts, current_len
            if not current_parts:
                return
            chunk_text = "\n\n".join(current_parts)
            if len(chunk_text) < min_chars:
                return
            # Prepend section header if available
            if last_header and not chunk_text.startswith("#"):
                chunk_text = last_header + "\n\n" + chunk_text
            chunks.append({
                "text": chunk_text,
                "index": len(chunks),
                "total": 0,  # filled in later
                "header": last_header,
            })
            # Keep overlap: take text from end of current chunk
            if overlap_chars > 0:
                overlap_text = chunk_text[-overlap_chars:]
                current_parts = [overlap_text]
                current_len = len(overlap_text)
            else:
                current_parts = []
                current_len = 0

        for para in paragraphs:
            # Track section headers
            header_match = re.match(r'^(#{1,6}\s+.+)', para.split('\n')[0])
            if header_match:
                last_header = header_match.group(1)

            # If a single paragraph exceeds max_chars, split it
            if len(para) > max_chars:
                # Flush current buffer first
                if current_parts:
                    _flush()
                # Split on sentences
                sentences = re.split(r'(?<=[.!?])\s+', para)
                for sent in sentences:
                    if len(sent) > max_chars:
                        # Split on words as last resort
                        words = sent.split()
                        for word in words:
                            if current_len + len(word) + 1 > max_chars:
                                _flush()
                            current_parts.append(word)
                            current_len += len(word) + 1
                    else:
                        if current_len + len(sent) + 1 > max_chars:
                            _flush()
                        current_parts.append(sent)
                        current_len += len(sent) + 1
            else:
                if current_len + len(para) + 2 > max_chars:
                    _flush()
                current_parts.append(para)
                current_len += len(para) + 2

        # Flush remaining
        if current_parts:
            chunk_text = "\n\n".join(current_parts)
            if len(chunk_text) >= min_chars:
                if last_header and not chunk_text.startswith("#"):
                    chunk_text = last_header + "\n\n" + chunk_text
                chunks.append({
                    "text": chunk_text,
                    "index": len(chunks),
                    "total": 0,
                    "header": last_header,
                })

        # Fill in total count
        for c in chunks:
            c["total"] = len(chunks)

        return chunks


class IngestManager:
    """Ingest files and URLs into agent or project memory as chunked markdown."""

    @staticmethod
    def _source_hash(source: str) -> str:
        """6-char hash of source name/URL.

        Retained for the legacy chunk-filename scheme and as a uniqueness
        fallback when a sanitized stem would otherwise be empty. The on-disk
        chunk filename + grouping key is now stem-based — see `_source_key`.
        """
        return hashlib.sha256(source.encode()).hexdigest()[:6]

    # ── Stem-based chunk naming ──────────────────────────────────────────
    # Chunk files are named `<key>__NNN.md`, where `<key>` is a filesystem-safe
    # rendering of the ORIGINAL source filename (extension dropped). The key
    # doubles as the public `source_hash` group key returned by list_ingested
    # and consumed by the delete endpoints, the data-review reviewer, the
    # project-sync daemon (per-upload grouping + drawer-count + KG scoping),
    # and the project-tree UI. `__` (double underscore) separates key from the
    # 3-digit chunk index; the sanitizer guarantees the key never contains it,
    # so `_key_from_filename` can split unambiguously.

    @staticmethod
    def _safe_stem(source: str) -> str:
        """Sanitize a source name/URL into a filesystem-safe filename stem.

        Drops the directory + extension, keeps the original characters as far
        as they're safe (letters/digits/`.`/`-`/space → space-to-`_`), collapses
        runs, and strips the `__` separator so it can't collide with the index
        delimiter. Falls back to the 6-char source hash when nothing usable
        survives (e.g. a URL of pure punctuation)."""
        base = os.path.basename(source.rstrip("/")) or source
        stem = os.path.splitext(base)[0]
        # Allow word chars, dot and hyphen; turn whitespace into underscore.
        stem = re.sub(r"\s+", "_", stem.strip())
        stem = re.sub(r"[^\w.\-]", "_", stem, flags=re.UNICODE)
        stem = re.sub(r"_+", "_", stem).strip("_.")
        # `__` is the reserved key/index delimiter — collapse any in the stem.
        stem = stem.replace("__", "_")
        if not stem:
            return f"src-{IngestManager._source_hash(source)}"
        return stem

    @staticmethod
    def _source_key(ingest_dir: str, source: str) -> str:
        """Resolve the unique group key (== filename stem) for `source` in
        `ingest_dir`. Reuses the key of an existing ingest of the SAME source
        (so re-ingest overwrites in place); otherwise derives a fresh stem and
        disambiguates against a different source already holding that stem."""
        wanted = IngestManager._safe_stem(source)
        existing: dict[str, str] = {}  # key -> source recorded in frontmatter
        try:
            for fname in os.listdir(ingest_dir):
                key = IngestManager._key_from_filename(fname)
                if not key or key in existing:
                    continue
                src = IngestManager._read_chunk_source(
                    os.path.join(ingest_dir, fname))
                existing[key] = src
        except OSError:
            pass
        # Re-ingest of the same source → reuse its key.
        for key, src in existing.items():
            if src == source:
                return key
        # Fresh source. Disambiguate if the wanted stem is taken by another.
        if wanted not in existing:
            return wanted
        n = 2
        while f"{wanted}-{n}" in existing:
            n += 1
        return f"{wanted}-{n}"

    @staticmethod
    def _chunk_filename(key: str, idx: int) -> str:
        """Chunk filename for a group key + chunk index."""
        return f"{key}__{idx:03d}.md"

    @staticmethod
    def chunk_filename_prefix(ingest_dir: str, key: str) -> str:
        """Filename prefix shared by all chunks of `key`. Joined with the
        ingest dir, this is the `source_file` prefix the project-sync daemon
        uses to count drawers + scope KG extraction per upload.

        New scheme → `<key>__`; legacy scheme → `ingest-<key>-`. Resolved by
        inspecting which scheme the key's chunks are actually stored under, so
        both coexist during the transition."""
        new_prefix = f"{key}__"
        legacy_prefix = f"ingest-{key}-"
        try:
            for fname in os.listdir(ingest_dir):
                if fname.startswith(new_prefix):
                    return new_prefix
                if fname.startswith(legacy_prefix) and fname.endswith(".md"):
                    return legacy_prefix
        except OSError:
            pass
        return new_prefix

    @staticmethod
    def _key_from_filename(fname: str) -> str | None:
        """Reverse of `_chunk_filename`. Tolerates the legacy
        `ingest-<hash>-NNN.md` scheme so pre-existing chunks still group/list/
        delete. Returns None for non-chunk files."""
        if not fname.endswith(".md"):
            return None
        stem = fname[:-3]
        if "__" in stem:
            key, _, tail = stem.rpartition("__")
            if key and tail.isdigit():
                return key
            return None
        # Legacy: ingest-<hash>-NNN.md → key was the <hash> segment.
        if fname.startswith("ingest-"):
            parts = fname.split("-", 2)
            if len(parts) >= 2 and parts[1]:
                return parts[1]
        return None

    # Frontmatter can outgrow any fixed byte cap: `title:` + `source:` both
    # carry the full relative path, and middle chunks add TWO `related:` entries
    # (prev+next, each a full chunk filename), so a deep folder import lands at
    # ~970 chars while chunk __000 (next only) fits in 700. A capped read that
    # cuts the block loses its closing `---`, and _parse_frontmatter's regex
    # then matches NOTHING and returns {} — every field silently falls back to
    # its default ("unknown" in the docs list; "" in the dedup key). Read the
    # header until the terminator instead of guessing its size.
    _FM_READ_CHUNK = 4096
    _FM_READ_MAX = 65536

    @staticmethod
    def _read_frontmatter(fpath: str) -> dict:
        """Parse a chunk file's frontmatter, reading only as far as it spans."""
        import brain as _brain
        with open(fpath, "r", errors="replace") as f:
            raw = f.read(IngestManager._FM_READ_CHUNK)
            if not raw.startswith("---"):
                return {}
            # Grow until the closing '---' is in hand (or the file/cap ends).
            while ("\n---" not in raw[3:]
                   and len(raw) < IngestManager._FM_READ_MAX):
                more = f.read(IngestManager._FM_READ_CHUNK)
                if not more:
                    break
                raw += more
        fm, _ = _brain._parse_frontmatter(raw)
        return fm

    @staticmethod
    def _yaml_unquote(s: str) -> str:
        """Invert brain._yaml_escape: strip the surrounding quotes + unescape.

        _parse_frontmatter returns values VERBATIM, so a source that
        _yaml_escape quoted ("Kunde-A/Bericht.txt" — any value with '-', '/'
        neighbours etc.) came back with literal quotes. That broke re-ingest
        dedup (_source_key compared '"x"' against 'x' → never matched → a
        re-upload minted a fresh -2 key instead of overwriting) and leaked a
        trailing quote into the docs list UI. Pre-existing; surfaced by the
        async-upload tests (9.324.0)."""
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        return s

    @staticmethod
    def _read_chunk_source(fpath: str) -> str:
        """Read the `source:` frontmatter value from a chunk file."""
        try:
            fm = IngestManager._read_frontmatter(fpath)
            return IngestManager._yaml_unquote(fm.get("source", ""))
        except Exception:
            return ""

    @staticmethod
    def _ingest_dir(agent_id: str, project_name: str | None = None) -> str:
        """Get the directory where ingested chunks are stored."""
        import brain as _brain
        if project_name:
            d = os.path.join(_brain.AGENTS_DIR, agent_id, "projects", project_name, "ingested")
        else:
            d = os.path.join(_brain.AGENTS_DIR, agent_id, "ingested")
        os.makedirs(d, exist_ok=True)
        return d

    @staticmethod
    def _collection_name(agent_id: str, project_name: str | None = None) -> str:
        """QMD collection name for embedding."""
        if project_name:
            return f"{agent_id}/{project_name}"
        return agent_id

    @staticmethod
    def ingest_file(agent_id: str, file_path: str,
                    project_name: str | None = None,
                    tags: list[str] | None = None,
                    chunk_size: int = 1500, chunk_overlap: int = 200,
                    source_name: str | None = None,
                    key_override: str | None = None) -> dict:
        """Parse, chunk, and store a file as ingested memory chunks.

        `source_name` overrides the recorded source (default: the temp file's
        basename). A folder import passes the RELATIVE PATH ("Kunde-A/x.pdf")
        here so two same-named files in different groups get DISTINCT source
        keys (_source_key disambiguates by full source string → `-2` suffix);
        without it both collapse to the same key and the second overwrites the
        first.

        `key_override` pins the chunk-group key instead of recomputing it —
        used by the async upload queue (IngestQueue), which RESERVES the key at
        stage time so the client learns the source_hash before extraction has
        run. Recomputing here would race a second staged same-stem file onto
        the same key."""
        if not os.path.exists(file_path):
            return {"error": f"File not found: {file_path}"}
        source_name = source_name or os.path.basename(file_path)
        try:
            text, source_type = DocumentParser.parse(file_path)
        except (ImportError, ValueError) as e:
            return {"error": str(e)}
        return IngestManager._store_chunks(
            agent_id, project_name, source_name, source_type, text,
            tags=tags, chunk_size=chunk_size, chunk_overlap=chunk_overlap,
            key_override=key_override,
        )

    @staticmethod
    def ingest_url(agent_id: str, url: str,
                   project_name: str | None = None,
                   tags: list[str] | None = None,
                   chunk_size: int = 1500, chunk_overlap: int = 200) -> dict:
        """Fetch URL, parse HTML, chunk, and store."""
        try:
            text = DocumentParser.parse_url(url)
        except Exception as e:
            return {"error": f"Failed to fetch URL: {e}"}
        return IngestManager._store_chunks(
            agent_id, project_name, url, "url", text,
            tags=tags, chunk_size=chunk_size, chunk_overlap=chunk_overlap,
        )

    @staticmethod
    def _store_chunks(agent_id: str, project_name: str | None,
                      source: str, source_type: str, text: str,
                      tags: list[str] | None = None,
                      chunk_size: int = 1500, chunk_overlap: int = 200,
                      key_override: str | None = None) -> dict:
        """Chunk text and write as `<stem>__NNN.md` files with frontmatter.

        The chunk filename preserves the original source filename (extension
        dropped); `key` is that stem and doubles as the public `source_hash`
        group key. See `_source_key` (or `key_override`, reserved at stage
        time by the async upload queue)."""
        import brain as _brain
        ingest_dir = IngestManager._ingest_dir(agent_id, project_name)
        key = key_override or IngestManager._source_key(ingest_dir, source)
        collection = IngestManager._collection_name(agent_id, project_name)

        # Delete existing chunks for this source (re-ingest) — both schemes.
        existing = [f for f in os.listdir(ingest_dir)
                    if IngestManager._key_from_filename(f) == key]
        for f in existing:
            os.remove(os.path.join(ingest_dir, f))

        # Chunk. min_chunk_size=1 (not the 100-token/400-char default) so a
        # SHORT but legitimate document (a note, a chat-protocol template like
        # "Chatprotokoll für: …\nNotizen:", a one-line memo) still produces one
        # chunk instead of being silently dropped. The 400-char floor was meant
        # to discard junk fragments, but it also threw away real short files →
        # "No content extracted" on a document that clearly HAS text. A truly
        # empty extraction (0 chars) still yields 0 chunks → the error below.
        chunks = DocumentChunker.chunk(text, chunk_size=chunk_size,
                                       chunk_overlap=chunk_overlap, min_chunk_size=1)
        if not chunks:
            return {"error": "No content extracted from document"}

        all_tags = ["ingested"]
        if tags:
            all_tags.extend(tags)
        # Add source name as tag (sanitized)
        safe_source_tag = re.sub(r'[^\w-]', '', source.split("/")[-1].split(".")[0].lower())
        if safe_source_tag:
            all_tags.append(safe_source_tag)
        tags_yaml = "\n".join(f"  - {t}" for t in all_tags)

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        files_written = []
        for chunk in chunks:
            idx = chunk["index"]
            total = chunk["total"]
            title = chunk["header"] or f"{source} - Chunk {idx + 1}"

            # Build related links
            related_lines = []
            if idx > 0:
                prev_file = IngestManager._chunk_filename(key, idx - 1)
                related_lines.append(f"  - file: {prev_file}\n    type: prev_chunk")
            if idx < total - 1:
                next_file = IngestManager._chunk_filename(key, idx + 1)
                related_lines.append(f"  - file: {next_file}\n    type: next_chunk")
            if idx != 0:
                first_file = IngestManager._chunk_filename(key, 0)
                related_lines.append(f"  - file: {first_file}\n    type: same_source")
            related_yaml = ""
            if related_lines:
                related_yaml = "related:\n" + "\n".join(related_lines) + "\n"

            filename = IngestManager._chunk_filename(key, idx)
            md_content = f"""---
title: {_brain._yaml_escape(title)}
source: {_brain._yaml_escape(source)}
source_type: {source_type}
source_hash: {key}
ingested_at: "{now}"
chunk_index: {idx}
total_chunks: {total}
agent: {agent_id}
tags:
{tags_yaml}
{related_yaml}---

{chunk['text']}
"""
            fpath = os.path.join(ingest_dir, filename)
            with open(fpath, "w") as f:
                f.write(md_content)
            files_written.append(filename)

        word_count = len(text.split())
        return {
            "source": source,
            "source_type": source_type,
            "source_hash": key,
            "chunks": len(chunks),
            "words": word_count,
            "files": files_written,
            "agent": agent_id,
            "project": project_name,
            "status": "ingested",
        }

    @staticmethod
    def list_ingested(agent_id: str, project_name: str | None = None) -> list[dict]:
        """List ingested documents grouped by source."""
        import brain as _brain
        ingest_dir = IngestManager._ingest_dir(agent_id, project_name)
        if not os.path.isdir(ingest_dir):
            return []
        # Group by source key (filename stem; frontmatter source_hash wins).
        groups: dict[str, dict] = {}
        for fname in os.listdir(ingest_dir):
            src_hash = IngestManager._key_from_filename(fname)
            if not src_hash:
                continue
            fpath = os.path.join(ingest_dir, fname)
            try:
                fm = IngestManager._read_frontmatter(fpath)
            except Exception:
                continue
            source = IngestManager._yaml_unquote(fm.get("source", "unknown"))
            src_hash = fm.get("source_hash") or src_hash
            if src_hash not in groups:
                groups[src_hash] = {
                    "source": source,
                    "source_type": fm.get("source_type", "unknown"),
                    "source_hash": src_hash,
                    "chunks": 0,
                    "ingested_at": fm.get("ingested_at", ""),
                    "tags": [],
                }
            groups[src_hash]["chunks"] += 1
            # os.listdir order is arbitrary, so the group may have been seeded
            # from a chunk whose header didn't parse. Let any chunk that DOES
            # carry a real value replace the placeholder — the listing must not
            # depend on which chunk happened to come first.
            if groups[src_hash]["source"] == "unknown" and source != "unknown":
                groups[src_hash]["source"] = source
            if (groups[src_hash]["source_type"] == "unknown"
                    and fm.get("source_type")):
                groups[src_hash]["source_type"] = fm["source_type"]
            if not groups[src_hash]["ingested_at"] and fm.get("ingested_at"):
                groups[src_hash]["ingested_at"] = fm["ingested_at"]
            # Parse tags from frontmatter
            tags_str = fm.get("tags", "")
            if isinstance(tags_str, str) and tags_str:
                for t in tags_str.split(","):
                    t = t.strip().strip("-").strip()
                    if t and t not in groups[src_hash]["tags"]:
                        groups[src_hash]["tags"].append(t)
        return sorted(groups.values(), key=lambda x: x.get("ingested_at", ""), reverse=True)

    @staticmethod
    def delete_ingested(agent_id: str, source_hash: str,
                        project_name: str | None = None) -> dict:
        """Delete all chunks for a source hash."""
        ingest_dir = IngestManager._ingest_dir(agent_id, project_name)
        if not os.path.isdir(ingest_dir):
            return {"error": "No ingested documents found"}
        deleted = 0
        source_name = ""
        for fname in os.listdir(ingest_dir):
            if IngestManager._key_from_filename(fname) == source_hash:
                if not source_name:
                    fpath = os.path.join(ingest_dir, fname)
                    try:
                        fm = IngestManager._read_frontmatter(fpath)
                        source_name = IngestManager._yaml_unquote(
                            fm.get("source", ""))
                    except Exception:
                        pass
                os.remove(os.path.join(ingest_dir, fname))
                deleted += 1
        return {"source": source_name, "source_hash": source_hash, "deleted": deleted}


# ─── Watched Folders (Auto-Ingestion) ────────────────────────────────

# ─── Async upload ingestion (staging + extraction worker pool) ───────────────
#
# The /ingest HTTP handler used to run the FULL extraction inline on the
# request thread. For a scanned PDF that means pymupdf4llm (60s budget) plus
# cloud OCR (300s budget) — far past the Cloudflare tunnel's ~100s response
# limit, so the browser got HTTP 524 while the server kept working, and the
# client's upload loop stalled for minutes per file. Since the handler also
# DISCARDED the original bytes after extraction, the work could not be deferred
# without first persisting them.
#
# New shape: the handler calls IngestManager.stage_upload(), which writes the
# original bytes + a metadata sidecar under <pdir>/ingest-staging/, RESERVES
# the chunk-group key (so the client gets its source_hash immediately for
# group assignment), and returns. The IngestQueue worker pool then runs the
# exact same IngestManager.ingest_file() path — same chunk .md layout in
# ingested/, same downstream contract (docs list, project-sync mining, KG).
# When a project's staged jobs drain, the queue kicks the project-sync daemon
# so mining doesn't wait for the next scheduled pass.

_STAGING_DIRNAME = "ingest-staging"


class IngestQueue:
    """Extraction worker pool for staged project-file uploads."""

    WORKERS = 2                      # OCR/markitdown are heavy; keep modest
    DONE_TTL_SECS = 24 * 3600        # prune terminal registry entries after

    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        # (agent_id, project_name) -> {key: {state, filename, source, ...}}
        self._jobs: dict[tuple[str, str], dict[str, dict]] = {}
        self._threads: list[threading.Thread] = []

    # ── paths ────────────────────────────────────────────────────────────
    @staticmethod
    def _staging_dir(agent_id: str, project_name: str | None) -> str:
        # Sibling of ingested/ (same parent dir), NOT inside it — the docs
        # list, the sync daemon and _source_key all enumerate ingested/ and
        # must never see raw originals there.
        ingest_dir = IngestManager._ingest_dir(agent_id, project_name)
        return os.path.join(os.path.dirname(ingest_dir), _STAGING_DIRNAME)

    # ── public API ───────────────────────────────────────────────────────
    def stage(self, agent_id: str, project_name: str, filename: str,
              data: bytes, *, source_name: str = "", tags: list | None = None,
              chunk_size: int = 1500, chunk_overlap: int = 200,
              user_id: str = "") -> dict:
        """Persist upload bytes + sidecar, reserve the group key, enqueue.

        Returns the immediate HTTP response dict ({status:"queued",
        source_hash:...}) or {"error": ...} for cheap synchronous rejections
        (unsupported extension — mirrors DocumentParser.parse()'s wording so
        the client's reason translation keeps working)."""
        ext = os.path.splitext(filename)[1].lower()
        if ext not in DocumentParser.SUPPORTED_EXTS:
            supported = ", ".join(sorted(DocumentParser.SUPPORTED_EXTS))
            return {"error": f"Unsupported format: {ext}. Supported: {supported}"}
        source = source_name or filename
        ingest_dir = IngestManager._ingest_dir(agent_id, project_name)
        sdir = self._staging_dir(agent_id, project_name)
        os.makedirs(sdir, exist_ok=True)
        with self._lock:
            key = self._reserve_key_locked(agent_id, project_name,
                                           ingest_dir, source)
            staged = os.path.join(sdir, f"{key}{ext}")
            with open(staged, "wb") as f:
                f.write(data)
            meta = {
                "agent_id": agent_id, "project_name": project_name,
                "filename": filename, "source_name": source_name,
                "tags": tags or [], "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap, "user_id": user_id,
                "key": key, "staged": staged,
                "queued_at": time.time(),
            }
            with open(os.path.join(sdir, f"{key}.meta.json"), "w") as f:
                json.dump(meta, f)
            self._jobs.setdefault((agent_id, project_name), {})[key] = {
                "state": "queued", "filename": filename, "source": source,
                "error": "", "chunks": 0, "queued_at": meta["queued_at"],
            }
        self._q.put((agent_id, project_name, key))
        return {"status": "queued", "source": source,
                "source_type": ext.lstrip("."), "source_hash": key,
                "filename": filename, "agent": agent_id,
                "project": project_name}

    def status(self, agent_id: str, project_name: str) -> dict:
        """Snapshot of this project's jobs; prunes stale terminal entries."""
        now = time.time()
        with self._lock:
            jobs = self._jobs.get((agent_id, project_name), {})
            for k in [k for k, j in jobs.items()
                      if j["state"] in ("done", "error", "cancelled")
                      and now - j.get("finished_at", now) > self.DONE_TTL_SECS]:
                del jobs[k]
            snapshot = {k: dict(j) for k, j in jobs.items()}
        pending = sum(1 for j in snapshot.values()
                      if j["state"] in ("queued", "extracting"))
        return {"jobs": snapshot, "pending": pending}

    def has_pending(self, agent_id: str, project_name: str) -> bool:
        """True while any staged job for this project is queued/extracting.
        The project-sync daemon checks this to HOLD OFF mining until the
        upload batch is fully extracted (else it mines a half-set and the
        drain-kick immediately re-syncs)."""
        with self._lock:
            return any(j["state"] in ("queued", "extracting")
                       for j in self._jobs.get((agent_id, project_name),
                                               {}).values())

    def cancel(self, agent_id: str, project_name: str, key: str) -> dict:
        """Per-file terminate. A QUEUED job dies immediately (staged files
        removed; the worker skips the stale queue entry when it pops). An
        EXTRACTING job can't be aborted mid-call (the extraction owns its own
        subprocess/HTTP timeouts), so it's flagged — the worker discards the
        result and deletes the written chunks the moment the call returns."""
        with self._lock:
            job = self._jobs.get((agent_id, project_name), {}).get(key)
            if not job:
                return {"error": "unknown job"}
            state = job["state"]
            if state in ("done", "error", "cancelled"):
                # Terminal → DELETE acts as dismiss (drops the registry row so
                # the file tree stops showing a stale error/cancel entry).
                del self._jobs[(agent_id, project_name)][key]
                return {"status": "dismissed"}
            job["cancel_requested"] = True
            if state == "queued":
                job["state"] = "cancelled"
                job["finished_at"] = time.time()
        if state == "queued":
            sdir = self._staging_dir(agent_id, project_name)
            ext = os.path.splitext(job.get("filename", ""))[1].lower()
            self._cleanup(os.path.join(sdir, f"{key}{ext}"),
                          os.path.join(sdir, f"{key}.meta.json"))
            self._maybe_kick_sync(agent_id, project_name)
            return {"status": "cancelled"}
        return {"status": "cancelling"}

    def start(self):
        """Spawn workers + re-enqueue staged leftovers from a prior run."""
        for i in range(self.WORKERS):
            t = threading.Thread(target=self._work, daemon=True,
                                 name=f"ingest_queue_{i}")
            t.start()
            self._threads.append(t)
        try:
            self._rescan_staging()
        except Exception as e:
            print(f"[ingest-queue] boot rescan failed: {e}", flush=True)

    # ── internals ────────────────────────────────────────────────────────
    def _reserve_key_locked(self, agent_id: str, project_name: str,
                            ingest_dir: str, source: str) -> str:
        """_source_key against disk, then disambiguate against PENDING jobs.

        Two same-stem files staged back to back both see an empty ingested/
        (no chunks written yet), so _source_key alone would hand both the same
        key and the second extraction would overwrite the first. A pending job
        with the SAME source reuses its key (re-upload = overwrite, matching
        _source_key's on-disk semantics). Caller holds self._lock."""
        pend = self._jobs.get((agent_id, project_name), {})
        active = {k: j for k, j in pend.items()
                  if j["state"] in ("queued", "extracting")}
        for k, j in active.items():
            if j.get("source") == source:
                return k
        key = IngestManager._source_key(ingest_dir, source)
        base, n = key, 2
        while key in active:
            key = f"{base}-{n}"
            n += 1
        return key

    def _work(self):
        while True:
            agent_id, project_name, key = self._q.get()
            try:
                self._run_job(agent_id, project_name, key)
            except Exception as e:
                print(f"[ingest-queue] job {key} crashed: {e}", flush=True)
                self._finish(agent_id, project_name, key,
                             error=f"{type(e).__name__}: {e}")
            finally:
                self._q.task_done()

    def _run_job(self, agent_id: str, project_name: str, key: str):
        from engine.context import request_context
        sdir = self._staging_dir(agent_id, project_name)
        meta_path = os.path.join(sdir, f"{key}.meta.json")
        try:
            with open(meta_path, "r") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            # Staged pair gone (double-enqueue of a re-upload already handled
            # by the first pop) — nothing to do.
            return
        staged = meta.get("staged", "")
        with self._lock:
            job = self._jobs.setdefault((agent_id, project_name), {}) \
                            .setdefault(key, {"filename": meta.get("filename", ""),
                                              "source": meta.get("source_name")
                                              or meta.get("filename", "")})
            if job.get("state") == "cancelled":
                # cancelled while queued — staged files already removed
                return
            if not os.path.isfile(staged):
                job["state"] = "error"
                job["error"] = "staged file missing"
                job["finished_at"] = time.time()
                self._cleanup(meta_path)
                return
            job["state"] = "extracting"
        # Pooled thread → context MUST be entered via `with request_context`
        # (reused-thread bleed invariant); current_user_id feeds OCR cost
        # attribution in doc_convert.
        with request_context(current_user_id=meta.get("user_id", "")):
            result = IngestManager.ingest_file(
                agent_id, staged,
                project_name=project_name or None,
                tags=meta.get("tags") or None,
                chunk_size=int(meta.get("chunk_size", 1500)),
                chunk_overlap=int(meta.get("chunk_overlap", 200)),
                source_name=meta.get("source_name") or None,
                key_override=key,
            )
            with self._lock:
                cancelled = bool(self._jobs.get((agent_id, project_name), {})
                                 .get(key, {}).get("cancel_requested"))
            if cancelled:
                # Terminate-per-file: the extraction call itself couldn't be
                # aborted, so discard its output — delete any chunks it wrote.
                if not result.get("error"):
                    try:
                        IngestManager.delete_ingested(
                            agent_id, key, project_name=project_name or None)
                    except Exception:
                        pass
                self._finish(agent_id, project_name, key, state="cancelled")
            elif result.get("error"):
                self._finish(agent_id, project_name, key,
                             error=result["error"])
            else:
                # Same best-effort auto-review the inline path ran, still
                # while the original is on disk.
                try:
                    if meta.get("user_id"):
                        from engine import doc_review as _dr
                        _dr.review_file_to_db(
                            staged, user_id=meta["user_id"],
                            source_kind="project_doc", source_ref=key,
                            filename=meta.get("filename", ""))
                except Exception as _e:
                    print(f"[ingest-queue] auto-review failed for {key}: {_e}",
                          flush=True)
                self._finish(agent_id, project_name, key,
                             chunks=int(result.get("chunks", 0)))
        self._cleanup(staged, meta_path)
        self._maybe_kick_sync(agent_id, project_name)

    def _finish(self, agent_id: str, project_name: str, key: str,
                *, error: str = "", chunks: int = 0, state: str = ""):
        with self._lock:
            job = self._jobs.setdefault((agent_id, project_name), {}) \
                            .setdefault(key, {})
            job["state"] = state or ("error" if error else "done")
            job["error"] = error
            job["chunks"] = chunks
            job["finished_at"] = time.time()

    @staticmethod
    def _cleanup(*paths: str):
        for p in paths:
            try:
                if p and os.path.isfile(p):
                    os.unlink(p)
            except OSError:
                pass

    def _maybe_kick_sync(self, agent_id: str, project_name: str):
        """When a project's staged jobs drain, wake the sync daemon so the
        fresh chunks get mined now instead of on the next scheduled pass."""
        if not project_name:
            return
        with self._lock:
            jobs = self._jobs.get((agent_id, project_name), {})
            if any(j["state"] in ("queued", "extracting")
                   for j in jobs.values()):
                return
        try:
            srv = sys.modules.get("__main__")
            if not hasattr(srv, "_project_sync_request"):
                srv = sys.modules.get("server")
            if srv and hasattr(srv, "_project_sync_request"):
                srv._project_sync_request(agent_id, project_name,
                                          triggered_by="upload")
        except Exception as e:
            print(f"[ingest-queue] sync kick failed: {e}", flush=True)

    def _rescan_staging(self):
        """Re-enqueue staged files that survived a server restart."""
        import brain as _brain
        agents_dir = _brain.AGENTS_DIR
        if not os.path.isdir(agents_dir):
            return
        found = 0
        for agent_id in os.listdir(agents_dir):
            pdir_root = os.path.join(agents_dir, agent_id, "projects")
            if not os.path.isdir(pdir_root):
                continue
            for proj in os.listdir(pdir_root):
                sdir = os.path.join(pdir_root, proj, _STAGING_DIRNAME)
                if not os.path.isdir(sdir):
                    continue
                for fn in os.listdir(sdir):
                    if not fn.endswith(".meta.json"):
                        continue
                    try:
                        with open(os.path.join(sdir, fn), "r") as f:
                            meta = json.load(f)
                    except (OSError, ValueError):
                        continue
                    key = meta.get("key") or fn[:-len(".meta.json")]
                    with self._lock:
                        self._jobs.setdefault((agent_id, proj), {})[key] = {
                            "state": "queued",
                            "filename": meta.get("filename", ""),
                            "source": meta.get("source_name")
                            or meta.get("filename", ""),
                            "error": "", "chunks": 0,
                            "queued_at": meta.get("queued_at", time.time()),
                        }
                    self._q.put((agent_id, proj, key))
                    found += 1
        if found:
            print(f"[ingest-queue] re-enqueued {found} staged upload(s) "
                  f"from a prior run", flush=True)


# Module-level singleton, started by server.py main() next to IngestWatcher.
INGEST_QUEUE = IngestQueue()


class IngestWatcher:
    """Background thread that polls watched folders and auto-ingests new/modified files."""

    POLL_INTERVAL = 30  # seconds

    def __init__(self):
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        """Start the background watcher thread."""
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ingest_watcher")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run_loop(self):
        """Poll all watched folders across all agents and projects."""
        while not self._stop.is_set():
            try:
                self._scan_all()
            except Exception as e:
                logging.debug("IngestWatcher error: %s", e)
            self._stop.wait(self.POLL_INTERVAL)

    def _scan_all(self):
        """Scan watched folders for all agents and projects."""
        import brain as _brain
        if not os.path.isdir(_brain.AGENTS_DIR):
            return
        for agent_name in os.listdir(_brain.AGENTS_DIR):
            if agent_name.startswith("."):
                continue
            agent_dir = os.path.join(_brain.AGENTS_DIR, agent_name)
            if not os.path.isdir(agent_dir):
                continue
            # Check agent-level watches (from agent.json)
            agent_json_path = os.path.join(agent_dir, "agent.json")
            if os.path.exists(agent_json_path):
                try:
                    with open(agent_json_path, "r") as f:
                        agent_cfg = json.load(f)
                    watches = agent_cfg.get("ingest_watch", [])
                    if watches:
                        self._process_watches(agent_name, None, watches, agent_dir)
                except (OSError, json.JSONDecodeError):
                    pass
            # Check project-level watches
            projects_dir = os.path.join(agent_dir, "projects")
            if os.path.isdir(projects_dir):
                for proj_name in os.listdir(projects_dir):
                    proj_dir = os.path.join(projects_dir, proj_name)
                    proj_json = os.path.join(proj_dir, "project.json")
                    if not os.path.exists(proj_json):
                        continue
                    try:
                        with open(proj_json, "r") as f:
                            proj_cfg = json.load(f)
                        watches = proj_cfg.get("watch_folders", [])
                        if watches:
                            self._process_watches(agent_name, proj_name, watches, proj_dir)
                    except (OSError, json.JSONDecodeError):
                        pass

    def _process_watches(self, agent_id: str, project_name: str | None,
                         watches: list[dict], base_dir: str):
        """Process watched folders, detect changes, ingest as needed."""
        registry_path = os.path.join(base_dir, "ingest_registry.json")
        registry = {}
        if os.path.exists(registry_path):
            try:
                with open(registry_path, "r") as f:
                    registry = json.load(f)
            except (OSError, json.JSONDecodeError):
                pass
        watches_reg = registry.get("watches", {})
        changed = False

        for watch in watches:
            watch_path = watch.get("path", "")
            if not watch_path or not os.path.isdir(watch_path):
                continue
            pattern = watch.get("pattern", "*")
            recursive = watch.get("recursive", False)
            tags = watch.get("tags", [])
            chunk_size = watch.get("chunk_size", 1500)

            # Get or create registry entry for this watch
            wreg = watches_reg.get(watch_path, {"files": {}, "last_scan": ""})

            # Scan for matching files
            if recursive:
                matched_files = []
                for root, _dirs, files in os.walk(watch_path):
                    for fn in files:
                        if fnmatch.fnmatch(fn, pattern):
                            matched_files.append(os.path.join(root, fn))
            else:
                matched_files = [
                    os.path.join(watch_path, fn) for fn in os.listdir(watch_path)
                    if fnmatch.fnmatch(fn, pattern) and os.path.isfile(os.path.join(watch_path, fn))
                ]

            current_files = set()
            for fpath in matched_files:
                fname = os.path.basename(fpath)
                current_files.add(fname)
                try:
                    stat = os.stat(fpath)
                except OSError:
                    continue
                prev = wreg["files"].get(fname, {})
                if prev.get("mtime") == stat.st_mtime and prev.get("size") == stat.st_size:
                    continue  # unchanged

                # New or modified file — ingest
                try:
                    result = IngestManager.ingest_file(
                        agent_id, fpath, project_name=project_name,
                        tags=tags, chunk_size=chunk_size,
                    )
                    if "error" not in result:
                        wreg["files"][fname] = {
                            "mtime": stat.st_mtime,
                            "size": stat.st_size,
                            "hash": result.get("source_hash", ""),
                            "chunks": result.get("chunks", 0),
                            "ingested_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        }
                        changed = True
                except Exception as e:
                    logging.debug("IngestWatcher: failed to ingest %s: %s", fpath, e)

            # Detect deleted files
            for fname in list(wreg["files"].keys()):
                if fname not in current_files:
                    src_hash = wreg["files"][fname].get("hash", "")
                    if src_hash:
                        IngestManager.delete_ingested(agent_id, src_hash, project_name)
                    del wreg["files"][fname]
                    changed = True

            wreg["last_scan"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            watches_reg[watch_path] = wreg

        if changed:
            registry["watches"] = watches_reg
            try:
                with open(registry_path, "w") as f:
                    json.dump(registry, f, indent=2)
            except OSError:
                pass
