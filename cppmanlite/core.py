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
import textwrap
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Environment detection
# --------------------------------------------------------------------------- #

def _detect_pyodide() -> bool:
    """Return True if running under Pyodide."""
    try:
        import sys
        return "pyodide" in sys.modules or "emscripten" in getattr(sys, "platform", "")
    except Exception:
        return False


_IS_PYODIDE = _detect_pyodide()


# --------------------------------------------------------------------------- #
# Network fetch — urllib in CPython, pyodide.http in Pyodide
# --------------------------------------------------------------------------- #

def _fetch_urllib(url: str, timeout: int) -> str:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "cppmanlite/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


async def _fetch_pyodide(url: str) -> str:
    """Fetch via pyodide.http.pyfetch (async)."""
    from pyodide.http import pyfetch
    resp = await pyfetch(url, headers={"User-Agent": "cppmanlite/0.1"})
    return await resp.string()


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

# GitHub Pages mirror — used as fallback in Pyodide (browser CORS blocks
# direct fetches to en.cppreference.com which doesn't send CORS headers).
_PAGES_MIRROR = "https://dive4dec.github.io/cppmanlite"


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
            _INDEX = json.loads(_fetch_url_sync(_INDEX_FALLBACK_URL))
        except Exception:
            _INDEX = []
    return _INDEX


def _fetch_url_sync(url: str) -> str:
    """Synchronous fetch for index loading — blocks in CPython, raises in Pyodide.

    In Pyodide, index should be bundled in the wheel so this is never called.
    If it is, we try asyncio.run as a fallback.
    """
    if _IS_PYODIDE:
        import asyncio
        return asyncio.run(_fetch_pyodide(url))
    return _fetch_urllib(url, 15)


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Page fetching and rendering
# --------------------------------------------------------------------------- #

_CONTENT_RE = re.compile(
    r'<div class="mw-content-ltr mw-parser-output"[^>]*>(.*?)(?:</div>\s*<!--|\Z)',
    re.DOTALL,
)
_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL)
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_EDIT_RE = re.compile(r'<span class="(?:mw-)?editsection[^"]*">.*?</span>', re.DOTALL)

# Strip cppreference navigation chrome (t-navbar has nested divs — match
# the outermost by greedy-matching to the closing </div> that is followed
# by a non-navbar block element or end-of-string).
_NAVBAR_RE = re.compile(
    r'<div class="t-navbar"[^>]*>.*?(?:</div>\s*(?=<div|<h[1-6]|<table|<p|\Z))',
    re.DOTALL,
)
_NV_TABLE_RE = re.compile(r'<table class="t-nv-begin"[^>]*>.*?</table>', re.DOTALL)


def _fetch_page_sync(url: str) -> str:
    """Synchronous page fetch (CPython only)."""
    full_url = f"{_PAGE_BASE}/{url}" if not url.startswith("http") else url
    html_raw = _fetch_urllib(full_url, 15)
    return _clean_page_html(html_raw)


async def _fetch_page_async(url: str) -> str:
    """Async page fetch (Pyodide). Tries cppreference.com first, then
    falls back to the GitHub Pages mirror (CORS-safe)."""
    full_url = f"{_PAGE_BASE}/{url}" if not url.startswith("http") else url
    try:
        html_raw = await _fetch_pyodide(full_url)
        return _clean_page_html(html_raw)
    except Exception:
        # CORS or network error — fall back to GitHub Pages mirror.
        # Mirror pages are pre-stripped HTML (no #mw-content-text wrapper),
        # so we clean them differently.
        mirror_url = f"{_PAGES_MIRROR}/docs/{url}"
        html_raw = await _fetch_pyodide(mirror_url)
        return _clean_mirror_html(html_raw)


def _clean_page_html(html_raw: str) -> str:
    """Extract and clean the main content from a cppreference page."""
    m = _CONTENT_RE.search(html_raw)
    if not m:
        return "<p>Could not extract page content.</p>"
    content = m.group(1)
    # Clean up
    content = _SCRIPT_RE.sub("", content)
    content = _STYLE_RE.sub("", content)
    content = _COMMENT_RE.sub("", content)
    content = _EDIT_RE.sub("", content)
    # Strip any residual [edit] markers (from &#91;edit&#93; entities)
    content = re.sub(r"&#91;edit&#93;", "", content)
    content = re.sub(r"\[edit\]", "", content)
    # Strip cppreference navigation chrome (t-navbar, t-nv-begin tables)
    content = _NAVBAR_RE.sub("", content)
    content = _NV_TABLE_RE.sub("", content)
    # Fix relative URLs
    content = re.sub(r'href="/w/', 'href="https://en.cppreference.com/w/', content)
    content = re.sub(r'src="/', 'src="https://en.cppreference.com/', content)
    return content


