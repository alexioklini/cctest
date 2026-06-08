"""Embed / extract a GDPR-review payload in a document so the review travels
WITH the file.

When a reviewed (and optionally anonymised) document is downloaded or written
back to disk, we attach a small JSON payload describing the review:

    {
      "v": 1,
      "review_id": str,
      "content_hash": str,           # hash of the ORIGINAL extracted text
      "status": "reviewed"|"anonymised",
      "anonymised": bool,
      "overrules": [{id, kind, label, explanation, by, at}],
      "anon_map": {nonce_b64, ct_b64, mapping_id} | null,  # encrypted de-anon index
    }

The encrypted `anon_map` is the de-anonymisation index embedded INLINE so the
file is self-contained — it can be de-anonymised after re-upload even on a
fresh server (decryption needs only `agents/main/pseudonym.key`, the same key
the rest of the GDPR pipeline uses).

Transport per format:
  - .docx/.pptx/.xlsx → OOXML custom document property `BrainGdprReview`
  - .pdf              → PDF metadata key `/BrainGdprReview` (PyMuPDF)
  - everything else   → sidecar file `<name>.brain-meta.json`

On re-analysis the reviewer calls `extract(path)` first; a hit means "already
reviewed" → reuse overrules + the de-anon index instead of re-scanning blind.
"""

from __future__ import annotations

import base64
import json
import os
import zipfile
from xml.etree import ElementTree as ET

# OOXML custom-properties part.
_CUSTOM_PROPS_PART = "docProps/custom.xml"
_CT_OVERRIDE = ('<Override PartName="/docProps/custom.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.'
                'custom-properties+xml"/>')
_CP_NS = ("http://schemas.openxmlformats.org/officeDocument/2006/"
          "custom-properties")
_VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
_PROP_NAME = "BrainGdprReview"
_PDF_KEY = "BrainGdprReview"
_SIDECAR_SUFFIX = ".brain-meta.json"

