"""JSCC dashboard: a read-mostly FastAPI + Jinja2 app over the same
storage layer the CLI uses. See ADR-007 for the stack choice."""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
