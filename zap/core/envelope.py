"""The machine-readable output envelope and the exit-code taxonomy.

Every ``--json`` invocation prints exactly one of these objects to stdout. Error
codes map 1:1 onto exit codes so an agent can branch on either. Bytes are
integers, timestamps are RFC3339 UTC.
"""

from __future__ import annotations

from typing import Any, List, Optional

from .. import SCHEMA_VERSION, __version__

# -- exit codes --------------------------------------------------------------
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_APP_NOT_FOUND = 3
EXIT_NOTHING = 4
EXIT_PARTIAL = 5
EXIT_BLOCKED = 6
EXIT_CONFIRMATION = 7
EXIT_AMBIGUOUS = 8
EXIT_LOCKED = 9

# -- error codes (stable enum) aligned to exit codes -------------------------
ERROR_EXIT = {
    "usage_error": EXIT_USAGE,
    "unknown_ids": EXIT_USAGE,
    "app_not_found": EXIT_APP_NOT_FOUND,
    "ambiguous_selector": EXIT_AMBIGUOUS,
    "nothing_to_remove": EXIT_NOTHING,
    "partial_failure": EXIT_PARTIAL,
    "blocked_by_safety": EXIT_BLOCKED,
    "plan_app_mismatch": EXIT_BLOCKED,
    "confirmation_required": EXIT_CONFIRMATION,
    "permission_required": EXIT_BLOCKED,
    "locked": EXIT_LOCKED,
    "unknown_operation": EXIT_USAGE,
    "internal_error": EXIT_ERROR,
}


# Reverse map for envelopes that carry a non-zero exit without a raised error.
EXIT_ERROR_CODE = {
    EXIT_ERROR: "internal_error",
    EXIT_USAGE: "usage_error",
    EXIT_APP_NOT_FOUND: "app_not_found",
    EXIT_NOTHING: "nothing_to_remove",
    EXIT_PARTIAL: "partial_failure",
    EXIT_BLOCKED: "blocked_by_safety",
    EXIT_CONFIRMATION: "confirmation_required",
    EXIT_AMBIGUOUS: "ambiguous_selector",
    EXIT_LOCKED: "locked",
}
EXIT_MESSAGES = {
    EXIT_NOTHING: "nothing was removed or restored",
    EXIT_PARTIAL: "some artifacts failed, drifted, or were blocked",
    EXIT_BLOCKED: "every selected artifact was blocked by the safety guard",
}


class ZapError(Exception):
    """A user-facing error carrying a stable code, message, and details."""

    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    @property
    def exit_code(self) -> int:
        return ERROR_EXIT.get(self.code, EXIT_ERROR)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


def envelope(
    command: str,
    *,
    ok: bool,
    data: Any = None,
    warnings: Optional[List[dict]] = None,
    error: Optional[dict] = None,
) -> dict:
    return {
        "zap_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "ok": ok,
        "data": data,
        "warnings": warnings or [],
        "error": error,
    }
