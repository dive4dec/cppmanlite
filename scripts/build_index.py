#!/usr/bin/env python3
"""Build the search index and strip HTML pages from the cppreference archive.

Usage:
    python3 build_index.py <input_dir> <output_dir>

Input:  directory containing cpp/ and c/ subdirs with .html files
Output: <output_dir>/index.json  +  <output_dir>/docs/ with stripped HTML
"""

import html
import json
import os
import re
import sys
from pathlib import Path


SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL)
STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
EDIT_RE = re.compile(r'<span class="mw-editsection">.*?</span>', re.DOTALL)
TITLE_RE = re.compile(r"<title>(.*?)</title>")
CONTENT_RE = re.compile(
    r'<div id="mw-content-text"[^>]*>(.*?)(?:</div>\s*<!--|\Z)', re.DOTALL
)
P_RE = re.compile(r"<p>(.*?)</p>", re.DOTALL)


def strip_page(html_text: str) -> tuple[str, str]:
    """Return (title, stripped_content_html) from a cppreference page."""
    m = TITLE_RE.search(html_text)
    raw_title = m.group(1).replace(" - cppreference.com", "").strip() if m else ""
    # Decode HTML entities first (&lt; → <, &nbsp; → \xa0, etc.)
    title = html.unescape(raw_title)
    # Fix mojibake: cppreference HTML sometimes has \xc3\x82\xc2\xa0 (double-encoded nbsp)
    # or stray \xc2 from encoding mismatches. Normalise: replace \xc2\xa0 and \xa0 with space.
    title = title.replace("\xc2\xa0", " ").replace("\xa0", " ")
    # Collapse multiple spaces
    title = re.sub(r"\s+", " ", title).strip()
    m = CONTENT_RE.search(html_text)
    content = m.group(1) if m else html_text
    content = SCRIPT_RE.sub("", content)
    content = STYLE_RE.sub("", content)
    content = COMMENT_RE.sub("", content)
    content = EDIT_RE.sub("", content)
    # Fix relative URLs to point to cppreference.com
    content = re.sub(r'href="/w/', 'href="https://en.cppreference.com/w/', content)
    content = re.sub(r'href="/cpp/', 'href="https://en.cppreference.com/cpp/', content)
    content = re.sub(r'src="/', 'src="https://en.cppreference.com/', content)
    return title, content


def extract_snippet(content: str, max_len: int = 200) -> str:
    m = P_RE.search(content)
    if m:
        text = re.sub(r"<[^>]+>", "", m.group(1))
        text = html.unescape(text)
        text = text.replace("\xc2\xa0", " ").replace("\xa0", " ")
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_len]
    return ""


def main():
    if len(sys.argv) < 3:
        print("Usage: build_index.py <input_dir> <output_dir>")
        sys.exit(1)

    input_dir = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    docs_dir = output_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    pages = []
    count = 0

    for subdir in ("cpp", "c"):
        src_root = input_dir / subdir
        if not src_root.exists():
            continue
        for root, dirs, files in os.walk(src_root):
            for f in files:
                if not f.endswith(".html"):
                    continue
                src_path = Path(root) / f
                rel_path = src_path.relative_to(input_dir)  # cpp/container/vector.html
                try:
                    html_text = src_path.read_text(encoding="utf-8", errors="replace")
                    title, content = strip_page(html_text)
                    snippet = extract_snippet(content)
                    url = str(rel_path)
                    pages.append({"title": title, "url": url, "snippet": snippet})
                    # Write stripped page to output/docs/
                    out_path = docs_dir / rel_path
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text(content, encoding="utf-8")
                    count += 1
                except Exception as e:
                    print(f"  SKIP {rel_path}: {e}", file=sys.stderr)

    pages.sort(key=lambda x: x["title"])
    index_path = output_dir / "index.json"
    index_path.write_text(
        json.dumps(pages, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Indexed {count} pages → {index_path}")
    print(f"Index size: {index_path.stat().st_size // 1024}KB")
    print(f"Stripped docs: {docs_dir}")


if __name__ == "__main__":
    main()
