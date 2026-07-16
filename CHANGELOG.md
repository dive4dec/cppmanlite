# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
