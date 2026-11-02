"""The ``zap`` command-line interface.

Design points that matter for agents:

* ``--json`` prints exactly one envelope to stdout and never prompts; stdin is
  read only when explicitly asked for with ``--plan -``.
* Removal is a **dry-run by default**; ``--yes`` is the only thing that makes
  ``uninstall`` actually act, and ``--purge`` (permanent) additionally
  requires ``--confirm-purge``.
* Exit codes and ``error.code`` are the stable, documented contract.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional, Set, Tuple

from . import SCHEMA_VERSION, __version__
from .core import history as history_mod
from .core import preflight
from .core.engine import Engine
from .core.envelope import (
    EXIT_BLOCKED,
    EXIT_ERROR,
    EXIT_ERROR_CODE,
    EXIT_MESSAGES,
    EXIT_NOTHING,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_USAGE,
    ZapError,
    envelope,
)
from .core.models import (
    ORPHANS_IDENTIFIER,
    STATUS_BLOCKED,
    STATUS_DRIFTED,
    STATUS_DRY_RUN,
    STATUS_FAILED,
    STATUS_REMOVED,
    Plan,
)
from .environment import current_environment
from .platforms.base import ResolveResult
from .render import Renderer


# How many guard-rejected paths are echoed on stdout; the -o file keeps all.
MAX_DROPPED_ON_STDOUT = 20


class Context:
    def __init__(self, env, as_json: bool, compact: bool = False):
        self.env = env
        self.json = as_json
        self.compact = compact
        self.engine = Engine(env)
        self.renderer = Renderer(env.is_tty)

    def ok(self, command: str, data, *, warnings=None, human: str = "") -> int:
        if self.json:
            sys.stdout.write(json.dumps(envelope(command, ok=True, data=data, warnings=warnings)) + "\n")
        elif human:
            sys.stdout.write(human + "\n")
        return EXIT_OK

    def emit(self, command: str, data, exit_code: int, *, warnings=None, human: str = "") -> int:
        if self.json:
            error = None
            if exit_code != EXIT_OK:
                code = EXIT_ERROR_CODE.get(exit_code, "internal_error")
                error = {"code": code, "message": EXIT_MESSAGES.get(exit_code, code), "details": {}}
            sys.stdout.write(json.dumps(envelope(command, ok=exit_code == EXIT_OK, data=data, warnings=warnings, error=error)) + "\n")
        elif human:
            sys.stdout.write(human + "\n")
        return exit_code

    def plan_dict(self, plan: Plan) -> dict:
        return plan.to_dict(compact=self.compact, max_dropped=MAX_DROPPED_ON_STDOUT)

    def fail(self, command: str, err: ZapError) -> int:
        if self.json:
            sys.stdout.write(json.dumps(envelope(command, ok=False, error=err.to_dict())) + "\n")
        else:
            sys.stderr.write(f"zap: {err.message}\n")
            details = err.details or {}
            for cand in details.get("candidates", []):
                sys.stderr.write(f"  - {cand}\n")
            for uid in details.get("unknown_ids", []):
                sys.stderr.write(f"  unknown id: {uid}\n")
        return err.exit_code


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    """Under --json a usage error must still be a JSON envelope, not argparse's
    plain-text message and a bare exit."""

    def error(self, message: str):  # type: ignore[override]
        raise ZapError("usage_error", message)


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="zap",
        description="Find and remove an app plus all of its leftover files. "
        "Cross-platform, agent-friendly, trash-first.",
    )
    p.add_argument("--version", action="version", version=f"zap {__version__}")
    p.add_argument("--json", action="store_true", help="emit one machine-readable JSON envelope")
    p.add_argument("--compact", action="store_true",
                   help="with --json: slim artifacts (no fingerprints/per-item notes); replayable with --plan, "
                        "but drift is then measured only against the fresh scan")
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("list", help="list discovered installed apps")
    sp.add_argument("--all", action="store_true", help="include protected/system apps")

    sp = sub.add_parser("scan", help="find an app's files without removing anything")
    sp.add_argument("app", help="app name, bundle id, or path")
    sp.add_argument("--aggressive", action="store_true", help="also select derived (prefix/vendor/embedded) matches")
    sp.add_argument("--include-low", action="store_true", help="also select name-only (heuristic) matches")
    sp.add_argument("-o", "--output", help="write the plan JSON to this file")

    sp = sub.add_parser("uninstall", help="remove an app and its files (dry-run unless --yes)")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("app", nargs="?", help="app name, bundle id, or path")
    g.add_argument("--plan", help="execute a previously-saved scan plan ('-' for stdin)")
    sp.add_argument("--yes", action="store_true", help="actually perform the removal (otherwise dry-run)")
    sp.add_argument("--dry-run", action="store_true", help="force a dry-run even with --yes")
    sp.add_argument("--purge", action="store_true", help="delete permanently instead of moving to trash")
    sp.add_argument("--confirm-purge", action="store_true",
                    help="required with --purge --yes: acknowledge that purged files cannot be undone")
    sp.add_argument("--aggressive", action="store_true", help="also act on derived matches")
    sp.add_argument("--include-low", action="store_true", help="also act on name-only matches")
    sp.add_argument("--include-system", action="store_true", help="also act on system/root artifacts")
    sp.add_argument("--skip-preflight", action="store_true",
                    help="attempt removal even when the permission preflight predicts failures")
    sp.add_argument("--only", action="append", default=[], help="act only on these artifact ids (comma-separated)")
    sp.add_argument("--except", dest="except_", action="append", default=[], help="exclude these artifact ids")
    sp.add_argument("--summary", action="store_true",
                    help="with --json: report counts and only the results needing attention")

    sp = sub.add_parser("orphans", help="find leftover files with no installed owner")
    sp.add_argument("-o", "--output", help="write the plan JSON to this file")
    sp.add_argument("--du", action="store_true",
                    help="print plain 'KB<TAB>path' lines (du -sk style) instead of the JSON/human plan; "
                         "root-only and shared artifacts are omitted")

    sub.add_parser("history", help="list past uninstall operations")

    sp = sub.add_parser("undo", help="restore a past operation from the trash")
    sp.add_argument("operation_id", help="operation id from `zap history`")

    sub.add_parser("capabilities", help="print schema/feature introspection for agents")
    return p


def _parse_ids(values: List[str]) -> Set[str]:
    out: Set[str] = set()
    for v in values or []:
        for part in v.replace(",", " ").split():
            if part:
                out.add(part)
    return out


# ---------------------------------------------------------------------------
# command handlers
# ---------------------------------------------------------------------------


def _resolve_or_raise(ctx: Context, selector: str):
    res: ResolveResult = ctx.engine.resolve(selector)
    if res.app is not None:
        return res.app
    if res.ambiguous:
        raise ZapError(
            "ambiguous_selector",
            f"'{selector}' matches {len(res.candidates)} apps; use an exact identifier.",
            {"candidates": [{"identifier": a.identifier, "path": str(a.path)} for a in res.candidates]},
        )
    raise ZapError("app_not_found", f"no installed app matches '{selector}'.", {"selector": selector})


def cmd_list(ctx: Context, args) -> int:
    apps = ctx.engine.list_apps()
    if not args.all:
        apps = [a for a in apps if not a.protected]
    data = {"apps": [a.to_dict() for a in apps], "count": len(apps)}
    return ctx.ok("list", data, human=ctx.renderer.apps(apps))


def cmd_scan(ctx: Context, args) -> int:
    app = _resolve_or_raise(ctx, args.app)
    plan = ctx.engine.scan(app, aggressive=args.aggressive, include_low=args.include_low)
    _maybe_write_plan(args, plan)
    return ctx.ok("scan", ctx.plan_dict(plan), warnings=plan.warnings, human=ctx.renderer.plan(plan, title="Scan"))


def cmd_orphans(ctx: Context, args) -> int:
    if getattr(args, "du", False) and ctx.json:
        # --du exists to break the JSON contract on purpose (plain KB<TAB>path
        # lines for du-style consumers); combined with --json the caller's
        # intent is ambiguous, so refuse rather than emit non-JSON on stdout.
        raise ZapError(
            "usage_error",
            "--du prints plain 'KB<TAB>path' lines and cannot honor --json; "
            "drop --du (the orphans JSON envelope already includes sizes).",
        )
    plan = ctx.engine.orphans()
    _maybe_write_plan(args, plan)
    if getattr(args, "du", False):
        # du -sk-compatible output for tooling (e.g. the Reclaim Space scanner):
        # one "KB<TAB>path" line per orphan, largest first. Root-only and shared
        # artifacts are omitted — a du consumer may feed these paths to a
        # trasher, and those tiers are never safe to remove unattended.
        for a in sorted(plan.artifacts, key=lambda a: a.size_bytes, reverse=True):
            if a.requires_root or a.shared:
                continue
            sys.stdout.write(f"{max(1, (a.size_bytes + 1023) // 1024)}\t{a.path}\n")
        return EXIT_OK
    return ctx.ok("orphans", ctx.plan_dict(plan), warnings=plan.warnings, human=ctx.renderer.plan(plan, title="Orphans"))


def _maybe_write_plan(args, plan: Plan) -> None:
    out = getattr(args, "output", None)
    if out:
        Path(out).write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")


def _load_plan(spec: str) -> Plan:
    """Parse a plan file. The result is *untrusted*: only its app identity and
    its artifact ids / selection are used (see :func:`_plan_selection`)."""
    try:
        if spec == "-":
            raw = sys.stdin.read()
        else:
            raw = Path(spec).read_text(encoding="utf-8")
    except OSError as exc:
        raise ZapError("usage_error", f"could not read plan: {exc}") from exc
    try:
        obj = json.loads(raw)
    except ValueError as exc:
        raise ZapError("usage_error", f"could not parse plan: {exc}") from exc
    # Accept either a bare plan dict or a full envelope wrapping one.
    if isinstance(obj, dict) and "artifacts" not in obj and isinstance(obj.get("data"), dict):
        obj = obj["data"]
    if not isinstance(obj, dict) or not isinstance(obj.get("artifacts"), list):
        raise ZapError("usage_error", "file does not contain a zap plan.")
    if int(obj.get("schema_version", 1) or 1) > SCHEMA_VERSION:
        raise ZapError(
            "usage_error",
            f"plan schema_version {obj.get('schema_version')} is newer than this zap ({SCHEMA_VERSION}).",
        )
    try:
        return Plan.from_dict(obj)
    except (KeyError, TypeError, ValueError) as exc:
        raise ZapError("usage_error", f"malformed plan: {type(exc).__name__}: {exc}") from exc


def _verify_plan_app(ctx: Context, plan: Plan):
    """A plan file is untrusted input: bind it back to a discovered app.

    The plan's ``app`` must resolve (by identifier) to an installed app at the
    same path, and that app must not be protected. Otherwise a fabricated plan
    could name an arbitrary directory as the "app".
    """
    claimed = plan.app
    claimed_path = os.path.normpath(os.path.abspath(str(claimed.path)))
    res: ResolveResult = ctx.engine.resolve(claimed.identifier)
    live = res.app
    if live is None and res.ambiguous:
        # Same id installed in two places: bind by the plan's path.
        for cand in res.candidates:
            if os.path.normpath(os.path.abspath(str(cand.path))) == claimed_path:
                live = cand
                break
    if live is None:
        raise ZapError(
            "plan_app_mismatch",
            f"plan names '{claimed.identifier}', which is not an installed app.",
            {"identifier": claimed.identifier},
        )
    live_path = os.path.normpath(os.path.abspath(str(live.path)))
    if live_path != claimed_path:
        raise ZapError(
            "plan_app_mismatch",
            f"plan path for '{claimed.identifier}' does not match the installed app.",
            {"identifier": claimed.identifier, "plan_path": claimed_path, "installed_path": live_path},
        )
    if live.protected:
        raise ZapError(
            "blocked_by_safety",
            f"'{live.name}' is a protected system app and will not be uninstalled.",
            {"app": live.identifier},
        )
    return live


def _plan_selection(ctx: Context, saved: Plan, args) -> Tuple[Plan, Set[str], List[dict]]:
    """Turn a saved plan into (fresh plan, selected ids, notes).

    A plan is never a source of *paths*. zap re-scans the app now and acts only
    on artifacts that the fresh scan attributes to it AND whose ids the plan
    selected. Saved fingerprints, when present, are carried onto the fresh
    artifacts so drift since the plan was written is still detected.
    """
    notes: List[dict] = []
    if args.aggressive or args.include_low:
        raise ZapError(
            "usage_error",
            "--aggressive/--include-low are scan-time options; with --plan the plan's own "
            "selection is used (re-scan with those flags and save a new plan).",
        )
    if saved.app.identifier == ORPHANS_IDENTIFIER:
        fresh = ctx.engine.orphans()
    else:
        live = _verify_plan_app(ctx, saved)
        fresh = ctx.engine.scan(live)
    saved_by_id = {a.id: a for a in saved.artifacts}
    fresh_by_id = {a.id: a for a in fresh.artifacts}
    stale = sorted(i for i in saved_by_id if i not in fresh_by_id)
    if stale:
        notes.append({"code": "plan_stale_ids", "ids": stale,
                      "message": f"{len(stale)} plan artifact(s) are no longer attributed to the app and were skipped."})
    for aid, fresh_art in fresh_by_id.items():
        old = saved_by_id.get(aid)
        if old is not None and old.fingerprint is not None and old.fingerprint.exists:
            fresh_art.fingerprint = old.fingerprint  # drift since the plan was saved
    selected_ids = {i for i, a in saved_by_id.items() if a.auto_selected and i in fresh_by_id}
    return fresh, selected_ids, notes


def _parse_ids_strict(values: List[str], flag: str) -> Optional[Set[str]]:
    if not values:
        return None
    ids = _parse_ids(values)
    if not ids:
        raise ZapError("usage_error", f"{flag} was given but contains no artifact ids.")
    return ids


def cmd_uninstall(ctx: Context, args) -> int:
    plan_notes: List[dict] = []
    if args.plan:
        saved = _load_plan(args.plan)
        plan, plan_ids, plan_notes = _plan_selection(ctx, saved, args)
        app = plan.app
    else:
        res = ctx.engine.resolve(args.app)
        if res.app is not None and res.fuzzy and args.yes:
            raise ZapError(
                "ambiguous_selector",
                f"'{args.app}' only matches '{res.app.identifier}' by substring; "
                "use the exact name, bundle id or path with --yes.",
                {"candidates": [{"identifier": res.app.identifier, "path": str(res.app.path)}]},
            )
        app = _resolve_or_raise(ctx, args.app)
        if app.protected:
            raise ZapError(
                "blocked_by_safety",
                f"'{app.name}' is a protected system app and will not be uninstalled.",
                {"app": app.identifier},
            )
        plan = ctx.engine.scan(app, aggressive=args.aggressive, include_low=args.include_low)
        plan_ids = None

    method = "purge" if args.purge else "trash"
    dry_run = (not args.yes) or args.dry_run
    if method == "purge" and not dry_run and not args.confirm_purge:
        raise ZapError(
            "confirmation_required",
            "--purge deletes permanently and cannot be undone; add --confirm-purge to proceed.",
            {"required_flag": "--confirm-purge"},
        )

    only = _parse_ids_strict(args.only, "--only")
    exclude = _parse_ids_strict(args.except_, "--except")
    if only is None and plan_ids is not None:
        only = plan_ids
        if not only:
            only = {"__none__"}  # plan selected nothing; select() reports it unknown -> handled below
    selected, unknown, notes = ctx.engine.select(
        plan, only=only, exclude=exclude,
        include_system=args.include_system, method=method,
    )
    unknown = [u for u in unknown if u != "__none__"]
    if exclude:
        by_id = {a.id for a in plan.artifacts}
        unknown += [i for i in exclude if i not in by_id]
    if unknown:
        raise ZapError("unknown_ids", "one or more artifact ids are not in the plan.",
                       {"unknown_ids": sorted(set(unknown))})
    notes = sorted(plan_notes + notes, key=lambda n: json.dumps(n, sort_keys=True))

    if not selected:
        data = {
            "app": app.to_dict(),
            "plan_id": plan.plan_id,
            "method": method,
            "dry_run": dry_run,
            "results": [],
            "summary": {"counts": {}, "bytes_reclaimed": 0},
            "selection_notes": notes,
        }
        human = ctx.renderer.plan(plan, title="Nothing selected to remove")
        return ctx.emit("uninstall", data, EXIT_NOTHING, warnings=plan.warnings, human=human)

    if not dry_run and not args.skip_preflight:
        blockers = preflight.check(ctx.env, selected)
        if blockers:
            raise ZapError(
                "permission_required",
                "insufficient permissions for the current selection; nothing was removed. "
                "Fix the items below, deselect them with --except, or pass --skip-preflight "
                "to attempt anyway.",
                {"blockers": blockers},
            )

    try:
        report = ctx.engine.execute(plan, selected, method=method, dry_run=dry_run)
    except history_mod.LockBusy as exc:
        raise ZapError("locked", str(exc)) from exc

    data = {
        "app": app.to_dict(),
        "plan_id": plan.plan_id,
        **report.to_dict(summary_only=bool(args.summary), compact=ctx.compact),
        "selection_notes": notes,
    }
    warnings = [_cap_dropped_warning(w) for w in plan.warnings]
    if dry_run:
        warnings.append({"code": "dry_run", "message": "Dry run — re-run with --yes to apply."})

    exit_code = _uninstall_exit_code(report, dry_run)

    human = ctx.renderer.execution(report, app_name=app.name)
    if dry_run:
        human += "\n" + ctx.renderer.s.dim("  (dry run — add --yes to apply)")
    return ctx.emit("uninstall", data, exit_code, warnings=warnings, human=human)


def _uninstall_exit_code(report, dry_run: bool) -> int:
    """The documented contract (same for dry and wet runs):

    * every result blocked                      -> 6
    * nothing acted on, some failed/drifted     -> 5
    * nothing acted on, all skipped             -> 4
    * acted on some, any failed/drifted/blocked -> 5
    * otherwise                                 -> 0
    """
    statuses = {r.status for r in report.results}
    acted = STATUS_REMOVED in statuses or STATUS_DRY_RUN in statuses
    troubled = bool(statuses & {STATUS_FAILED, STATUS_DRIFTED, STATUS_BLOCKED})
    if statuses and statuses <= {STATUS_BLOCKED}:
        return EXIT_BLOCKED  # the guard refused everything, wet or dry
    if not acted:
        # Nothing removed/previewed: total failure or drift is 5, not 4, so an
        # agent can tell "every item failed" from "nothing was selected".
        if statuses & {STATUS_FAILED, STATUS_DRIFTED}:
            return EXIT_PARTIAL
        return EXIT_NOTHING
    if troubled:
        return EXIT_PARTIAL
    return EXIT_OK


def _cap_dropped_warning(w: dict) -> dict:
    from .core.models import _cap_dropped

    return _cap_dropped(w, MAX_DROPPED_ON_STDOUT)


def cmd_history(ctx: Context, args) -> int:
    ops = history_mod.list_operations(ctx.env)
    data = {"operations": [o.to_dict() for o in ops], "count": len(ops)}
    return ctx.ok("history", data, human=ctx.renderer.history(ops))


def cmd_undo(ctx: Context, args) -> int:
    if not history_mod.is_valid_operation_id(args.operation_id):
        raise ZapError("usage_error", f"'{args.operation_id}' is not an operation id (see `zap history`).")
    try:
        with history_mod.exclusive_lock(ctx.env):
            results = history_mod.undo_operation(ctx.env, args.operation_id)
    except history_mod.LockBusy as exc:
        raise ZapError("locked", str(exc)) from exc
    except FileNotFoundError as exc:
        raise ZapError("unknown_operation", f"no such operation: {args.operation_id}") from exc
    failed = [r for r in results if r["status"] == "failed"]
    restored = [r for r in results if r["status"] == "restored"]
    data = {"operation_id": args.operation_id, "results": results}
    if failed:
        code = EXIT_PARTIAL
    elif not restored:
        code = EXIT_NOTHING
    else:
        code = EXIT_OK
    return ctx.emit("undo", data, code, human=ctx.renderer.undo(results))


def cmd_capabilities(ctx: Context, args) -> int:
    from .core import envelope as E
    from .core import models as M

    data = {
        "zap_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "platform": ctx.env.system,
        "commands": ["list", "scan", "uninstall", "orphans", "history", "undo", "capabilities"],
        "exit_codes": {
            "ok": E.EXIT_OK, "error": E.EXIT_ERROR, "usage": E.EXIT_USAGE,
            "app_not_found": E.EXIT_APP_NOT_FOUND, "nothing_to_remove": E.EXIT_NOTHING,
            "partial_failure": E.EXIT_PARTIAL, "blocked_by_safety": E.EXIT_BLOCKED,
            "confirmation_required": E.EXIT_CONFIRMATION, "ambiguous": E.EXIT_AMBIGUOUS,
            "locked": E.EXIT_LOCKED,
        },
        "error_codes": sorted(E.ERROR_EXIT.keys()),
        "confidence_levels": [M.CONFIDENCE_EXACT, M.CONFIDENCE_DERIVED, M.CONFIDENCE_HEURISTIC],
        "risk_levels": [M.RISK_SAFE, M.RISK_NORMAL, M.RISK_ELEVATED],
        "categories": sorted(M.CATEGORY_RISK.keys()),
        "defaults": {
            "removal": "dry-run unless --yes",
            "method": "trash (recoverable) unless --purge (which also needs --confirm-purge)",
            "auto_selected": "full-bundle-id exact matches only",
        },
    }
    human = json.dumps(data, indent=2)
    return ctx.ok("capabilities", data, human=human)


HANDLERS = {
    "list": cmd_list,
    "scan": cmd_scan,
    "uninstall": cmd_uninstall,
    "orphans": cmd_orphans,
    "history": cmd_history,
    "undo": cmd_undo,
    "capabilities": cmd_capabilities,
}


def main(argv: Optional[List[str]] = None, env=None) -> int:
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse_args(raw_argv)
    except ZapError as err:
        if "--json" in raw_argv:
            sys.stdout.write(json.dumps(envelope("usage", ok=False, error=err.to_dict())) + "\n")
        else:
            sys.stderr.write(f"zap: {err.message}\n")
            parser.print_usage(sys.stderr)
        return err.exit_code
    if not args.command:
        parser.print_help(sys.stderr)
        return EXIT_USAGE

    if env is None:
        env = current_environment()
    ctx = Context(env, as_json=bool(args.json), compact=bool(args.compact))
    handler = HANDLERS[args.command]
    try:
        return handler(ctx, args)
    except ZapError as err:
        return ctx.fail(args.command, err)
    except BrokenPipeError:  # pragma: no cover
        try:
            sys.stdout.close()
        except Exception:
            pass
        return EXIT_OK
    except KeyboardInterrupt:  # pragma: no cover
        sys.stderr.write("\nzap: interrupted\n")
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - last-resort structured error
        return ctx.fail(args.command, ZapError("internal_error", f"{type(exc).__name__}: {exc}"))


if __name__ == "__main__":
    sys.exit(main())
