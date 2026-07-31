---
name: project_pdf_subprocess_timeout_fix
description: "Fix — pymupdf4llm now runs in a hard-killable subprocess so a pathological PDF can't peg a CPU core forever and freeze a chat turn (root cause of the 9.156.x \"server down\")"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5d571558-3f48-4dab-a29a-10b41e032c14
---

v9.157.3 (2026-06-17): root-caused + fixed the "brain server down" incident.

**Symptom:** server stayed HTTP-reachable for light GETs but a project chat turn never completed; one core pegged at 100%. NOT a crash, NOT the reranker (first theory, wrong), NOT the feedback query (sampling red herring), NOT the 9.157.1 OCR change.

**Root cause:** a web-fetched PDF hung `pymupdf4llm.to_markdown` for the full 60s `_PDF_EXTRACT_TIMEOUT_SECS`. pymupdf4llm layout analysis is CPU-bound and UNINTERRUPTIBLE from Python — the thread-based `_run_with_timeout` (engine/doc_convert.py) can only *abandon* the daemon thread (Python can't kill threads), so it kept grinding at 100% on a core indefinitely, starving the turn. Log signature: `[doc-convert] pymupdf4llm timed out after 60s on …`.

**Fix:** new `_pymupdf4llm_subprocess(path, idxs, timeout)` runs pymupdf4llm in a child process (`sys.executable -c` worker, job via stdin JSON, markdown via temp file); `subprocess.run(timeout=)` SIGKILLs the child on timeout → CPU reclaimed (proven: busy-loop child killed at 2s, no zombie). `_do_extract` calls `_extract_pdf_pymupdf4llm` directly (subprocess is the single timeout authority; raises `_ExtractTimeout` → fitz fallback). Simplified to ONE whole-doc subprocess call for all sizes (~1.8s/18pp vs ~8.6s for the old per-page-subprocess loop). fitz/pdfplumber legacy path (pdf_engine != pymupdf4llm) keeps its thread timeout.

**Lesson:** a daemon-thread timeout does NOT bound CPU — only a subprocess (killable) does. Any in-process CPU-bound C call (pymupdf4llm, pdfplumber table detection, …) on the request path is a latent wedge. The fitz/pdfplumber legacy path is the remaining (lower-risk) thread-timeout site.

Was wrong twice before landing this (reranker, feedback query) — reproduce from logs before asserting root cause. Related: [[project_pdf_extraction_backends_eval]] (pymupdf4llm shipped as default), [[feedback_never_sigkill_brain]] (restart via SIGTERM).
