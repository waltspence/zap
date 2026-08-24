"""Platform backend selection (no import-time platform gating)."""

from __future__ import annotations

from ..environment import Environment
from .base import Backend, ResolveResult


def get_backend(env: Environment) -> Backend:
    if env.is_darwin:
        from .macos import MacBackend

        return MacBackend(env)
    from .linux import LinuxBackend

    return LinuxBackend(env)


__all__ = ["Backend", "ResolveResult", "get_backend"]
