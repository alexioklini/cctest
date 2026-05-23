"""engine/ingest.py — document/ingest pipeline (refactor E3).

Extracted from brain.py. Owns the four cohesive classes that parse documents
to text, chunk them, persist them as `ingest-*.md` chunk files, and auto-ingest
watched folders:

  - `DocumentParser`  — format-detect → plain-text/markdown. The office/tabular
    parsers (`parse_docx/xlsx/pptx`) are thin shims over the unified
    `engine.doc_convert._do_extract` pipeline; the rest (pdf/txt/md/html/csv/
    image/svg/url) are self-contained stdlib/Pillow/fitz parsers.
  - `DocumentChunker` — paragraph/sentence/word chunking with section-header
    preservation + overlap. Pure (re + stdlib only).
  - `IngestManager`   — parse → chunk → write `ingest-<hash>-NNN.md` with
    frontmatter under `agents/<agent>/[projects/<proj>/]ingested/`; list/delete.
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
import re
import threading
import urllib.request


# ─── Document Ingestion Engine ────────────────────────────────────────

class DocumentParser:
    """Parse various document formats to plain text."""

    @staticmethod
    def parse_pdf(path: str) -> str:
        """Parse PDF to text using pymupdf."""
        try:
            import fitz  # pymupdf
        except ImportError:
            raise ImportError("Install pymupdf for PDF support: pip3 install pymupdf")
        doc = fitz.open(path)
        pages = []
        for page in doc:
            pages.append(page.get_text())
        doc.close()
        return "\n\n".join(pages)

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
        """6-char hash of source name/URL."""
        return hashlib.sha256(source.encode()).hexdigest()[:6]

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
                    chunk_size: int = 1500, chunk_overlap: int = 200) -> dict:
        """Parse, chunk, and store a file as ingested memory chunks."""
        if not os.path.exists(file_path):
            return {"error": f"File not found: {file_path}"}
        source_name = os.path.basename(file_path)
        try:
            text, source_type = DocumentParser.parse(file_path)
        except (ImportError, ValueError) as e:
            return {"error": str(e)}
        return IngestManager._store_chunks(
            agent_id, project_name, source_name, source_type, text,
            tags=tags, chunk_size=chunk_size, chunk_overlap=chunk_overlap,
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
                      chunk_size: int = 1500, chunk_overlap: int = 200) -> dict:
        """Chunk text and write as ingest-*.md files with frontmatter."""
        import brain as _brain
        src_hash = IngestManager._source_hash(source)
        ingest_dir = IngestManager._ingest_dir(agent_id, project_name)
        collection = IngestManager._collection_name(agent_id, project_name)

        # Delete existing chunks for this source (re-ingest)
        existing = [f for f in os.listdir(ingest_dir) if f.startswith(f"ingest-{src_hash}-") and f.endswith(".md")]
        for f in existing:
            os.remove(os.path.join(ingest_dir, f))

        # Chunk
        chunks = DocumentChunker.chunk(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
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
                prev_file = f"ingest-{src_hash}-{idx - 1:03d}.md"
                related_lines.append(f"  - file: {prev_file}\n    type: prev_chunk")
            if idx < total - 1:
                next_file = f"ingest-{src_hash}-{idx + 1:03d}.md"
                related_lines.append(f"  - file: {next_file}\n    type: next_chunk")
            if idx != 0:
                first_file = f"ingest-{src_hash}-000.md"
                related_lines.append(f"  - file: {first_file}\n    type: same_source")
            related_yaml = ""
            if related_lines:
                related_yaml = "related:\n" + "\n".join(related_lines) + "\n"

            filename = f"ingest-{src_hash}-{idx:03d}.md"
            md_content = f"""---
title: {_brain._yaml_escape(title)}
source: {_brain._yaml_escape(source)}
source_type: {source_type}
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

        # Trigger QMD indexing
        _brain._qmd_debounced_embed(collection)

        word_count = len(text.split())
        return {
            "source": source,
            "source_type": source_type,
            "source_hash": src_hash,
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
        # Group by source hash
        groups: dict[str, dict] = {}
        for fname in os.listdir(ingest_dir):
            if not fname.startswith("ingest-") or not fname.endswith(".md"):
                continue
            fpath = os.path.join(ingest_dir, fname)
            try:
                with open(fpath, "r") as f:
                    raw = f.read(800)
                fm, _ = _brain._parse_frontmatter(raw)
            except Exception:
                continue
            source = fm.get("source", "unknown")
            src_hash = fname.split("-")[1] if "-" in fname else "?"
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
        import brain as _brain
        ingest_dir = IngestManager._ingest_dir(agent_id, project_name)
        if not os.path.isdir(ingest_dir):
            return {"error": "No ingested documents found"}
        deleted = 0
        source_name = ""
        for fname in os.listdir(ingest_dir):
            if fname.startswith(f"ingest-{source_hash}-") and fname.endswith(".md"):
                if not source_name:
                    fpath = os.path.join(ingest_dir, fname)
                    try:
                        with open(fpath, "r") as f:
                            fm, _ = _brain._parse_frontmatter(f.read(500))
                        source_name = fm.get("source", "unknown")
                    except Exception:
                        pass
                os.remove(os.path.join(ingest_dir, fname))
                deleted += 1
        collection = IngestManager._collection_name(agent_id, project_name)
        _brain._qmd_debounced_embed(collection)
        return {"source": source_name, "source_hash": source_hash, "deleted": deleted}


# ─── Watched Folders (Auto-Ingestion) ────────────────────────────────

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
