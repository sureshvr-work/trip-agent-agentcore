"""Importing this package registers every tool with the registry."""
from tools.registry import specs, call, TOOLS          # re-export
from tools import geocode, weather, places, websearch, flights  # noqa: F401 (register)

__all__ = ["specs", "call", "TOOLS"]