def _clean_mirror_html(html_raw: str) -> str:
    """Clean a pre-stripped page from the GitHub Pages mirror.

    Mirror pages are raw HTML from the cppreference archive — they don't
    have the #mw-content-text wrapper, but they do have t-navbar and
    t-nv-begin tables that need stripping.
    """
    content = html_raw
    content = _SCRIPT_RE.sub("", content)
    content = _STYLE_RE.sub("", content)
    content = _COMMENT_RE.sub("", content)
    content = _EDIT_RE.sub("", content)
    content = re.sub(r"&#91;edit&#93;", "", content)
    content = re.sub(r"\[edit\]", "", content)
    content = _NAVBAR_RE.sub("", content)
    content = _NV_TABLE_RE.sub("", content)
    # Fix relative URLs in archive pages (../../cpp/... → /w/cpp/...)
    content = re.sub(
        r'href="(\.\./)*([^"]+\.html)"',
        lambda m: f'href="https://en.cppreference.com/w/{m.group(2)}"',
        content,
    )
    return content


# --------------------------------------------------------------------------- #
# HTML → plain-text conversion (for terminal output)
# --------------------------------------------------------------------------- #

# Tags that should produce a line break
_BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
               "hr", "table", "ul", "ol", "pre", "blockquote", "section"}


