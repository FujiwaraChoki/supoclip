"""Compatibility import for existing deployments. Use src.main for new setups."""

from .main import app, create_app

__all__ = ["app", "create_app"]
