"""dblp-sparql-mcp — MCP server for the official dblp SPARQL endpoint.

The package version is read from the installed distribution metadata so
``pyproject.toml`` remains the single source of truth.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("dblp-sparql-mcp")
except PackageNotFoundError:  # pragma: no cover
    # Running from a checkout without an installed distribution.
    __version__ = "0+unknown"

__all__ = ["__version__"]
