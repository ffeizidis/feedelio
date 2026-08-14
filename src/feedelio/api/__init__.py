"""HTTP layer: a thin JSON API over :mod:`feedelio.core`, plus the built SPA."""

from feedelio.api.app import create_app

__all__ = ["create_app"]
