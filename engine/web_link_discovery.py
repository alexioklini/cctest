# Project web-URL link discovery (Option B — propose, don't auto-import).
#
# For each HTML page configured in a project's `web_urls`, find the links that
# point to DOCUMENT files (PDF/DOCX/XLSX/PPTX/CSV/…) and return them as
# PROPOSED sources the user approves in the UI — they are NOT added to the
# project automatically. This is the bounded, controlled-corpus counterpart to
# recursive crawling, which this codebase deliberately does NOT do (see the
# v9.73.1 closed-corpus decision): we surface depth-1 documents that a page
# explicitly links, scoped to the SAME host, and let the user pick.
#
# Deliberately NOT here: following HTML→HTML links (no recursion), off-host
# links, or anything that imports without an explicit approval step.

from __future__ import annotations

import urllib.parse
import urllib.request

# Document extensions we propose. Mirrors doc_convert.SUPPORTED_EXTS minus the
# archive/email types (.zip/.epub/.msg/.eml) — those aren't what a "linked
# document" on a publications page means, and proposing them would be noise.
_DOC_EXTS = {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".csv", ".tsv", ".doc"}

# How many configured pages we scan per discovery call, and the per-page link
# cap — keeps a sprawling index page from proposing thousands of links.
_MAX_PAGES = 12
_MAX_LINKS_PER_PAGE = 200


def _same_host(a: str, b: str) -> bool:
    try:
        ha = (urllib.parse.urlparse(a).hostname or "").lower().lstrip("www.")
        hb = (urllib.parse.urlparse(b).hostname or "").lower().lstrip("www.")
        return bool(ha) and ha == hb
    except ValueError:
        return False


def _doc_ext(url: str) -> str | None:
    """Return the document extension a URL points at, or None. Checks the path
    only (query strings are ignored)."""
    import os
    try:
        path = urllib.parse.urlparse(url).path
    except ValueError:
        return None
    ext = os.path.splitext(path)[1].lower()
    return ext if ext in _DOC_EXTS else None


def _fetch_html(url: str, timeout: int) -> tuple[str, str]:
    """Fetch a URL and return (html_text, final_url). Returns ('', url) on any
    error or when the response isn't HTML (we only parse links out of HTML)."""
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        req = urllib.request.Request(url, headers=req_headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get_content_type() or "").lower()
            if "html" not in ctype:
                return "", (resp.url if hasattr(resp, "url") else url)
            raw = resp.read(10 * 1024 * 1024)
            encoding = resp.headers.get("Content-Encoding", "")
            if encoding == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            charset = resp.headers.get_content_charset() or "utf-8"
            final_url = resp.url if hasattr(resp, "url") else url
            return raw.decode(charset, errors="replace"), final_url
    except Exception:
        return "", url


def _links_from_html(html: str, base_url: str) -> list[tuple[str, str]]:
    """Extract (absolute_url, link_text) pairs for SAME-HOST document links in
    one HTML page. Relative hrefs are resolved against base_url."""
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return []
    out = []
    seen = set()
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        abs_url = urllib.parse.urljoin(base_url, href)
        if not abs_url.lower().startswith(("http://", "https://")):
            continue
        if not _doc_ext(abs_url) or not _same_host(abs_url, base_url):
            continue
        key = abs_url.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        text = a.get_text(" ", strip=True) or ""
        out.append((key, text[:160]))
        if len(out) >= _MAX_LINKS_PER_PAGE:
            break
    return out


def discover_document_links(web_urls: list, timeout: int = 30) -> dict:
    """Scan the project's configured HTML pages for same-host document links.

    web_urls: the project.json web_urls list ([{url,title}, ...]).
    Returns {"proposed": [{url, title, ext, found_on, in_project}], "scanned": N,
    "pages": [{url, links}]}. `in_project` marks a link already configured as a
    web_url (so the UI can dim it). Pure discovery — nothing is imported."""
    from engine import deep_research as _dr

    existing = {_dr._norm_url(u.get("url", ""))
                for u in (web_urls or []) if isinstance(u, dict)}
    # Only scan pages that are themselves HTML (not the file URLs already in the
    # set — discovering links out of a PDF makes no sense).
    pages = [(u.get("url") or "").strip()
             for u in (web_urls or [])
             if isinstance(u, dict) and (u.get("url") or "").strip()
             and not _doc_ext((u.get("url") or "").strip())]
    pages = pages[:_MAX_PAGES]

    proposed = []
    seen_norm = set()
    page_reports = []
    for page_url in pages:
        html, final_url = _fetch_html(page_url, timeout)
        if not html:
            page_reports.append({"url": page_url, "links": 0})
            continue
        links = _links_from_html(html, final_url)
        page_reports.append({"url": page_url, "links": len(links)})
        for link_url, text in links:
            norm = _dr._norm_url(link_url)
            if norm in seen_norm:
                continue
            seen_norm.add(norm)
            import os
            ext = _doc_ext(link_url) or ""
            # Title: link text if meaningful, else the file name.
            title = text if len(text) >= 4 else ""
            if not title:
                title = os.path.basename(urllib.parse.urlparse(link_url).path)
            proposed.append({
                "url": link_url,
                "title": title,
                "ext": ext,
                "found_on": page_url,
                "in_project": norm in existing,
            })
    return {"proposed": proposed, "scanned": len(pages), "pages": page_reports}
