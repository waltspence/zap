"""Validation and classification of app-supplied identifiers.

Bundle identifiers, bundle names, executable names and ``.desktop`` fields all
originate from files the *application* controls. zap turns them into
filesystem paths and glob patterns, so they are untrusted input: a value
containing ``/``, ``..``, a NUL byte, or one that is empty or a copy-paste
template (``com.example.app``) must never be expanded into a path. This module
is the choke point that every identifier passes through before any path is
built from it.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# A conservative Apple/UTI-style bundle identifier: reverse-DNS, at least two
# dot-separated labels, ASCII alnum plus '-'. Deliberately stricter than what
# macOS technically accepts — we would rather reject an exotic id (and fall
# back to name matching) than build a path from something surprising.
_BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*(\.[A-Za-z0-9][A-Za-z0-9-]*)+$")

# A 10-character Apple Team Identifier (used to prefix hardened-runtime caches
# and group containers). Matched exactly — never used as a substring glob.
_TEAM_ID_RE = re.compile(r"^[A-Z0-9]{10}$")

_MAX_BUNDLE_ID_LEN = 255

# Identifiers that are generic, placeholder, or shared across many unrelated
# apps. A path must never be *auto-derived* from one of these — doing so would
# sweep up files belonging to other software. They are surfaced to the user as
# "manual only" instead.
_TEMPLATE_PREFIXES = (
    "com.example.",
    "com.yourcompany.",
    "com.company.",
    "com.mycompany.",
    "com.test.",
    "com.electron.",
    "org.electron.",
    "com.github.electron.",
)
_TEMPLATE_EXACT = frozenset(
    {
        "com.example",
        "com.electron.app",
        "com.electron",
        "com.your-company.app",
        "com.apple.dt.xcode.tools",  # placeholder-ish; not an installed app
    }
)

# Vendor updater / framework / crash-reporter identifiers that are legitimately
# shared by *many* apps from a vendor. Attributing them to a single app and
# deleting them breaks the others (e.g. removing Chrome must not delete the
# Keystone updater that also serves Google Drive). Surfaced as shared, never
# auto-selected.
SHARED_COMPONENT_IDS = frozenset(
    {
        "com.google.keystone",
        "com.google.keystone.agent",
        "com.google.keystone.daemon",
        "com.google.softwareupdate",
        "org.sparkle-project.sparkle",
        "com.microsoft.autoupdate",
        "com.microsoft.autoupdate2",
        "com.microsoft.autoupdate.fba",
        "com.adobe.acc.installer",
        "com.adobe.armdc.app",
        "com.oracle.java.updater",
    }
)
SHARED_COMPONENT_PREFIXES = (
    "com.google.keystone",
    "org.sparkle-project.",
    "com.microsoft.autoupdate",
    "com.apple.",  # Apple system components are never a third-party leftover
)

# Names that identify shared desktop/session infrastructure, not app leftovers.
# A *name-derived* match may never resolve to any of these (case-insensitive),
# on either platform.
RESERVED_DIR_NAMES = frozenset(
    n.lower()
    for n in {
        # Linux / XDG session + desktop
        "systemd",
        "autostart",
        "pulse",
        "pulseaudio",
        "dconf",
        "gtk-2.0",
        "gtk-3.0",
        "gtk-4.0",
        "fontconfig",
        "dbus-1",
        "ibus",
        "nautilus",
        "gvfs",
        "mimeapps.list",
        "user-dirs.dirs",
        "environment.d",
        "pipewire",
        "wireplumber",
        # KDE / Plasma
        "plasma",
        "plasmashell",
        "kdeglobals",
        "kwinrc",
        # macOS shared support dirs
        "apple",
        "crashreporter",
        "com.apple.crashreporter",
        "mobilesync",
        "addressbook",
        "calendars",
        "syncservices",
        # generic caches that are not app-owned
        "google",  # only reserved for *name* matching; bundle-id matching is fine
        "microsoft",
        "adobe",
    }
)


class IdentifierError(ValueError):
    """Raised when an identifier is unusable for path construction."""


def normalize_casefold(value: str) -> str:
    """Case-fold + NFC-normalize for robust cross-form comparison."""
    return unicodedata.normalize("NFC", value).casefold()


def name_forms(value: str) -> "set[str]":
    """All Unicode normalization forms of a name we should compare against.

    APFS stores filenames decomposed (NFD) while most tools produce composed
    (NFC); comparing a single form silently misses matches, so we keep both.
    """
    forms = set()
    for form in ("NFC", "NFD"):
        forms.add(unicodedata.normalize(form, value))
    return forms


def is_valid_bundle_id(value: Optional[str]) -> bool:
    if not value or not isinstance(value, str):
        return False
    if "\x00" in value or "/" in value or "\\" in value:
        return False
    if ".." in value:
        return False
    if len(value) > _MAX_BUNDLE_ID_LEN:
        return False
    return bool(_BUNDLE_ID_RE.match(value))


def is_template_bundle_id(value: str) -> bool:
    low = value.lower()
    if low in _TEMPLATE_EXACT:
        return True
    return any(low.startswith(p) for p in _TEMPLATE_PREFIXES)


def is_shared_component_id(value: str) -> bool:
    low = value.lower()
    if low in SHARED_COMPONENT_IDS:
        return True
    return any(low.startswith(p) for p in SHARED_COMPONENT_PREFIXES)


def is_apple_system_id(value: str) -> bool:
    return value.lower().startswith("com.apple.")


def usable_bundle_id(value: Optional[str]) -> Optional[str]:
    """Return the id if it is safe to derive paths from, else ``None``.

    ``None`` means "fall back to name matching, never auto-select" rather than
    "error" — a missing or template id is a common, benign situation.
    """
    if not is_valid_bundle_id(value):
        return None
    assert value is not None
    if is_template_bundle_id(value):
        return None
    return value


def is_valid_team_id(value: Optional[str]) -> bool:
    return bool(value) and bool(_TEAM_ID_RE.match(value or ""))


def is_safe_name_component(value: Optional[str]) -> bool:
    """Whether ``value`` may be used as a single path component in a match."""
    if not value or not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped:
        return False
    if stripped in {".", ".."}:
        return False
    if "\x00" in value or "/" in value or "\\" in value:
        return False
    return True


def is_reserved_name(value: str) -> bool:
    return normalize_casefold(value).strip() in {
        normalize_casefold(n) for n in RESERVED_DIR_NAMES
    }


def bundle_id_prefix_matches(target: str, candidate: str) -> bool:
    """Dot-boundary prefix test: does ``candidate`` belong to ``target``?

    ``com.google.Chrome`` matches ``com.google.Chrome`` and
    ``com.google.Chrome.helper`` (next char is a dot) but NOT
    ``com.google.ChromeCanary`` — which is a *different, sibling* app.
    """
    if not target or not candidate:
        return False
    t = normalize_casefold(target)
    c = normalize_casefold(candidate)
    if c == t:
        return True
    return c.startswith(t + ".")


def vendor_product_from_bundle_id(bundle_id: str) -> Optional["tuple[str, str]"]:
    """Decompose a reverse-DNS id into (vendor, product) folder names.

    ``com.google.Chrome`` -> ("Google", "Chrome"); this is how Electron and
    many native apps name ``Application Support/<Vendor>/<Product>`` trees that
    are keyed by neither the bundle id nor the app's display name.
    """
    parts = bundle_id.split(".")
    if len(parts) < 3:
        return None
    vendor = parts[1]
    product = parts[-1]
    if not vendor or not product:
        return None
    return (vendor.capitalize(), product)


# Imported late to avoid a hard dependency at module import for the rare
# ``is_safe_name_component`` os.curdir check above.
import os  # noqa: E402
