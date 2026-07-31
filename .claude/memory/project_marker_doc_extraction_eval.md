---
name: project_marker_doc_extraction_eval
description: "datalab-to/marker as a markitdown alternative for doc→markdown — evaluated 2026-06-16, deferred (license + GPU), matrix has a marker slot reserved"
metadata: 
  node_type: memory
  type: project
  originSessionId: 99a1fa24-8a53-401a-80cc-9bc845d6506f
---

2026-06-16: evaluated **datalab-to/marker** (github) as a stronger doc→markdown
backend than markitdown, prompted by the WPB Konzernbilanz extraction issue.

**marker strengths:** real layout detection + table extraction + reading order +
header/footer removal + LaTeX/equations (markitdown's weak spot). PDF/DOCX/PPTX/
XLSX/HTML/EPUB → markdown/json/html/chunks. Runs fully local (MPS/Metal works).
Fast + accurate in their benchmarks (2.84s/doc, heuristic 95.67 vs Llamaparse 84).
Optional --use_llm to boost accuracy (Gemini/Ollama/Claude/etc).

**marker blockers (why DEFERRED, not adopted):**
1. **License:** code GPL-3.0, MODELS "Modified OpenRAIL-M" — free only for
   research/personal/startups <$2M funding; **commercial use needs a separate
   license**. Show-stopper for the bank deployment until cleared.
2. **Heavy ML dep:** PyTorch + model weights + 3-5 GB VRAM/worker. Competes with
   oMLX/vllm-metal/embeddings for Mac unified memory. Fine on DGX Spark later,
   resource conflict on the Mac now.
3. Needs its own subprocess/service (like sidecar/crawl4ai).

**Decision:** NOT built now. The WPB problem was NOT extraction quality — the eval
showed **markitdown already renders the balance-sheet table correctly**; the real
bug was a 50K-char truncation in the web-url miner (web_fetch max_length default,
fixed v9.140.x by calling the miner with max_length=10_000_000). So there's no
acute quality gap forcing marker.

**Architecture left for it:** the new conversion matrix (config.json →
conversion.markitdown_exts, editable in General Settings → Service-Modelle, the
read_document/OCR area) chooses extractor per file type. marker would slot in as a
THIRD backend option per type (markitdown | own _extract_* | marker). Revisit when:
(a) commercial license cleared, (b) on DGX Spark (GPU headroom), or (c) a real
table-quality failure that markitdown+own-code can't handle. See
[[project_dgx_spark_warmup_plan]].

Current extractor split (engine/doc_convert.py): markitdown-first for
.pdf/.docx/.pptx/.msg/.epub/.zip; own code for .xlsx/.xls/.csv/.tsv/.eml. markitdown
is a good TEXT converter, weak on TABLES/STRUCTURE — that's why xlsx (v9.137) + eml
were already pulled off it.