_OOXML_EXTS = frozenset({".docx", ".pptx", ".xlsx"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def embed(path: str, payload: dict) -> None:
    """Attach `payload` to the file at `path`, in place. Best-format transport;
    always falls back to a sidecar .json so the round-trip never silently
    drops. Raises only on a hard I/O failure."""
    ext = os.path.splitext(path)[1].lower()
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    try:
        if ext in _OOXML_EXTS:
            _embed_ooxml(path, blob)
            return
        if ext == ".pdf":
            _embed_pdf(path, blob)
            return
    except Exception as e:
        print(f"[review_metadata] embed({ext}) failed, using sidecar: {e}",
              flush=True)
    _embed_sidecar(path, blob)


def extract(path: str) -> dict | None:
    """Return the review payload attached to `path`, or None. Checks the native
    transport first, then the sidecar. Never raises."""
    ext = os.path.splitext(path)[1].lower()
    blob = None
    try:
        if ext in _OOXML_EXTS:
            blob = _extract_ooxml(path)
        elif ext == ".pdf":
            blob = _extract_pdf(path)
    except Exception:
        blob = None
    if blob is None:
        blob = _extract_sidecar(path)
    if not blob:
        return None
    try:
        return json.loads(blob)
    except Exception:
        return None


def encode_mapping(mapping) -> dict | None:
    """Encrypt a pseudonymizer Mapping for inline embedding. Returns
    `{mapping_id, nonce_b64, ct_b64}` or None on failure."""
    if mapping is None:
        return None
    try:
        import pseudonymizer as _ps
        nonce, ct = _ps.encrypt_mapping(mapping)
        return {
            "mapping_id": mapping.mapping_id,
            "nonce_b64": base64.b64encode(nonce).decode("ascii"),
            "ct_b64": base64.b64encode(ct).decode("ascii"),
        }
    except Exception as e:
        print(f"[review_metadata] encode_mapping failed: {e}", flush=True)
        return None


def decode_mapping(anon_map: dict):
    """Decrypt an inline-embedded de-anon index → a pseudonymizer Mapping, or
    None. Inverse of `encode_mapping`."""
    if not anon_map:
        return None
    try:
        import pseudonymizer as _ps
        nonce = base64.b64decode(anon_map["nonce_b64"])
        ct = base64.b64decode(anon_map["ct_b64"])
        return _ps.decrypt_mapping(anon_map["mapping_id"], nonce, ct)
    except Exception as e:
        print(f"[review_metadata] decode_mapping failed: {e}", flush=True)
        return None


# ---------------------------------------------------------------------------
# Sidecar
# ---------------------------------------------------------------------------


def _sidecar_path(path: str) -> str:
    return path + _SIDECAR_SUFFIX


def _embed_sidecar(path: str, blob: str) -> None:
    with open(_sidecar_path(path), "w", encoding="utf-8") as f:
        f.write(blob)


def _extract_sidecar(path: str) -> str | None:
    sp = _sidecar_path(path)
    if not os.path.isfile(sp):
        return None
    try:
        with open(sp, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# OOXML custom properties
# ---------------------------------------------------------------------------


def _custom_props_xml(value: str) -> bytes:
    """Build a minimal docProps/custom.xml carrying our single property."""
    esc = (value.replace("&", "&amp;").replace("<", "&lt;")
           .replace(">", "&gt;"))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Properties xmlns="{_CP_NS}" xmlns:vt="{_VT_NS}">'
        '<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" '
        f'pid="2" name="{_PROP_NAME}"><vt:lpwstr>{esc}</vt:lpwstr>'
        '</property></Properties>'
    ).encode("utf-8")


def _embed_ooxml(path: str, blob: str) -> None:
    """Rewrite the OOXML zip with our custom property added/replaced. Preserves
    every other member; injects the custom.xml part + its content-type override
    + a relationship if missing."""
    import shutil
    import tempfile

    new_custom = _custom_props_xml(blob)
    tmp_fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(path)[1])
    os.close(tmp_fd)
    try:
        with zipfile.ZipFile(path, "r") as zin, \
                zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            names = set(zin.namelist())
            for name in zin.namelist():
                if name == _CUSTOM_PROPS_PART:
                    continue  # replaced below
                data = zin.read(name)
                if name == "[Content_Types].xml":
                    data = _ensure_content_type(data)
                elif name == "_rels/.rels":
                    data = _ensure_root_rel(data, names)
                zout.writestr(name, data)
            zout.writestr(_CUSTOM_PROPS_PART, new_custom)
        shutil.move(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _ensure_content_type(data: bytes) -> bytes:
    txt = data.decode("utf-8", "replace")
    if "docProps/custom.xml" in txt:
        return data
    return txt.replace("</Types>", _CT_OVERRIDE + "</Types>").encode("utf-8")


def _ensure_root_rel(data: bytes, names: set) -> bytes:
    txt = data.decode("utf-8", "replace")
    if "docProps/custom.xml" in txt:
        return data
    rel = ('<Relationship Id="rIdBrainGdpr" '
           'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
           'relationships/custom-properties" Target="docProps/custom.xml"/>')
    return txt.replace("</Relationships>", rel + "</Relationships>").encode("utf-8")


def _extract_ooxml(path: str) -> str | None:
    try:
        with zipfile.ZipFile(path, "r") as z:
            if _CUSTOM_PROPS_PART not in z.namelist():
                return None
            data = z.read(_CUSTOM_PROPS_PART)
    except Exception:
        return None
    try:
        root = ET.fromstring(data)
    except Exception:
        return None
    for prop in root.iter("{%s}property" % _CP_NS):
        if prop.get("name") == _PROP_NAME:
            for child in prop:
                if (child.text or "").strip():
                    return child.text
    return None


# ---------------------------------------------------------------------------
# PDF metadata
# ---------------------------------------------------------------------------


def _embed_pdf(path: str, blob: str) -> None:
    # PyMuPDF's set_metadata rejects non-standard Info keys, so carry the
    # payload as an embedded-file stream named after our key instead — a
    # native, self-contained PDF feature that survives copies.
    import fitz  # PyMuPDF
    doc = fitz.open(path)
    try:
        data = blob.encode("utf-8")
        # Replace any prior copy so re-review doesn't accumulate duplicates.
        try:
            for i in range(doc.embfile_count()):
                if doc.embfile_info(i).get("name") == _PDF_KEY:
                    doc.embfile_del(_PDF_KEY)
                    break
        except Exception:
            pass
        doc.embfile_add(_PDF_KEY, data,
                        filename=_PDF_KEY + ".json",
                        desc="Brain GDPR review metadata")
        # Embedded files require a full (non-incremental) save, which PyMuPDF
        # refuses to write back to the open file — save to a temp then move.
        import shutil
        import tempfile
        tmp_fd, tmp = tempfile.mkstemp(suffix=".pdf")
        os.close(tmp_fd)
        doc.save(tmp, garbage=3, deflate=True)
        doc.close()
        shutil.move(tmp, path)
        return
    finally:
        if not doc.is_closed:
            doc.close()


def _extract_pdf(path: str) -> str | None:
    import fitz
    doc = fitz.open(path)
    try:
        try:
            data = doc.embfile_get(_PDF_KEY)
        except Exception:
            return None
        if not data:
            return None
        return data.decode("utf-8", "replace")
    finally:
        doc.close()


__all__ = ["embed", "extract", "encode_mapping", "decode_mapping"]
