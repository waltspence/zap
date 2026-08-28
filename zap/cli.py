"""The ``zap`` command-line interface.

Design points that matter for agents:

* ``--json`` prints exactly one envelope to stdout and never reads stdin or
  prompts, so an autonomous caller can never hang.
* Removal is a **dry-run by default**; ``--yes`` is the only thing that makes
  ``uninstall`` actually act, and ``--purge`` (permanent) still requires it.
* Exit codes and ``error.code`` are the stable, documented contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Set

from . import SCHEMA_VERSION, __version__
from .core import history as history_mod
from .core.engine import Engine
from .core.envelope import (
    EXIT_ERROR,
    EXIT_NOTHING,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_USAGE,
    ZapError,
    envelope,
)
from .core.models import Plan
from .environment import current_environment
from .platforms.base import ResolveResult
from .render import Renderer


class Context:
    def __init__(self, env, as_json: bool):
        self.env = env
        self.json = as_json
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
            sys.stdout.write(json.dumps(envelope(command, ok=exit_code == EXIT_OK, data=data, warnings=warnings)) + "\n")
        elif human:
            sys.stdout.write(human + "\n")
        return exit_code

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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zap",
        description="Find and remove an app plus all of its leftover files. "
        "Cross-platform, agent-friendly, trash-first.",
    )
    p.add_argument("--version", action="version", version=f"zap {__version__}")
    p.add_argument("--json", action="store_true", help="emit one machine-readable JSON envelope")
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
    sp.add_argument("--aggressive", action="store_true", help="also act on derived matches")
    sp.add_argument("--include-low", action="store_true", help="also act on name-only matches")
    sp.add_argument("--include-system", action="store_true", help="also act on system/root artifacts")
    sp.add_argument("--only", action="append", default=[], help="act only on these artifact ids (comma-separated)")
    sp.add_argument("--except", dest="except_", action="append", default=[], help="exclude these artifact ids")

    sp = sub.add_parser("orphans", help="find leftover files with no installed owner")
    sp.add_argument("-o", "--output", help="write the plan JSON to this file")

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
            {"candidates": [a.identifier for a in res.candidates],
             "candidate_details": [a.to_dict() for a in res.candidates]},
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
    return ctx.ok("scan", plan.to_dict(), warnings=plan.warnings, human=ctx.renderer.plan(plan, title="Scan"))


def cmd_orphans(ctx: Context, args) -> int:
    plan = ctx.engine.orphans()
    _maybe_write_plan(args, plan)
    return ctx.ok("orphans", plan.to_dict(), human=ctx.renderer.plan(plan, title="Orphans"))


def _maybe_write_plan(args, plan: Plan) -> None:
    out = getattr(args, "output", None)
    if out:
        Path(out).write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")


def _load_plan(spec: str) -> Plan:
    if spec == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(spec).read_text(encoding="utf-8")
    try:
        obj = json.loads(raw)
    except ValueError as exc:
        raise ZapError("usage_error", f"could not parse plan: {exc}") from exc
    # Accept either a bare plan dict or a full envelope wrapping one.
    if isinstance(obj, dict) and "artifacts" not in obj and isinstance(obj.get("data"), dict):
        obj = obj["data"]
    if not isinstance(obj, dict) or "artifacts" not in obj:
        raise ZapError("usage_error", "file does not contain a zap plan.")
    return Plan.from_dict(obj)


def cmd_uninstall(ctx: Context, args) -> int:
    if args.plan:
        plan = _load_plan(args.plan)
        app = plan.app
    else:
        app = _resolve_or_raise(ctx, args.app)
        if app.protected:
            raise ZapError(
                "blocked_by_safety",
                f"'{app.name}' is a protected system app and will not be uninstalled.",
                {"app": app.identifier},
            )
        plan = ctx.engine.scan(app, aggressive=args.aggressive, include_low=args.include_low)

    method = "purge" if args.purge else "trash"
    dry_run = (not args.yes) or args.dry_run

    only = _parse_ids(args.only)
    exclude = _parse_ids(args.except_)
    selected, unknown, notes = ctx.engine.select(
        plan, only=only or None, exclude=exclude or None,
        include_system=args.include_system, method=method,
    )
    if unknown:
        raise ZapError("unknown_ids", "one or more artifact ids are not in the plan.",
                       {"unknown_ids": sorted(unknown)})

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

    try:
        report = ctx.engine.execute(plan, selected, method=method, dry_run=dry_run)
    except history_mod.LockBusy as exc:
        raise ZapError("locked", str(exc)) from exc

    data = {
        "app": app.to_dict(),
        "plan_id": plan.plan_id,
        **report.to_dict(),
        "selection_notes": notes,
    }
    warnings = list(plan.warnings)
    if dry_run and not args.plan:
        warnings.append({"code": "dry_run", "message": "Dry run — re-run with --yes to apply."})

    exit_code = EXIT_OK
    if not dry_run:
        if report.has_failures():
            exit_code = EXIT_PARTIAL
        elif not report.removed_any():
            exit_code = EXIT_NOTHING

    human = ctx.renderer.execution(report, app_name=app.name)
    if dry_run:
        human += "\n" + ctx.renderer.s.dim("  (dry run — add --yes to apply)")
    return ctx.emit("uninstall", data, exit_code, warnings=warnings, human=human)


def cmd_history(ctx: Context, args) -> int:
    ops = history_mod.list_operations(ctx.env)
    data = {"operations": [o.to_dict() for o in ops], "count": len(ops)}
    return ctx.ok("history", data, human=ctx.renderer.history(ops))


def cmd_undo(ctx: Context, args) -> int:
    try:
        results = history_mod.undo_operation(ctx.env, args.operation_id)
    except FileNotFoundError as exc:
        raise ZapError("unknown_operation", f"no such operation: {args.operation_id}") from exc
    failed = [r for r in results if r["status"] == "failed"]
    data = {"operation_id": args.operation_id, "results": results}
    code = EXIT_PARTIAL if failed else EXIT_OK
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
            "method": "trash (recoverable) unless --purge",
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
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help(sys.stderr)
        return EXIT_USAGE

    if env is None:
        env = current_environment()
    ctx = Context(env, as_json=bool(args.json))
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
