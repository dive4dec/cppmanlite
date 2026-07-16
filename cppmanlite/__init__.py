"""cppmanlite — lightweight serverless C++ documentation lookup.

A pure-Python package for searching and displaying C++ documentation
from cppreference.com.  Works in standard Python, Jupyter notebooks,
and Pyodide (no C dependencies).

Usage:
    import cppmanlite
    cppmanlite.search("vector")       # list matching pages
    cppmanlite.man("std::vector")     # display a page
    cppmanlite.help("sort")           # alias for search

In Jupyter, results render as HTML with clickable links.
"""

from .core import search, man, help as help_query, list_pages, refresh_index

__version__ = "0.1.0"
__all__ = ["search", "man", "help_query", "list_pages", "refresh_index"]