def _html_to_text(html_str: str, width: int = 80) -> str:
    """Convert HTML to readable plain text with proper line breaks."""
    # NB: strip HTML tags BEFORE decoding entities, otherwise
    # &lt;class T&gt; becomes <class T> and gets eaten as a fake tag.

    text = html_str

    # Replace block-level tags with newlines (before stripping all tags)
    for tag in _BLOCK_TAGS:
        text = re.sub(rf"<{tag}[^>]*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(rf"</{tag}>", "\n", text, flags=re.IGNORECASE)

    # <td> / <th> → tab separator
    text = re.sub(r"<t[dh][^>]*>", "\t", text, flags=re.IGNORECASE)
    text = re.sub(r"</t[dh]>", "", text, flags=re.IGNORECASE)

    # <code> / <tt> → backtick wrapping (strip the tag, keep content)
    text = re.sub(r"<code[^>]*>", "`", text, flags=re.IGNORECASE)
    text = re.sub(r"</code>", "`", text, flags=re.IGNORECASE)
    text = re.sub(r"<tt[^>]*>", "`", text, flags=re.IGNORECASE)
    text = re.sub(r"</tt>", "`", text, flags=re.IGNORECASE)

    # <b> / <strong> → ** (bold marker)
    text = re.sub(r"<b[^>]*>", "**", text, flags=re.IGNORECASE)
    text = re.sub(r"</b>", "**", text, flags=re.IGNORECASE)
    text = re.sub(r"<strong[^>]*>", "**", text, flags=re.IGNORECASE)
    text = re.sub(r"</strong>", "**", text, flags=re.IGNORECASE)

    # <i> / <em> → * (italic marker)
    text = re.sub(r"<i[^>]*>", "*", text, flags=re.IGNORECASE)
    text = re.sub(r"</i>", "*", text, flags=re.IGNORECASE)
    text = re.sub(r"<em[^>]*>", "*", text, flags=re.IGNORECASE)
    text = re.sub(r"</em>", "*", text, flags=re.IGNORECASE)

    # Strip all remaining tags
    text = re.sub(r"<[^>]+>", "", text)

    # NOW decode entities (safe — no more HTML tags to confuse)
    text = html.unescape(text)

    # Process line by line
    lines = text.split("\n")
    result = []
    for line in lines:
        # Expand tabs to 4 spaces
        line = line.expandtabs(4)
        # Collapse multiple spaces (but preserve indentation)
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        stripped = re.sub(r"  +", " ", stripped).strip()
        if stripped:
            # Wrap long lines
            wrapped = textwrap.fill(stripped, width=width,
                                    initial_indent=indent,
                                    subsequent_indent=indent + "  ")
            result.append(wrapped)
        elif result and result[-1]:  # preserve blank lines between content
            result.append("")

    # Remove leading/trailing blank lines
    while result and not result[0]:
        result.pop(0)
    while result and not result[-1]:
        result.pop()

    return "\n".join(result)


# --------------------------------------------------------------------------- #
# Display
# --------------------------------------------------------------------------- #

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


def _format_page_html(content: str, page_url: str = "") -> str:
    """Format page content for Jupyter display with working links.

    In a **trusted** notebook, Jupyter renders external https:// links
    with target=\"_blank\" — clicking opens cppreference.com in a new tab.
    In an untrusted notebook, the sanitizer strips external hrefs to '#'.

    Links:
    - Navigation links → absolute https://en.cppreference.com/w/... + target=\"_blank\"
    - Edit links (<a href=\".../index.php?...action=edit\">) → unwrapped (text kept, <a> removed)
    - Anchor links (href=\"#...\") → left as-is (in-page navigation)
    """
    from urllib.parse import urljoin

    base_href = (
        f"https://en.cppreference.com/w/{page_url}" if page_url
        else "https://en.cppreference.com/w/"
    )

    # 1. Remove edit links entirely: <a ... href=".../index.php?...action=edit...">text</a> → text
    content = re.sub(
        r'<a [^>]*href="[^"]*index\.php[^"]*action=edit[^"]*"[^>]*>(.*?)</a>',
        r'\1',
        content,
        flags=re.DOTALL,
    )

    # 2. Rewrite remaining hrefs
    def _rewrite_href(m: re.Match) -> str:
        href = m.group(1)

        # Skip javascript: URLs — neutralize
        if href.startswith("javascript:"):
            return 'href="#" onclick="return false"'

        # Skip anchors (in-page navigation) — keep as-is
        if href.startswith("#"):
            return f'href="{href}"'

        # Resolve to absolute URL if needed
        if not href.startswith("http://") and not href.startswith("https://"):
            href = urljoin(base_href, href)

        return f'href="{html.escape(href)}" target="_blank" rel="noopener"'

    content = re.sub(r'href="([^"]*)"', _rewrite_href, content)

    return (
        '<div class="cppmanlite-content" '
        'style="max-height:600px;overflow:auto;'
        'border:1px solid #ddd;padding:16px;font-size:14px">'
        + content
        + '</div>'
        + '<p style="font-size:11px;color:#888;margin-top:4px">'
        + 'Links open cppreference.com in a new tab. '
        + 'If links don\'t work, trust this notebook: '
        + '<b>File → Trust Notebook</b>'
        + '</p>'
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def man(query: str) -> Any:
    """Display a C++ documentation page (like ``man`` for C++).

    In Jupyter: renders HTML inline.
    In terminal: prints formatted plain text.
    In Pyodide: returns a coroutine (auto-awaited by the Pyodide REPL).

    Args:
        query: Page title or URL path (e.g. "std::vector" or "cpp/container/vector").
    """
    if _IS_PYODIDE:
        return _man_async(query)
    return _man_sync(query)


def _man_sync(query: str) -> None:
    """Synchronous man() for CPython / terminal."""
    results = search(query, limit=1)
    if not results:
        msg = f"No documentation found for '{query}'."
        if _is_jupyter():
            from IPython.display import HTML, display

            display(HTML(f"<p>{html.escape(msg)}</p>"))
        print(msg)
        return
    url = results[0]["url"]
    title = results[0]["title"]
    content = _fetch_page_sync(url)
    if _is_jupyter():
        from IPython.display import HTML, display

        display(HTML(_format_page_html(content, page_url=url)))
    else:
        # Terminal: print formatted text
        print(f"\n{'=' * 80}\n{title}\n{'=' * 80}\n")
        print(_html_to_text(content, width=80))


async def _man_async(query: str) -> None:
    """Async man() for Pyodide."""
    results = search(query, limit=1)
    if not results:
        print(f"No documentation found for '{query}'.")
        return
    url = results[0]["url"]
    title = results[0]["title"]
    content = await _fetch_page_async(url)
    if _is_jupyter():
        from IPython.display import HTML, display

        display(HTML(_format_page_html(content, page_url=url)))
    else:
        # Pyodide console / terminal
        print(f"\n{'=' * 80}\n{title}\n{'=' * 80}\n")
        print(_html_to_text(content, width=80))


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
    _INDEX = json.loads(_fetch_url_sync(url))
    return len(_INDEX)


# Re-export ``help`` under a safe alias to avoid shadowing builtin
help_query = help
