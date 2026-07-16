# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.2] - 2026-07-16

### Fixed
- **Jupyter link resolution** — relative links in `man()` output resolved
  against the JupyterHub host (e.g. `https://dive.cs.cityu.edu.hk/cpp/...`)
  instead of cppreference.com. Now wraps content in an `<iframe srcdoc>` with
  a `<base href="https://en.cppreference.com/w/...">` tag so all relative
  links resolve correctly inside the sandboxed iframe.

## [0.1.1] - 2026-07-16

### Fixed
- **Pyodide async fetch** — `pyfetch()` returns a coroutine; `_fetch_pyodide()`
  is now properly `async` with `await pyfetch(...)` and `await resp.string()`.
  `man()` returns a coroutine in Pyodide (auto-awaited by the REPL).
- **Pyodide CORS fallback** — `en.cppreference.com` doesn't send CORS headers,
  so direct browser fetches fail. `man()` now tries cppreference.com first,
  then falls back to the GitHub Pages mirror (`dive4dec.github.io/cppmanlite/docs/`).
- **Terminal text formatting** — `man()` output was a single cramped line.
  New `_html_to_text()` converts HTML to readable 80-column plain text with
  proper line breaks for block-level tags (p, div, tr, li, h1-h6, etc.).
- **Template code mangled** — `&lt;class T&gt;` was decoded to `<class T>` before
  tag stripping, causing it to be eaten as a fake HTML tag. Tags are now
  stripped **before** entity decoding.
- **`[edit]` markers in output** — `_EDIT_RE` only matched `mw-editsection`
  spans; now also matches `editsection noprint plainlinks`. Strips both
  `&#91;edit&#93;` entities and literal `[edit]` text.
- **GitHub Actions workflow** — rewrote to use `peaceiris/actions-gh-pages`
  (deploys to `gh-pages` branch), removed broken `configure-pages`/
  `upload-pages-artifact`/`deploy-pages` approach. Removed unnecessary
  `pip install -e .` (build_index.py is pure stdlib).
- **pyproject.toml build backend** — fixed from invalid
  `setuptools.backends._legacy:_Backend` to `setuptools.build_meta`.
  License changed to SPDX string. Removed deprecated license classifier.

### Changed
- Rebuilt bundled `index.json` with fixed `build_index.py` (no `&lt;`/`&gt;`/`Â`
  artifacts in titles).
- `_fetch_page()` strips `t-navbar` and `t-nv-begin` navigation chrome from
  fetched pages (same treatment as the web UI).

### Tested
- CPython terminal: `search()`, `man()`, `help_query()` — all pass
- Pyodide 0.28.2: `import`, `search`, `man` (via CORS fallback), `help_query` — all pass
- Jupyter: HTML rendering verified (57KB content)
- Wheel: `py3-none-any`, no build warnings, clean venv install

## [0.1.0] - 2026-07-16

### Added
- **Pure Python package** (`cppmanlite`) — `search()`, `man()`, `help()` functions
  for querying C++ documentation. No external dependencies. Works in CPython,
  Pyodide, and Jupyter (renders HTML inline).
- **Static search site** — lunr.js-powered client-side search over a 1.2MB index
  of 6,010 C++ reference pages. Dark/light theme, on-demand page reader.
- **Docker image** — multi-stage build: downloads cppreference HTML archive,
  strips boilerplate, builds search index, serves via nginx.
- **Helm chart** — deploys to Kubernetes at route `/cppmanlite`.
- **GitHub Action** — monthly auto-update of the search index and GitHub Pages
  deployment.
- **Search index** (`cppmanlite/data/index.json`) — 6,010 pages with titles,
  URL paths, and content snippets. Built from cppreference HTML book archive
  v20250209.
