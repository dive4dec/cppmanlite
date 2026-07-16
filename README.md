# cppmanlite

Lightweight serverless C++ documentation lookup — a pure Python package and
static site powered by [cppreference.com](https://en.cppreference.com).

## Features

- **Pure Python** — no C dependencies, works in CPython, Pyodide, Jupyter
- **Static site** — served from GitHub Pages or any static host
- **Client-side search** — lunr.js fuzzy search over a ~1MB index
- **On-demand page loading** — fetches from cppreference.com or local bundle
- **Auto-updating** — GitHub Action refreshes the index monthly
- **Kubernetes-ready** — Helm chart included for `/cppmanlite` route

## Python package

```bash
pip install cppmanlite
```

```python
import cppmanlite

# Search for C++ documentation
cppmanlite.search("vector")
# [{'title': 'std::vector', 'url': 'cpp/container/vector.html', ...}, ...]

# Display a page (renders HTML in Jupyter, plain text in terminal)
cppmanlite.man("std::vector")

# Alias
cppmanlite.help("sort")
```

Works in [Pyodide](https://pyodide.org) (Python in the browser) — no compiled
dependencies required.

## Static site

The `site/` directory contains a standalone search UI:

- `index.html` — search box + results + page reader
- `app.js` — lunr.js search + on-demand page fetching
- `style.css` — dark/light theme

Deployed to GitHub Pages at: **https://dive4dec.github.io/cppmanlite/**

## Docker / Kubernetes

```bash
docker build -t cppmanlite:v1 .
```

Multi-stage build: downloads the cppreference HTML archive, strips boilerplate,
builds a search index, and serves everything via nginx.

```bash
helm install cppmanlite ./chart -n cppmanlite
```

Serves at `https://socratic.cs.cityu.edu.hk/cppmanlite/`

## How it works

1. **Index building** (`scripts/build_index.py`): Downloads the cppreference
   HTML book archive, extracts page titles + snippets, fixes relative URLs,
   and writes a compact JSON index (~1.2MB for 6,000+ pages).

2. **Search**: lunr.js builds a client-side full-text search index from the
   JSON. Results show title, URL path, and a content snippet.

3. **Page reading**: When a user clicks a result, the reader fetches the page
   — either from a local `docs/` bundle (Docker/K8s deployment) or directly
   from cppreference.com (GitHub Pages).

4. **Auto-update**: A GitHub Action runs monthly, downloads the latest
   cppreference archive, rebuilds the index, and deploys to GitHub Pages.

## Data source

Documentation from [cppreference.com](https://en.cppreference.com), licensed
under CC-BY-SA 3.0 / GFDL. The HTML book archive is published by
[PeterFeicht/cppreference-doc](https://github.com/PeterFeicht/cppreference-doc).

## License

MIT — see [LICENSE](LICENSE).
