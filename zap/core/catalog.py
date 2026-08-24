"""The declarative catalog of where apps scatter their files.

Each :class:`Location` names a container directory and a *strategy* for turning
the target app's identifiers into concrete artifacts inside it. The same list
also yields the safety guard's allow-listed roots (:func:`roots_for`), so the
guard can never permit a path the catalog would not search, nor drift from it.

Strategies (implemented in ``scanner.py``):

``id_path``          container/<bundle_id><suffix> — exact, one file/dir.
``id_scan``          scan children, keep bundle-id / prefix / team-prefixed.
``name_scan``        scan children, keep display/product-name matches (heuristic).
``vendor_dir``       container/<Vendor>/<Product> from the bundle-id domain.
``byhost``           container/<bundle_id>.*.plist glob (ByHost prefs).
``group_container``  group.<bundle_id> exact + <teamid>.* — always shared.
``receipt``          container/<bundle_id>.bom + .plist (installer receipts).
``crash``            scan children named <ProcessName>_… (heuristic).
``bundle_scan``      scan child *bundles*, read each Info.plist, match its id.
``icloud``           iCloud~<vendor>~<app> containers — user data, never auto.
``warn_only``        surfaced but NEVER removed (kexts / system extensions).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

from ..environment import HOME, SYS
from . import models as M

# Strategy identifiers
ID_PATH = "id_path"
ID_SCAN = "id_scan"
NAME_SCAN = "name_scan"
VENDOR_DIR = "vendor_dir"
BYHOST = "byhost"
GROUP_CONTAINER = "group_container"
RECEIPT = "receipt"
CRASH = "crash"
BUNDLE_SCAN = "bundle_scan"
ICLOUD = "icloud"
WARN_ONLY = "warn_only"


@dataclass(frozen=True)
class Location:
    category: str
    base: str  # HOME or SYS
    container: str  # subpath under base, e.g. "Library/Caches"
    strategy: str
    suffixes: Tuple[str, ...] = ()  # for id_path/receipt
    risk: Optional[str] = None  # overrides CATEGORY_RISK
    shared: bool = False
    requires_root: bool = False
    note: str = ""

    def risk_level(self) -> str:
        return self.risk or M.CATEGORY_RISK.get(self.category, M.RISK_NORMAL)


def _mac_user() -> List[Location]:
    L = "Library"
    return [
        Location(M.CAT_PREFERENCES, HOME, f"{L}/Preferences", ID_PATH, (".plist",)),
        Location(M.CAT_PREFERENCES, HOME, f"{L}/Preferences/ByHost", BYHOST),
        Location(M.CAT_CACHES, HOME, f"{L}/Caches", ID_SCAN),
        Location(M.CAT_HTTP_STORAGE, HOME, f"{L}/HTTPStorages", ID_SCAN),
        Location(M.CAT_WEBKIT, HOME, f"{L}/WebKit", ID_SCAN),
        Location(M.CAT_COOKIES, HOME, f"{L}/Cookies", ID_PATH, (".binarycookies",)),
        Location(M.CAT_CONTAINERS, HOME, f"{L}/Containers", ID_SCAN),
        Location(
            M.CAT_GROUP_CONTAINERS, HOME, f"{L}/Group Containers", GROUP_CONTAINER,
            shared=True,
            note="Group containers are shared between an app's extensions and "
            "sometimes sibling apps; review before removing.",
        ),
        Location(M.CAT_APP_SUPPORT, HOME, f"{L}/Application Support", ID_SCAN),
        Location(M.CAT_APP_SUPPORT, HOME, f"{L}/Application Support", NAME_SCAN),
        Location(M.CAT_APP_SUPPORT, HOME, f"{L}/Application Support", VENDOR_DIR),
        Location(
            M.CAT_CRASH_REPORT, HOME, f"{L}/Application Support/CrashReporter", CRASH
        ),
        Location(
            M.CAT_SAVED_STATE, HOME, f"{L}/Saved Application State", ID_PATH,
            (".savedState",),
        ),
        Location(M.CAT_LOGS, HOME, f"{L}/Logs", ID_SCAN),
        Location(M.CAT_LOGS, HOME, f"{L}/Logs", NAME_SCAN),
        Location(M.CAT_CRASH_REPORT, HOME, f"{L}/Logs/DiagnosticReports", CRASH),
        Location(M.CAT_LAUNCH_AGENT, HOME, f"{L}/LaunchAgents", ID_PATH, (".plist",)),
        Location(M.CAT_APP_SCRIPTS, HOME, f"{L}/Application Scripts", ID_SCAN),
        Location(M.CAT_PLUGIN, HOME, f"{L}/PreferencePanes", BUNDLE_SCAN),
        Location(M.CAT_PLUGIN, HOME, f"{L}/QuickLook", BUNDLE_SCAN),
        Location(M.CAT_PLUGIN, HOME, f"{L}/Spotlight", BUNDLE_SCAN),
        Location(M.CAT_PLUGIN, HOME, f"{L}/Internet Plug-Ins", BUNDLE_SCAN),
        Location(M.CAT_PLUGIN, HOME, f"{L}/Screen Savers", BUNDLE_SCAN),
        Location(
            M.CAT_ICLOUD, HOME, f"{L}/Mobile Documents", ICLOUD,
            note="iCloud container may hold user documents; never auto-selected.",
        ),
    ]


def _mac_system() -> List[Location]:
    L = "Library"
    E = M.RISK_ELEVATED
    return [
        Location(M.CAT_LAUNCH_AGENT, SYS, f"{L}/LaunchAgents", ID_PATH, (".plist",), risk=E, requires_root=True),
        Location(M.CAT_LAUNCH_DAEMON, SYS, f"{L}/LaunchDaemons", ID_PATH, (".plist",), risk=E, requires_root=True,
                 note="Boot out the daemon (launchctl bootout) before removing, "
                      "or launchd may rewrite it until the next reboot."),
        Location(M.CAT_APP_SUPPORT, SYS, f"{L}/Application Support", ID_SCAN, risk=E, requires_root=True),
        Location(M.CAT_APP_SUPPORT, SYS, f"{L}/Application Support", NAME_SCAN, risk=E, requires_root=True),
        Location(M.CAT_CACHES, SYS, f"{L}/Caches", ID_SCAN, risk=E, requires_root=True),
        Location(M.CAT_PREFERENCES, SYS, f"{L}/Preferences", ID_PATH, (".plist",), risk=E, requires_root=True),
        Location(M.CAT_PRIVILEGED_HELPER, SYS, f"{L}/PrivilegedHelperTools", ID_PATH, ("",), risk=E, requires_root=True),
        Location(M.CAT_PLUGIN, SYS, f"{L}/PreferencePanes", BUNDLE_SCAN, risk=E, requires_root=True),
        Location(M.CAT_PLUGIN, SYS, f"{L}/QuickLook", BUNDLE_SCAN, risk=E, requires_root=True),
        Location(M.CAT_PLUGIN, SYS, f"{L}/Spotlight", BUNDLE_SCAN, risk=E, requires_root=True),
        Location(M.CAT_PLUGIN, SYS, f"{L}/Internet Plug-Ins", BUNDLE_SCAN, risk=E, requires_root=True),
        Location(M.CAT_RECEIPT, SYS, "var/db/receipts", RECEIPT, (".bom", ".plist"), risk=E, requires_root=True),
        Location(M.CAT_RECEIPT, SYS, f"{L}/Receipts", RECEIPT, (".bom", ".plist", ".pkg"), risk=E, requires_root=True),
        Location(M.CAT_SYSTEM_EXTENSION, SYS, f"{L}/Extensions", WARN_ONLY, risk=E,
                 note="Kernel extension — detected only; zap never removes kexts."),
        Location(M.CAT_SYSTEM_EXTENSION, SYS, f"{L}/SystemExtensions", WARN_ONLY, risk=E,
                 note="System extension — managed by systemextensionsctl; not removed."),
    ]


def _linux_user() -> List[Location]:
    return [
        Location(M.CAT_CONFIG, HOME, ".config", NAME_SCAN),
        Location(M.CAT_CACHES, HOME, ".cache", NAME_SCAN),
        Location(M.CAT_APP_SUPPORT, HOME, ".local/share", NAME_SCAN),
        Location(M.CAT_STATE, HOME, ".local/state", NAME_SCAN),
        Location(M.CAT_CONFIG, HOME, ".var/app", ID_SCAN,
                 note="Flatpak per-app data, keyed by the Flatpak application id."),
        Location(M.CAT_APP_SUPPORT, HOME, "snap", NAME_SCAN,
                 note="Snap per-app data directory."),
        Location(M.CAT_LOGIN_ITEM, HOME, ".config/autostart", NAME_SCAN, (".desktop",)),
        Location(M.CAT_LOGIN_ITEM, HOME, ".config/systemd/user", NAME_SCAN, (".service",)),
        Location(M.CAT_OTHER, HOME, ".local/share/applications", NAME_SCAN, (".desktop",)),
    ]


def _linux_system() -> List[Location]:
    E = M.RISK_ELEVATED
    return [
        Location(M.CAT_APP_BUNDLE, SYS, "opt", NAME_SCAN, risk=E, requires_root=True),
        Location(M.CAT_OTHER, SYS, "usr/share/applications", NAME_SCAN, (".desktop",), risk=E, requires_root=True),
    ]


def mac_locations() -> List[Location]:
    return _mac_user() + _mac_system()


def linux_locations() -> List[Location]:
    return _linux_user() + _linux_system()


def application_roots(system: str) -> List[Tuple[str, str]]:
    """Roots under which an app *bundle/binary* itself may legitimately live."""
    if system == "darwin":
        return [
            (SYS, "Applications"),
            (SYS, "Applications/Utilities"),
            (SYS, "Applications/Setapp"),
            (HOME, "Applications"),
        ]
    return [(SYS, "opt"), (SYS, "usr/local")]


def roots_for(locations: List[Location], system: str) -> Set[Tuple[str, str]]:
    """The set of ``(base, container)`` roots to allow-list for the guard."""
    roots: Set[Tuple[str, str]] = set()
    for loc in locations:
        roots.add((loc.base, loc.container))
    for base, sub in application_roots(system):
        roots.add((base, sub))
    return roots
