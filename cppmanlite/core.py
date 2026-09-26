"""Core search and display logic for cppmanlite.

No external dependencies — pure stdlib.  Fetches pages on-demand from
cppreference.com when not bundled locally.

Works in CPython, Jupyter, and Pyodide (browser).  In Pyodide, network
fetches use the browser's Fetch API via pyodide.http instead of urllib.
"""

from __future__ import annotations

import html
import json
import math
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
#
# Deterministic scorer that mirrors site/app.js exactly (same tokenisation,
# alias table, field weights, AND penalty and sort). Replacing the old naive
# substring scorer so the Python package and the static site rank identically.
# Why not BM25/lunr here? There is no lunr dependency in the package (it must
# stay pure-stdlib / Pyodide-safe), and a hand-rolled scorer puts exact-title
# hits first in a fully predictable way.

_SEARCH_TOKEN_RE = re.compile(r"[a-z_][a-z0-9_]*|[0-9]+")

# cppreference-style aliases: map a common name to the canonical identifier
# used in page titles. (std::string == std::basic_string, ...)
_SEARCH_ALIAS = {
    "string": "basic_string", "wstring": "basic_wstring",
    "u8string": "basic_u8string", "u16string": "basic_u16string",
    "u32string": "basic_u32string", "str": "basic_string", "wstr": "basic_wstring",
}

# field weights: (whole-token match, substring-inside-larger-token)
_W = {"title": (100, 40), "terms": (80, 32), "url": (40, 16), "snippet": (8, 3)}


def _tok_set(s: str) -> set:
    return set(_SEARCH_TOKEN_RE.findall(s.lower()))


def _strip_ns(s: str) -> str:
    return re.sub(r"^(std::)+", "", s)


def _strip_args(s: str) -> str:
    return re.sub(r"\(.*\)\s*$", "", s).strip()


def _alias_expand(s: str) -> str:
    parts = [p for p in re.split(r"[\s_]+", s) if p]
    return "_".join(_SEARCH_ALIAS.get(p, p) for p in parts)


def _match_tok(tok: str, s_low: str, tokset: set, full: int, sub: int) -> int:
    if tok in tokset:
        return full
    if tok in s_low:
        return sub
    return 0


def _term_score(tok: str, term_freq: dict, terms_low: str) -> float:
    # base "terms" weight, scaled up (log) by how often the identifier appears
    # in the page body → the defining page outranks a page that just mentions it.
    n = term_freq.get(tok, 0)
    if n:
        return _W["terms"][0] * (1 + math.log2(n))
    if tok in terms_low:
        return _W["terms"][1]
    return 0


def _doc_score(meta: dict, search_toks: list, whole_cands: list) -> float:
    score = 0
    matched = 0
    # Whole-title relations: EXACT always; PREFIX only for specific/compound queries.
    for wc in whole_cands:
        if not wc or len(wc) < 2:
            continue
        d_title = _strip_ns(meta["t_low"])
        if d_title == wc:
            score += 1000
        else:
            specific = len(search_toks) >= 2 or "_" in wc or len(wc) >= 8
            if specific and d_title.startswith(wc + " "):
                score += 400
    for tok in search_toks:
        s = max(
            _match_tok(tok, meta["t_low"], meta["t_tok"], _W["title"][0], _W["title"][1]),
            _term_score(tok, meta["term_freq"], meta["terms_low"]),
            _match_tok(tok, meta["u_low"], meta["u_tok"], _W["url"][0], _W["url"][1]),
            _match_tok(tok, meta["s_low"], meta["s_tok"], _W["snippet"][0], _W["snippet"][1]),
        )
        if s > 0:
            score += s
            matched += 1
    # AND semantics: penalise if not every query token matched somewhere.
    if len(search_toks) > 1 and matched < len(search_toks):
        score *= matched / len(search_toks)
    return score


def _build_meta(entry: dict) -> dict:
    t = entry.get("title", "")
    u = entry.get("url", "")
    s = entry.get("snippet", "")
    # terms is a {identifier: count} map; tolerate an older list format.
    terms = entry.get("terms") or {}
    if isinstance(terms, list):
        term_freq = {t: 1 for t in terms}
    else:
        term_freq = terms
    t_low = t.lower()
    return {
        "t_low": t_low,
        "u_low": u.lower(),
        "s_low": s.lower(),
        "t_tok": _tok_set(t),
        "u_tok": _tok_set(u),
        "s_tok": _tok_set(s),
        "term_freq": term_freq,
        "terms_low": " ".join(term_freq.keys()),
        "title_len": len(t_low),
    }


def search(query: str, limit: int = 20) -> list[dict[str, str]]:
    """Search C++ documentation pages.

    Deterministic scorer (mirrors the static site): exact-title matches rank
    first, then alias titles, then identifiers found in a page's body (e.g.
    ``int64_t`` → Fixed width integer types), then URL/snippet substring hits.

    Args:
        query: Search term (e.g. "vector", "std::max", "int64_t", "shared_ptr").
        limit: Maximum number of results.

    Returns:
        List of dicts with keys: title, url, snippet.
    """
    idx = _load_index()
    if not idx:
        return []
    q_norm = query.lower().strip()
    if not q_norm:
        return []

    q_toks = _SEARCH_TOKEN_RE.findall(q_norm)
    # drop a leading "std" namespace token (std::vector → ["std","vector"])
    if q_toks and q_toks[0] == "std" and q_norm.startswith("std::"):
        q_toks = q_toks[1:]
    if not q_toks:
        return []

    # expand each token through the alias table, dedup, preserve order
    seen = set()
    search_toks = []
    for t in q_toks:
        for cand in (t, _SEARCH_ALIAS.get(t, "")):
            if cand and cand not in seen:
                seen.add(cand)
                search_toks.append(cand)

    q_title_whole = _strip_args(_strip_ns(q_norm))
    whole_cands = []
    for c in (q_title_whole, _alias_expand(q_title_whole)):
        if len(c) >= 2 and c not in whole_cands:
            whole_cands.append(c)

    scored = []
    for entry in idx:
        score = _doc_score(_build_meta(entry), search_toks, whole_cands)
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda x: (-x[0], len(x[1].get("title", "").lower()), x[1].get("url", "")))
    return [entry for _, entry in scored[:limit]]


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
