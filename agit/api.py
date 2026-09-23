"""Compatibility wrapper. The HTTP adapter lives in agit.service.api."""

from agit.service.api import DEFAULT_HOST, DEFAULT_PORT, AgitHTTPServer, build_server, serve

__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "AgitHTTPServer", "build_server", "serve"]
