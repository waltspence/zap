"""Human-readable rendering for the terminal (color only on a real TTY)."""

from __future__ import annotations

import os

from .core import fsutil
from .core.models import (
    CONFIDENCE_DERIVED,
    CONFIDENCE_EXACT,
    RISK_ELEVATED,
    RISK_SAFE,
    STATUS_BLOCKED,
    STATUS_DRIFTED,
    STATUS_DRY_RUN,
    STATUS_FAILED,
    STATUS_REMOVED,
    STATUS_SKIPPED,
    Plan,
)


class Style:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\x1b[{code}m{text}\x1b[0m"

    def bold(self, t): return self._wrap("1", t)
    def dim(self, t): return self._wrap("2", t)
    def red(self, t): return self._wrap("31", t)
    def green(self, t): return self._wrap("32", t)
    def yellow(self, t): return self._wrap("33", t)
    def blue(self, t): return self._wrap("34", t)
    def cyan(self, t): return self._wrap("36", t)


def color_enabled(is_tty: bool) -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return is_tty


class Renderer:
    def __init__(self, is_tty: bool):
        self.s = Style(color_enabled(is_tty))

    # -- apps --------------------------------------------------------------

    def apps(self, apps) -> str:
        if not apps:
            return "No applications discovered."
        rows = []
        for a in apps:
            ident = a.identifier
            ver = a.version or ""
            tag = ""
            if a.protected:
                tag = self.s.dim(" (protected)")
            elif a.managed_by:
                tag = self.s.dim(f" ({a.managed_by})")
            rows.append((a.name, ident, ver, str(a.path) + tag))
        widths = [max(len(str(r[i])) for r in rows) for i in range(3)]
        header = self.s.bold(
            f"{'NAME'.ljust(widths[0])}  {'IDENTIFIER'.ljust(widths[1])}  {'VERSION'.ljust(widths[2])}  PATH"
        )
        lines = [header]
        for name, ident, ver, path in rows:
            lines.append(
                f"{name.ljust(widths[0])}  {self.s.cyan(ident.ljust(widths[1]))}  {ver.ljust(widths[2])}  {self.s.dim(path)}"
            )
        lines.append("")
        lines.append(self.s.dim(f"{len(apps)} app(s)."))
        return "\n".join(lines)

    def candidates(self, candidates) -> str:
        lines = [self.s.yellow("Multiple apps match. Re-run with an exact identifier:")]
        for a in candidates:
            lines.append(f"  {self.s.cyan(a.identifier)}  {self.s.dim(str(a.path))}")
        return "\n".join(lines)

    # -- plan --------------------------------------------------------------

    def _risk_label(self, risk: str) -> str:
        if risk == RISK_SAFE:
            return self.s.green(risk)
        if risk == RISK_ELEVATED:
            return self.s.red(risk)
        return self.s.yellow(risk)

    def _conf_label(self, conf: str) -> str:
        if conf == CONFIDENCE_EXACT:
            return self.s.green(conf)
        if conf == CONFIDENCE_DERIVED:
            return self.s.yellow(conf)
        return self.s.dim(conf)

    def plan(self, plan: Plan, *, title: str = "Uninstall plan") -> str:
        a = plan.app
        lines = [self.s.bold(f"{title}: {a.name}")]
        meta = f"  {self.s.cyan(a.identifier)}"
        if a.version:
            meta += self.s.dim(f"  v{a.version}")
        meta += self.s.dim(f"  {a.path}")
        lines.append(meta)
        lines.append(self.s.dim(f"  plan_id {plan.plan_id}"))
        lines.append("")

        if not plan.artifacts:
            lines.append("  No associated files found.")
        else:
            for art in plan.artifacts:
                mark = self.s.green("[x]") if art.auto_selected else self.s.dim("[ ]")
                flags = []
                if art.shared:
                    flags.append(self.s.yellow("shared"))
                if art.requires_root:
                    flags.append(self.s.red("root"))
                if not art.reversible:
                    flags.append(self.s.red("keep-only"))
                flagstr = (" " + " ".join(flags)) if flags else ""
                lines.append(
                    f"  {mark} {self.s.dim(art.id)}  "
                    f"{fsutil.human_size(art.size_bytes).rjust(9)}  "
                    f"{art.category.ljust(20)}  "
                    f"{self._conf_label(art.match.confidence).ljust(20)}  "
                    f"{self._risk_label(art.risk)}{flagstr}"
                )
                lines.append(f"        {self.s.dim(str(art.path))}")
                for note in art.notes:
                    lines.append(f"        {self.s.yellow('! ' + note)}")

        s = plan.summary()
        lines.append("")
        lines.append(
            self.s.bold(
                f"  {s['auto_selected']} selected of {s['total_artifacts']} "
                f"({fsutil.human_size(s['selected_bytes'])} of {fsutil.human_size(s['total_bytes'])})"
            )
        )
        for w in plan.warnings:
            lines.append(self.s.yellow(f"  ! {w.get('message', w.get('code'))}"))
        return "\n".join(lines)

    # -- execution ---------------------------------------------------------

    def _status_label(self, status: str) -> str:
        colors = {
            STATUS_REMOVED: self.s.green,
            STATUS_DRY_RUN: self.s.cyan,
            STATUS_SKIPPED: self.s.dim,
            STATUS_DRIFTED: self.s.yellow,
            STATUS_BLOCKED: self.s.red,
            STATUS_FAILED: self.s.red,
        }
        fn = colors.get(status, self.s.dim)
        return fn(status)

    def execution(self, report, *, app_name: str = "") -> str:
        verb = "Would remove" if report.dry_run else "Removed"
        head = f"{verb} ({report.method})"
        if app_name:
            head += f" — {app_name}"
        lines = [self.s.bold(head)]
        for r in report.results:
            extra = ""
            if r.reason:
                extra = self.s.dim(f"  ({r.reason})")
            lines.append(
                f"  {self._status_label(r.status).ljust(18)} "
                f"{fsutil.human_size(r.size_bytes).rjust(9)}  {self.s.dim(str(r.path))}{extra}"
            )
        s = report.summary()
        lines.append("")
        counts = ", ".join(f"{k}={v}" for k, v in sorted(s["counts"].items()))
        lines.append(self.s.bold(f"  {counts}  |  {fsutil.human_size(s['bytes_reclaimed'])}"))
        if report.operation_id:
            lines.append(self.s.dim(f"  operation_id {report.operation_id}  (undo: zap undo {report.operation_id})"))
        return "\n".join(lines)

    # -- history / undo ----------------------------------------------------

    def history(self, ops) -> str:
        if not ops:
            return "No uninstall history."
        lines = [self.s.bold("Uninstall history:")]
        for op in ops:
            h = op.header
            lines.append(
                f"  {self.s.cyan(op.operation_id)}  {h.get('app_name', h.get('app',''))}  "
                f"{self.s.dim(h.get('created_at',''))}  "
                f"{op.reversible_count}/{len(op.records)} reversible  ({h.get('method','')})"
            )
        return "\n".join(lines)

    def undo(self, results) -> str:
        lines = [self.s.bold("Undo:")]
        for r in results:
            fn = self.s.green if r["status"] == "restored" else self.s.red
            extra = self.s.dim(f"  ({r.get('reason')})") if r.get("reason") else ""
            lines.append(f"  {fn(r['status']).ljust(20)} {self.s.dim(r['original'])}{extra}")
        return "\n".join(lines)
