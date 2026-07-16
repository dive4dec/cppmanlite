"""Core search and display logic for cppmanlite.

No external dependencies — pure stdlib.  Fetches pages on-demand from
cppreference.com when not bundled locally.

Works in CPython, Jupyter, and Pyodide (browser).  In Pyodide, network
fetches use the browser's Fetch API via pyodide.http instead of urllib.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Environment detection
# --------------------------------------------------------------------------- #

def _detect_pyodide() -> bool:
    """Return True if running under Pyodide."""
    try:
        import sys
        return "pyodide" in sys.modules or "pyodide" in getattr(sys, "platform", "")
    except Exception:
        return False


_IS_PYODIDE = _detect_pyodide()


# --------------------------------------------------------------------------- #
# Network fetch — urllib in CPython, pyodide.http in Pyodide
# --------------------------------------------------------------------------- #

def _fetch_url(url: str, timeout: int = 15) -> str:
    """Fetch a URL and return text.  Uses urllib (CPython) or pyfetch (Pyodide)."""
    if _IS_PYODIDE:
        return _fetch_pyodide(url)
    return _fetch_urllib(url, timeout)


def _fetch_urllib(url: str, timeout: int) -> str:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "cppmanlite/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _fetch_pyodide(url: str) -> str:
    """Fetch via pyodide.http.pyfetch (async under the hood, but Pyodide
    auto-awaits top-level coroutines)."""
    from pyodide.http import pyfetch
    resp = pyfetch(url, headers={"User-Agent": "cppmanlite/0.1"})
    return resp.string


# --------------------------------------------------------------------------- #
# Index management
# --------------------------------------------------------------------------- #

_INDEX: list[dict[str, str]] = []
_INDEX_PATH = Path(__file__).parent / "data" / "index.json"

# When running in Pyodide the bundled index.json ships inside the wheel;
# when running in CPython without the bundle, fetch from GitHub Pages.
_INDEX_FALLBACK_URL = "https://dive4dec.github.io/cppmanlite/index.json"

# cppreference page base URL (redirects /w/cpp/... → /cpp/...)
_PAGE_BASE = "https://en.cppreference.com/w"


def _load_index() -> list[dict[str, str]]:
    """Load the search index, fetching it if necessary."""
    global _INDEX
    if _INDEX:
        return _INDEX
    if _INDEX_PATH.exists():
        with open(_INDEX_PATH, encoding="utf-8") as f:
            _INDEX = json.load(f)
    else:
        # Fetch from GitHub Pages (works in both CPython and Pyodide)
        try:
            _INDEX = json.loads(_fetch_url(_INDEX_FALLBACK_URL))
        except Exception:
            _INDEX = []
    return _INDEX


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search(query: str, limit: int = 20) -> list[dict[str, str]]:
    """Search C++ documentation pages.

    Args:
        query: Search term (e.g. "vector", "std::sort", "shared_ptr").
        limit: Maximum number of results.

    Returns:
        List of dicts with keys: title, url, snippet.
    """
    idx = _load_index()
    if not idx:
        return []
    q = query.lower().strip()
    # Normalise std:: prefix
    q_norm = re.sub(r"^std::", "", q)
    results = []
    for entry in idx:
        title = entry.get("title", "").lower()
        url = entry.get("url", "").lower()
        # Score: exact match > starts with > contains in title > contains in URL
        score = 0
        if title == q or title == q_norm:
            score = 100
        elif title.startswith(q) or title.startswith(q_norm):
            score = 80
        elif q in title or q_norm in title:
            score = 60
        elif q in url or q_norm in url:
            score = 40
        if score > 0:
            results.append({**entry, "_score": score})
    results.sort(key=lambda x: (-x["_score"], x.get("title", "")))
    return [{k: v for k, v in r.items() if k != "_score"} for r in results[:limit]]


def list_pages(limit: int = 0) -> list[dict[str, str]]:
    """List all indexed pages (for debugging/browsing)."""
    idx = _load_index()
    return idx if limit == 0 else idx[:limit]


# ---------------------------------------------------------------------------
# Page fetching and rendering
# ---------------------------------------------------------------------------

_CONTENT_RE = re.compile(
    r'<div id="mw-content-text"[^>]*>(.*?)(?:</div>\s*<!--|\Z)',
    re.DOTALL,
)
_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL)
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_EDIT_RE = re.compile(r'<span class="mw-editsection">.*?</span>', re.DOTALL)


_NAVBAR_RE = re.compile(
    r'<div class="t-navbar"[^>]*>.*?(?:</div>\s*(?=<div|<h[1-6]|<table|<p|\Z))',
    re.DOTALL,
)
_NV_TABLE_RE = re.compile(r'<table class="t-nv-begin"[^>]*>.*?</table>', re.DOTALL)


def _fetch_page(url: str) -> str:
    """Fetch a cppreference page and extract the main content HTML."""
    full_url = f"{_PAGE_BASE}/{url}" if not url.startswith("http") else url
    html_raw = _fetch_url(full_url)
    # Extract #mw-content-text
    m = _CONTENT_RE.search(html_raw)
    if not m:
        return "<p>Could not extract page content.</p>"
    content = m.group(1)
    # Clean up
    content = _SCRIPT_RE.sub("", content)
    content = _STYLE_RE.sub("", content)
    content = _COMMENT_RE.sub("", content)
    content = _EDIT_RE.sub("", content)
    # Strip residual [edit] markers left by mw-editsection removal
    content = re.sub(r"\[edit\]", "", content)
    # Strip cppreference navigation chrome (t-navbar, t-nv-begin tables)
    content = _NAVBAR_RE.sub("", content)
    content = _NV_TABLE_RE.sub("", content)
    # Fix relative URLs
    content = re.sub(r'href="/w/', 'href="https://en.cppreference.com/w/', content)
    content = re.sub(r'src="/', 'src="https://en.cppreference.com/', content)
    return content


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def _is_jupyter() -> bool:
    try:
        from IPython.display import HTML, display  # noqa: F401

        get_ipython  # type: ignore[name-defined]
        return True
    except Exception:
        return False


def _format_search_html(results: list[dict[str, str]]) -> str:
    rows = []
    for r in results:
        title = html.escape(r.get("title", ""))
        url = html.escape(r.get("url", ""))
        snippet = html.escape(r.get("snippet", ""))[:120]
        rows.append(
            f'<tr><td><a href="https://en.cppreference.com/w/{url}" '
            f'target="_blank">{title}</a></td>'
            f'<td><code>{snippet}</code></td></tr>'
        )
    return (
        '<table style="font-size:14px;border-collapse:collapse">'
        "<tr><th>Title</th><th>Path</th></tr>"
        + "\n".join(rows)
        + "</table>"
    )


def _format_page_html(content: str) -> str:
    return (
        '<div style="max-height:600px;overflow:auto;'
        'border:1px solid #ddd;padding:16px;font-size:14px">'
        + content
        + "</div>"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def man(query: str) -> Any:
    """Display a C++ documentation page (like ``man`` for C++).

    In Jupyter: renders HTML inline.
    In terminal: prints plain text.

    Args:
        query: Page title or URL path (e.g. "std::vector" or "cpp/container/vector").
    """
    results = search(query, limit=1)
    if not results:
        msg = f"No documentation found for '{query}'."
        if _is_jupyter():
            from IPython.display import HTML, display

            display(HTML(f"<p>{html.escape(msg)}</p>"))
        print(msg)
        return
    url = results[0]["url"]
    content = _fetch_page(url)
    if _is_jupyter():
        from IPython.display import HTML, display

        display(HTML(_format_page_html(content)))
    else:
        # Strip HTML tags for terminal, then decode entities
        text = re.sub(r"<[^>]+>", "", content)
        text = html.unescape(text)
        text = re.sub(r"\[edit\]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        print(text[:4000])


def help(query: str) -> Any:
    """Search C++ documentation (alias for :func:`search`).

    Args:
        query: Search term.
    """
    return search(query)


def refresh_index() -> int:
    """Re-download the search index from GitHub Pages.

    Returns the number of indexed pages.
    """
    global _INDEX
    _INDEX = []
    url = "https://dive4dec.github.io/cppmanlite/index.json"
    _INDEX = json.loads(_fetch_url(url))
    return len(_INDEX)


# Re-export ``help`` under a safe alias to avoid shadowing builtin
help_query = help
