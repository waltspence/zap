"""zap-gui: the lightest possible desktop front-end for ``zap``.

Pure stdlib (tkinter), no third-party packages, ~300 lines. The GUI never
touches files itself: every action shells out to ``python -m zap --json``
so the CLI's safety checks, tiers, trash and undo log stay in charge.

Icon: place a ``icon.gif`` (any Tk) or ``icon.png`` (Tk 8.6+) next to this
file, or at ``~/.config/zap/icon.{gif,png}``. Otherwise a built-in bolt is
drawn.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Optional

TIER_LABEL = {"exact": "exact", "derived": "derived", "heuristic": "name-only"}


def human(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024 or unit == "GB":
            return f"{x:.0f} {unit}" if unit == "B" else f"{x:.1f} {unit}"
        x /= 1024
    return f"{x:.1f} GB"


def run_zap(args: List[str]) -> Dict[str, Any]:
    """Run ``zap --json <args>`` and return the envelope (never raises)."""
    cmd = [sys.executable, "-m", "zap", "--json", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": {"code": "spawn_failed", "message": str(exc)}}
    out = proc.stdout.strip() or proc.stderr.strip()
    try:
        return json.loads(out)
    except ValueError:
        return {"ok": False, "error": {"code": "bad_output", "message": out[:500] or f"exit {proc.returncode}"}}


# ---------------------------------------------------------------- icon


def _bolt_icon(size: int = 64) -> tk.PhotoImage:
    """Draw a lightning bolt on a dark disc as a PPM (works on Tk 8.5)."""
    bg, disc, bolt = (30, 30, 34), (52, 52, 60), (255, 204, 0)
    poly = [(0.58, 0.08), (0.30, 0.55), (0.48, 0.55), (0.40, 0.92), (0.72, 0.42), (0.54, 0.42)]
    pts = [(x * size, y * size) for x, y in poly]

    def inside(px: float, py: float) -> bool:
        hit = False
        j = len(pts) - 1
        for i, (xi, yi) in enumerate(pts):
            xj, yj = pts[j]
            if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi + 1e-9) + xi:
                hit = not hit
            j = i
        return hit

    c, r2 = size / 2, (size / 2 - 1) ** 2
    rows = bytearray()
    for y in range(size):
        for x in range(size):
            if inside(x + 0.5, y + 0.5):
                rows += bytes(bolt)
            elif (x - c) ** 2 + (y - c) ** 2 <= r2:
                rows += bytes(disc)
            else:
                rows += bytes(bg)
    data = b"P6 %d %d 255\n" % (size, size) + bytes(rows)
    return tk.PhotoImage(data=data, format="ppm")


def load_icon() -> Optional[tk.PhotoImage]:
    here = Path(__file__).parent
    cfg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "zap"
    for name in ("icon.gif", "icon.png"):
        for d in (here, cfg):
            p = d / name
            if p.is_file():
                try:
                    return tk.PhotoImage(file=str(p))
                except tk.TclError:
                    pass
    try:
        return _bolt_icon()
    except tk.TclError:
        return None


# ---------------------------------------------------------------- app


class ZapGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Zap")
        root.minsize(760, 480)
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.apps: List[dict] = []
        self.plan: Optional[dict] = None
        self.checked: Dict[str, bool] = {}
        self.busy = False
        self.last_msg = ""

        self.icon = load_icon()
        if self.icon is not None:
            try:
                root.iconphoto(True, self.icon)
            except tk.TclError:
                pass

        self._build()
        self.refresh_apps()
        root.after(100, self._pump)

    # -- layout ---------------------------------------------------------
    def _build(self) -> None:
        pw = ttk.Panedwindow(self.root, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=8, pady=(8, 0))

        left = ttk.Frame(pw)
        pw.add(left, weight=1)
        self.filter = tk.StringVar()
        self.filter.trace_add("write", lambda *_: self._fill_apps())
        ttk.Entry(left, textvariable=self.filter).pack(fill="x")
        self.applist = tk.Listbox(left, exportselection=False, activestyle="none")
        self.applist.pack(fill="both", expand=True, pady=(4, 0))
        self.applist.bind("<<ListboxSelect>>", lambda _e: self.scan())

        right = ttk.Frame(pw)
        pw.add(right, weight=3)
        self.title = ttk.Label(right, text="Select an app", font=("", 13, "bold"))
        self.title.pack(anchor="w")
        cols = ("sel", "size", "kind", "match", "path")
        self.tree = ttk.Treeview(right, columns=cols, show="headings", selectmode="browse")
        for c, w, txt in (("sel", 28, ""), ("size", 72, "Size"), ("kind", 130, "Kind"),
                          ("match", 80, "Match"), ("path", 380, "Path")):
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="w", stretch=(c == "path"))
        self.tree.tag_configure("risk", foreground="#b00020")
        self.tree.tag_configure("off", foreground="#888888")
        self.tree.pack(fill="both", expand=True, pady=(4, 0))
        self.tree.bind("<Button-1>", self._toggle_click)
        self.tree.bind("<space>", lambda _e: self._toggle_selected())

        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=8, pady=8)
        self.aggressive = tk.BooleanVar(value=False)
        self.low = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Aggressive", variable=self.aggressive, command=self.scan).pack(side="left")
        ttk.Checkbutton(bar, text="Include name-only", variable=self.low, command=self.scan).pack(side="left", padx=(6, 0))
        self.status = ttk.Label(bar, text="", anchor="w")
        self.status.pack(side="left", fill="x", expand=True, padx=10)
        self.btn_go = ttk.Button(bar, text="Move to Trash", command=self.uninstall, state="disabled")
        self.btn_go.pack(side="right")
        ttk.Button(bar, text="Undo…", command=self.undo).pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="Refresh", command=self.refresh_apps).pack(side="right", padx=(0, 6))

    # -- background plumbing -------------------------------------------
    def _bg(self, tag: str, args: List[str]) -> None:
        if self.busy:
            return
        self.busy = True
        self.status.config(text="Working…")
        threading.Thread(target=lambda: self.q.put((tag, run_zap(args))), daemon=True).start()

    def _pump(self) -> None:
        try:
            while True:
                tag, env = self.q.get_nowait()
                self.busy = False
                getattr(self, f"_on_{tag}")(env)
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def _fail(self, env: dict) -> None:
        err = env.get("error") or {}
        self.status.config(text=f"{err.get('code', 'error')}: {err.get('message', '')}")

    # -- apps -----------------------------------------------------------
    def refresh_apps(self) -> None:
        self._bg("list", ["list"])

    def _on_list(self, env: dict) -> None:
        if not env.get("ok"):
            return self._fail(env)
        self.apps = [a for a in env["data"]["apps"] if not a.get("protected")]
        self.apps.sort(key=lambda a: (a.get("display_name") or a["name"]).lower())
        self._fill_apps()
        self.status.config(text=self.last_msg or f"{len(self.apps)} apps")
        self.last_msg = ""

    def _fill_apps(self) -> None:
        f = self.filter.get().lower()
        self.applist.delete(0, "end")
        self.visible = [a for a in self.apps if f in (a.get("display_name") or a["name"]).lower()
                        or f in (a.get("bundle_id") or "").lower()]
        for a in self.visible:
            self.applist.insert("end", a.get("display_name") or a["name"])

    def _current_app(self) -> Optional[dict]:
        sel = self.applist.curselection()
        return self.visible[sel[0]] if sel else None

    # -- scan -----------------------------------------------------------
    def scan(self) -> None:
        app = self._current_app()
        if app is None:
            return
        args = ["scan", app["identifier"]]
        if self.aggressive.get():
            args.append("--aggressive")
        if self.low.get():
            args.append("--include-low")
        self._bg("scan", args)

    def _on_scan(self, env: dict) -> None:
        if not env.get("ok"):
            self.plan = None
            self.tree.delete(*self.tree.get_children())
            self.btn_go.config(state="disabled")
            return self._fail(env)
        self.plan = env["data"]
        self.btn_go.config(text="Move to Trash", command=self.uninstall)
        app = self.plan["app"]
        self.title.config(text=f"{app.get('display_name') or app['name']}  ·  {app.get('bundle_id') or ''}  v{app.get('version') or '?'}")
        self.checked = {a["id"]: bool(a["auto_selected"]) for a in self.plan["artifacts"]}
        self._fill_tree()
        for w in self.plan.get("warnings", []):
            if w.get("code") != "dropped_by_safety":
                self.status.config(text=f"warning: {w.get('code')}")

    def _fill_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        assert self.plan is not None
        for a in self.plan["artifacts"]:
            on = self.checked[a["id"]]
            tags = []
            if a.get("requires_root") or a.get("shared") or a.get("risk") == "high":
                tags.append("risk")
            if not on:
                tags.append("off")
            self.tree.insert("", "end", iid=a["id"], tags=tags, values=(
                "☑" if on else "☐", human(a["size_bytes"]), a["category"].replace("_", " "),
                TIER_LABEL.get(a["match"]["confidence"], a["match"]["confidence"]), a["path"]))
        self._update_footer()

    def _update_footer(self) -> None:
        assert self.plan is not None
        sel = [a for a in self.plan["artifacts"] if self.checked[a["id"]]]
        total = sum(a["size_bytes"] for a in sel)
        self.status.config(text=f"{len(sel)} of {len(self.plan['artifacts'])} selected · {human(total)}")
        self.btn_go.config(state="normal" if sel else "disabled")

    def _toggle_click(self, event: tk.Event) -> None:
        if self.tree.identify_column(event.x) == "#1":
            iid = self.tree.identify_row(event.y)
            if iid and self.plan is not None:
                self._toggle(iid)

    def _toggle_selected(self) -> None:
        if self.plan is None:
            return
        for iid in self.tree.selection():
            self._toggle(iid)

    def _toggle(self, iid: str) -> None:
        self.checked[iid] = not self.checked[iid]
        self._fill_tree()
        self.tree.selection_set(iid)

    # -- act ------------------------------------------------------------
    def uninstall(self) -> None:
        if self.plan is None:
            return
        ids = [i for i, on in self.checked.items() if on]
        if not ids:
            return
        app = self.plan["app"]
        name = app.get("display_name") or app["name"]
        if not messagebox.askokcancel("Move to Trash", f"Move {len(ids)} items of {name} to the Trash?\n\nYou can undo this from the Undo… button."):
            return
        args = ["uninstall", app["identifier"], "--yes", "--include-low", "--aggressive", "--only", ",".join(ids)]
        self._bg("uninstall", args)

    def _on_uninstall(self, env: dict) -> None:
        if not env.get("ok") and not env.get("data"):
            return self._fail(env)
        d = env["data"]
        counts = d.get("counts", {})
        bad = [r for r in d.get("results", []) if r["status"] not in ("removed", "dry_run")]
        msg = f"{counts.get('removed', 0)} removed · {human(d.get('bytes_reclaimed', 0))} reclaimed"
        if bad:
            msg += f" · {len(bad)} need attention"
            messagebox.showwarning("Some items were not removed",
                                   "\n".join(f"{r['status']}: {r['path']}\n  {r.get('reason') or ''}" for r in bad[:12]))
        self.last_msg = msg
        self.refresh_apps()
        self.plan = None
        self.tree.delete(*self.tree.get_children())
        self.title.config(text=msg)
        self.btn_go.config(state="disabled")

    # -- undo -----------------------------------------------------------
    def undo(self) -> None:
        self._bg("history", ["history"])

    def _on_history(self, env: dict) -> None:
        if not env.get("ok"):
            return self._fail(env)
        ops = env["data"]["operations"]
        self.plan = None
        self.ops = {op["operation_id"]: op for op in ops}
        self.tree.delete(*self.tree.get_children())
        self.title.config(text="Undo — pick an operation, then Restore")
        for op in ops:
            self.tree.insert("", "end", iid=op["operation_id"], values=(
                "", f"{op['reversible_count']}/{op['item_count']}", op["method"],
                op["created_at"][:16].replace("T", " "), op["app_name"]))
        self.status.config(text=f"{len(ops)} operations" if ops else "Nothing to undo")
        self.btn_go.config(text="Restore", command=self._restore, state="normal" if ops else "disabled")

    def _restore(self) -> None:
        sel = self.tree.selection()
        if not sel or sel[0] not in getattr(self, "ops", {}):
            self.status.config(text="Pick an operation first")
            return
        self.btn_go.config(text="Move to Trash", command=self.uninstall, state="disabled")
        self._bg("undo", ["undo", sel[0]])

    def _on_undo(self, env: dict) -> None:
        d = env.get("data") or {}
        results = d.get("results", [])
        n = sum(1 for r in results if r.get("status") == "restored")
        self.last_msg = (f"restored {n} of {len(results)}" if results else
                         (env.get("error") or {}).get("message", "nothing restored"))
        self.title.config(text=self.last_msg)
        self.tree.delete(*self.tree.get_children())
        self.refresh_apps()


def main() -> int:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("aqua" if sys.platform == "darwin" else "clam")
    except tk.TclError:
        pass
    ZapGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
