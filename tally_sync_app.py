"""
Tally Sync Connector - desktop app (Tkinter, standard library only).

Run:   python tally_sync_app.py
Needs: tally_core.py in the same folder.
"""
import os
import queue
import threading
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk

import tally_core as core

# ---------------------------------------------------------------- theme
NAVY, NAVY2 = "#14213D", "#1E2F57"
ACCENT, ACCENT_DK = "#2563EB", "#1D4ED8"
BG, CARD, BORDER = "#F3F5F9", "#FFFFFF", "#DDE2EC"
TEXT, MUTED = "#1F2937", "#6B7280"
GREEN, RED, AMBER = "#16A34A", "#DC2626", "#B45309"
SIDE_MUTED = "#9FB0D3"
FONT = "Segoe UI"
PAGE = 100  # rows per page in the Vouchers screen
INTERVALS = {"15 minutes": 15, "30 minutes": 30, "1 hour": 60,
             "2 hours": 120, "4 hours": 240, "Daily": 1440}


# ---------------------------------------------------------------- small widget helpers
def card(parent, **kw):
    return tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1, **kw)


def button(parent, text, command, primary=True):
    return tk.Button(
        parent, text=text, command=command, relief="flat", bd=0, cursor="hand2",
        padx=16, pady=7, font=(FONT, 9, "bold"),
        bg=ACCENT if primary else CARD, fg="white" if primary else TEXT,
        activebackground=ACCENT_DK if primary else "#EEF2F8",
        activeforeground="white" if primary else TEXT,
        highlightthickness=0 if primary else 1, highlightbackground=BORDER)


def entry(parent, var, width=30):
    return tk.Entry(parent, textvariable=var, width=width, relief="flat", bg=CARD, fg=TEXT,
                    font=(FONT, 10), insertbackground=TEXT, highlightthickness=1,
                    highlightbackground=BORDER, highlightcolor=ACCENT)


def label(parent, text, size=9, color=TEXT, bold=False, bg=None, **kw):
    return tk.Label(parent, text=text, font=(FONT, size, "bold" if bold else "normal"),
                    fg=color, bg=bg or parent["bg"], **kw)


def make_tree(parent, columns, height=12):
    """columns: list of (id, heading, width, anchor). Returns (wrapper_frame, tree)."""
    wrap = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
    tree = ttk.Treeview(wrap, columns=[c[0] for c in columns], show="headings",
                        height=height, selectmode="browse")
    for cid, head, width, anchor in columns:
        tree.heading(cid, text=head, anchor=anchor)
        tree.column(cid, width=width, anchor=anchor, stretch=True)
    sb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    tree.tag_configure("fail", foreground=RED)
    tree.tag_configure("odd", background="#F8FAFD")
    return wrap, tree


def fill_tree(tree, rows, tags_fn=None):
    tree.delete(*tree.get_children())
    for i, r in enumerate(rows):
        tags = ("odd",) if i % 2 else ()
        if tags_fn:
            tags = tags + tags_fn(r)
        tree.insert("", "end", values=r, tags=tags)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Tally Sync Connector")
        self.geometry("1040x680")
        self.minsize(920, 600)
        self.configure(bg=BG)

        self.cfg = core.load_config()
        self.q = queue.Queue()
        self.syncing = False
        self._auto_job = None
        self.v_page = 0
        self._search_job = None

        self._style()
        self._layout()
        self.show("Dashboard")
        self._schedule_auto()
        self.after(150, self._poll)
        self.after(400, self.test_connection)  # quietly detect Tally on startup

    # ------------------------------------------------------------ styling / layout
    def _style(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                    rowheight=26, borderwidth=0, font=(FONT, 9))
        s.configure("Treeview.Heading", background="#EEF2F8", foreground=MUTED, relief="flat",
                    font=(FONT, 8, "bold"), padding=(8, 6))
        s.map("Treeview", background=[("selected", "#DBE7FF")], foreground=[("selected", TEXT)])
        s.configure("TCombobox", padding=4)
        s.configure("Horizontal.TProgressbar", troughcolor="#E5E7EB", background=ACCENT,
                    borderwidth=0, thickness=8)

    def _layout(self):
        side = tk.Frame(self, bg=NAVY, width=190)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        brand = tk.Frame(side, bg=NAVY)
        brand.pack(fill="x", padx=16, pady=(20, 18))
        tk.Label(brand, text="T", bg=ACCENT, fg="white", font=(FONT, 13, "bold"),
                 width=2).pack(side="left")
        col = tk.Frame(brand, bg=NAVY)
        col.pack(side="left", padx=10)
        label(col, "Tally Sync", 11, "white", True, bg=NAVY).pack(anchor="w")
        label(col, "Local connector", 8, SIDE_MUTED, bg=NAVY).pack(anchor="w")

        self.nav = {}
        for name in ("Dashboard", "Ledgers", "Vouchers", "Sync History", "Settings"):
            b = tk.Button(side, text="   " + name, anchor="w", relief="flat", bd=0,
                          font=(FONT, 10), fg=SIDE_MUTED, bg=NAVY, activebackground=NAVY2,
                          activeforeground="white", cursor="hand2", padx=12, pady=9,
                          command=lambda n=name: self.show(n))
            b.pack(fill="x", padx=10, pady=1)
            self.nav[name] = b

        foot = tk.Frame(side, bg=NAVY2)
        foot.pack(side="bottom", fill="x", padx=10, pady=14)
        row = tk.Frame(foot, bg=NAVY2)
        row.pack(fill="x", padx=10, pady=(8, 0))
        self.dot = tk.Canvas(row, width=10, height=10, bg=NAVY2, highlightthickness=0)
        self.dot.pack(side="left")
        self.dot_id = self.dot.create_oval(1, 1, 9, 9, fill=MUTED, outline="")
        self.conn_var = tk.StringVar(value="Checking Tally...")
        label(row, "", 8, "white", True, bg=NAVY2, textvariable=self.conn_var).pack(side="left", padx=6)
        self.url_var = tk.StringVar(value=self.cfg["url"])
        label(foot, "", 8, SIDE_MUTED, bg=NAVY2, textvariable=self.url_var).pack(
            anchor="w", padx=26, pady=(0, 8))

        self.content = tk.Frame(self, bg=BG)
        self.content.pack(side="left", fill="both", expand=True, padx=24, pady=18)

        self.pages = {
            "Dashboard": self._build_dashboard(),
            "Ledgers": self._build_ledgers(),
            "Vouchers": self._build_vouchers(),
            "Sync History": self._build_history(),
            "Settings": self._build_settings(),
        }

    def _header(self, page, title, subtitle):
        top = tk.Frame(page, bg=BG)
        top.pack(fill="x", pady=(0, 14))
        left = tk.Frame(top, bg=BG)
        left.pack(side="left")
        label(left, title, 17, TEXT, True).pack(anchor="w")
        label(left, subtitle, 9, MUTED).pack(anchor="w")
        right = tk.Frame(top, bg=BG)
        right.pack(side="right")
        return right

    def show(self, name):
        for n, b in self.nav.items():
            b.configure(bg=NAVY2 if n == name else NAVY, fg="white" if n == name else SIDE_MUTED)
        for p in self.pages.values():
            p.pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        {"Dashboard": self.refresh_dashboard, "Ledgers": self.refresh_ledgers,
         "Vouchers": self.refresh_vouchers, "Sync History": self.refresh_history}.get(
            name, lambda: None)()

    # ------------------------------------------------------------ Dashboard
    def _build_dashboard(self):
        p = tk.Frame(self.content, bg=BG)
        right = self._header(p, "Dashboard", "Sync Tally data into your local application")
        self.btn_full = button(right, "Full resync", lambda: self.start_sync("Full"), primary=False)
        self.btn_full.pack(side="left", padx=(0, 8))
        self.btn_sync = button(right, "Sync now", lambda: self.start_sync("Manual"))
        self.btn_sync.pack(side="left")

        c = card(p)
        c.pack(fill="x")
        inner = tk.Frame(c, bg=CARD)
        inner.pack(fill="x", padx=16, pady=12)
        label(inner, "COMPANY", 8, MUTED, True).grid(row=0, column=0, sticky="w")
        self.company_var = tk.StringVar()
        label(inner, "", 13, TEXT, True, textvariable=self.company_var).grid(row=1, column=0, sticky="w")
        self.range_var = tk.StringVar()
        label(inner, "", 9, MUTED, textvariable=self.range_var).grid(row=2, column=0, sticky="w")
        inner.columnconfigure(1, weight=1)
        self.next_var = tk.StringVar()
        label(inner, "", 9, MUTED, textvariable=self.next_var).grid(row=0, column=2, rowspan=3, sticky="e")

        stats = tk.Frame(p, bg=BG)
        stats.pack(fill="x", pady=12)
        self.stat_vars = {}
        for i, (key, title) in enumerate((("led", "Ledgers synced"), ("vch", "Vouchers synced"),
                                          ("last", "Last sync"), ("status", "Status"))):
            stats.columnconfigure(i, weight=1, uniform="s")
            sc = card(stats)
            sc.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0 if i == 3 else 6))
            label(sc, title.upper(), 8, MUTED, True).pack(anchor="w", padx=14, pady=(12, 0))
            v = tk.StringVar(value="-")
            lbl = label(sc, "", 18 if key in ("led", "vch") else 12, TEXT, True, textvariable=v)
            lbl.pack(anchor="w", padx=14, pady=(2, 12))
            self.stat_vars[key] = (v, lbl)

        pc = card(p)
        pc.pack(fill="x")
        pin = tk.Frame(pc, bg=CARD)
        pin.pack(fill="x", padx=16, pady=12)
        self.status_var = tk.StringVar(value="Ready")
        label(pin, "", 9, TEXT, True, textvariable=self.status_var).pack(anchor="w")
        self.progress = ttk.Progressbar(pin, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(6, 0))

        label(p, "Recent syncs", 11, TEXT, True).pack(anchor="w", pady=(14, 6))
        wrap, self.recent_tree = make_tree(p, [
            ("t", "Run time", 170, "w"), ("k", "Type", 100, "w"), ("l", "Ledgers", 80, "e"),
            ("v", "Vouchers", 80, "e"), ("s", "Status", 90, "w")], height=6)
        wrap.pack(fill="both", expand=True)
        return p

    def refresh_dashboard(self):
        self.company_var.set(self.cfg["company"] or "No company selected - open Settings")
        self.range_var.set(f"Vouchers from {self.cfg['date_from']} to {self.cfg['date_to']}")
        self.url_var.set(self.cfg["url"])
        try:
            n_led, n_vch, last = core.get_stats(self.cfg["db_path"])
            hist = core.get_history(self.cfg["db_path"], 6)
        except Exception as e:  # bad db path etc.
            self.status_var.set(f"Database error: {e}")
            return
        self.stat_vars["led"][0].set(f"{n_led:,}")
        self.stat_vars["vch"][0].set(f"{n_vch:,}")
        if last:
            self.stat_vars["last"][0].set(self._nice_time(last[0]))
            ok = (last[1] or "Success") == "Success"
            self.stat_vars["status"][0].set("Up to date" if ok else "Failed")
            self.stat_vars["status"][1].configure(fg=GREEN if ok else RED)
        else:
            self.stat_vars["last"][0].set("Never")
            self.stat_vars["status"][0].set("Not synced yet")
            self.stat_vars["status"][1].configure(fg=MUTED)
        fill_tree(self.recent_tree,
                  [(self._nice_time(h[0]), h[1], h[2], h[3], h[4]) for h in hist],
                  lambda r: ("fail",) if r[4] == "Failed" else ())

    @staticmethod
    def _nice_time(iso):
        try:
            return datetime.fromisoformat(iso).strftime("%d-%b-%Y %I:%M %p")
        except (ValueError, TypeError):
            return iso or ""

    # ------------------------------------------------------------ Ledgers
    def _build_ledgers(self):
        p = tk.Frame(self.content, bg=BG)
        right = self._header(p, "Ledgers", "Ledger master and balances stored locally")
        button(right, "Export CSV", self.export_ledgers, primary=False).pack()
        bar = tk.Frame(p, bg=BG)
        bar.pack(fill="x", pady=(0, 10))
        label(bar, "Search", 9, MUTED, True).pack(side="left")
        self.l_search = tk.StringVar()
        e = entry(bar, self.l_search, 36)
        e.pack(side="left", padx=8, ipady=4)
        e.bind("<KeyRelease>", lambda _e: self._debounce(self.refresh_ledgers))
        self.l_count = tk.StringVar()
        label(bar, "", 9, MUTED, textvariable=self.l_count).pack(side="right")
        wrap, self.l_tree = make_tree(p, [
            ("n", "Ledger name", 320, "w"), ("g", "Group", 200, "w"),
            ("o", "Opening (Rs.)", 130, "e"), ("c", "Closing (Rs.)", 130, "e")], height=18)
        wrap.pack(fill="both", expand=True)
        return p

    def refresh_ledgers(self):
        rows = core.query_ledgers(self.cfg["db_path"], self.l_search.get().strip())
        fill_tree(self.l_tree, [(n, g, core.inr(o), core.inr(c)) for n, g, o, c in rows])
        self.l_count.set(f"{len(rows):,} ledgers")

    def export_ledgers(self):
        rows = core.query_ledgers(self.cfg["db_path"], self.l_search.get().strip())
        self._export("ledgers.csv", ["Ledger", "Group", "Opening", "Closing"], rows)

    # ------------------------------------------------------------ Vouchers
    def _build_vouchers(self):
        p = tk.Frame(self.content, bg=BG)
        right = self._header(p, "Vouchers", "Browse vouchers stored in the local database")
        button(right, "Export CSV", self.export_vouchers, primary=False).pack()
        bar = tk.Frame(p, bg=BG)
        bar.pack(fill="x", pady=(0, 10))
        label(bar, "Search", 9, MUTED, True).pack(side="left")
        self.v_search = tk.StringVar()
        e = entry(bar, self.v_search, 30)
        e.pack(side="left", padx=8, ipady=4)
        e.bind("<KeyRelease>", lambda _e: self._debounce(self._v_reset))
        label(bar, "Type", 9, MUTED, True).pack(side="left", padx=(10, 0))
        self.v_type = tk.StringVar(value="All types")
        self.v_type_cb = ttk.Combobox(bar, textvariable=self.v_type, values=["All types"],
                                      state="readonly", width=16)
        self.v_type_cb.pack(side="left", padx=8)
        self.v_type_cb.bind("<<ComboboxSelected>>", lambda _e: self._v_reset())
        wrap, self.v_tree = make_tree(p, [
            ("d", "Date", 100, "w"), ("t", "Type", 110, "w"), ("n", "Voucher no.", 110, "w"),
            ("p", "Party ledger", 260, "w"), ("a", "Amount (Rs.)", 120, "e"),
            ("m", "Narration", 220, "w")], height=17)
        wrap.pack(fill="both", expand=True)
        pager = tk.Frame(p, bg=BG)
        pager.pack(fill="x", pady=(8, 0))
        self.v_info = tk.StringVar()
        label(pager, "", 9, MUTED, textvariable=self.v_info).pack(side="left")
        button(pager, "Next >", lambda: self._v_move(1), primary=False).pack(side="right")
        button(pager, "< Prev", lambda: self._v_move(-1), primary=False).pack(side="right", padx=6)
        return p

    def _v_reset(self):
        self.v_page = 0
        self.refresh_vouchers()

    def _v_move(self, step):
        self.v_page = max(0, self.v_page + step)
        self.refresh_vouchers()

    def refresh_vouchers(self):
        path = self.cfg["db_path"]
        self.v_type_cb["values"] = ["All types"] + core.voucher_types(path)
        total, rows = core.query_vouchers(path, self.v_search.get().strip(), self.v_type.get(),
                                          PAGE, self.v_page * PAGE)
        last_page = max(0, (total - 1) // PAGE)
        if self.v_page > last_page:  # e.g. after filtering
            self.v_page = last_page
            return self.refresh_vouchers()
        fill_tree(self.v_tree, [(core.iso_to_ui(d), t, n, p, core.inr(a), m)
                                for d, t, n, p, a, m in rows])
        start = self.v_page * PAGE + 1 if total else 0
        self.v_info.set(f"Showing {start:,}-{self.v_page * PAGE + len(rows):,} of {total:,} vouchers"
                        f"   |   Page {self.v_page + 1} of {last_page + 1}")

    def export_vouchers(self):
        _, rows = core.query_vouchers(self.cfg["db_path"], self.v_search.get().strip(),
                                      self.v_type.get())
        self._export("vouchers.csv", ["Date", "Type", "Voucher no.", "Party", "Amount", "Narration"],
                     [(core.iso_to_ui(r[0]),) + tuple(r[1:]) for r in rows])

    # ------------------------------------------------------------ Sync history
    def _build_history(self):
        p = tk.Frame(self.content, bg=BG)
        self._header(p, "Sync History", "Every sync run, newest first")
        wrap, self.h_tree = make_tree(p, [
            ("t", "Run time", 170, "w"), ("k", "Type", 90, "w"), ("l", "Ledgers", 70, "e"),
            ("v", "Vouchers", 80, "e"), ("s", "Status", 80, "w"), ("m", "Message", 380, "w")],
            height=22)
        wrap.pack(fill="both", expand=True)
        return p

    def refresh_history(self):
        hist = core.get_history(self.cfg["db_path"], 300)
        fill_tree(self.h_tree, [(self._nice_time(h[0]),) + tuple(h[1:]) for h in hist],
                  lambda r: ("fail",) if r[4] == "Failed" else ())

    # ------------------------------------------------------------ Settings
    def _build_settings(self):
        p = tk.Frame(self.content, bg=BG)
        right = self._header(p, "Settings", "Tally connection, local storage and schedule")
        button(right, "Save settings", self.save_settings).pack()

        c1 = card(p)
        c1.pack(fill="x", pady=(0, 12))
        label(c1, "Tally connection", 11, TEXT, True).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 6))
        c1.columnconfigure(1, weight=1)
        self.s_url = tk.StringVar(value=self.cfg["url"])
        self.s_company = tk.StringVar(value=self.cfg["company"])
        label(c1, "Tally URL", 9, MUTED, True).grid(row=1, column=0, sticky="w", padx=16)
        entry(c1, self.s_url).grid(row=1, column=1, sticky="ew", padx=(0, 16), pady=4, ipady=4)
        label(c1, "Company", 9, MUTED, True).grid(row=2, column=0, sticky="w", padx=16)
        self.company_cb = ttk.Combobox(c1, textvariable=self.s_company, values=[])
        self.company_cb.grid(row=2, column=1, sticky="ew", padx=(0, 16), pady=4, ipady=3)
        row = tk.Frame(c1, bg=CARD)
        row.grid(row=3, column=1, sticky="w", pady=(4, 12))
        button(row, "Test connection / detect companies",
               lambda: self.test_connection(verbose=True), primary=False).pack(side="left")
        self.test_var = tk.StringVar()
        self.test_lbl = label(row, "", 9, MUTED, textvariable=self.test_var)
        self.test_lbl.pack(side="left", padx=10)

        c2 = card(p)
        c2.pack(fill="x", pady=(0, 12))
        label(c2, "Local database & date range", 11, TEXT, True).grid(
            row=0, column=0, sticky="w", padx=16, pady=(12, 6))
        c2.columnconfigure(1, weight=1)
        self.s_db = tk.StringVar(value=self.cfg["db_path"])
        self.s_from = tk.StringVar(value=self.cfg["date_from"])
        self.s_to = tk.StringVar(value=self.cfg["date_to"])
        label(c2, "SQLite file", 9, MUTED, True).grid(row=1, column=0, sticky="w", padx=16)
        entry(c2, self.s_db).grid(row=1, column=1, sticky="ew", pady=4, ipady=4)
        button(c2, "Browse", self._browse_db, primary=False).grid(row=1, column=2, padx=16)
        label(c2, "Voucher dates", 9, MUTED, True).grid(row=2, column=0, sticky="w", padx=16)
        dr = tk.Frame(c2, bg=CARD)
        dr.grid(row=2, column=1, sticky="w", pady=(4, 12))
        entry(dr, self.s_from, 12).pack(side="left", ipady=4)
        label(dr, "  to  ", 9, MUTED).pack(side="left")
        entry(dr, self.s_to, 12).pack(side="left", ipady=4)
        label(dr, "   DD-MM-YYYY", 8, MUTED).pack(side="left")

        c3 = card(p)
        c3.pack(fill="x")
        label(c3, "Auto sync", 11, TEXT, True).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 6))
        self.s_auto = tk.BooleanVar(value=self.cfg["auto_enabled"])
        tk.Checkbutton(c3, text="Sync automatically while this app is open", variable=self.s_auto,
                       bg=CARD, activebackground=CARD, font=(FONT, 9)).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=12)
        label(c3, "Every", 9, MUTED, True).grid(row=2, column=0, sticky="w", padx=16, pady=(6, 12))
        self.s_interval = tk.StringVar(value=self.cfg["interval"])
        ttk.Combobox(c3, textvariable=self.s_interval, values=list(INTERVALS), state="readonly",
                     width=14).grid(row=2, column=1, sticky="w", pady=(6, 12))
        return p

    def _browse_db(self):
        f = filedialog.asksaveasfilename(defaultextension=".db", initialfile="tally_local.db",
                                         filetypes=[("SQLite database", "*.db"), ("All files", "*.*")])
        if f:
            self.s_db.set(f)

    def save_settings(self):
        try:
            core.ui_to_tally(self.s_from.get())
            core.ui_to_tally(self.s_to.get())
        except core.TallyError as e:
            messagebox.showerror("Invalid date", str(e))
            return
        self.cfg.update(url=self.s_url.get().strip() or "http://localhost:9000",
                        company=self.s_company.get().strip(), db_path=self.s_db.get().strip(),
                        date_from=self.s_from.get().strip(), date_to=self.s_to.get().strip(),
                        auto_enabled=bool(self.s_auto.get()), interval=self.s_interval.get())
        try:
            core.save_config(self.cfg)
        except OSError as e:
            messagebox.showerror("Could not save", str(e))
            return
        self._schedule_auto()
        self.test_connection()
        self.show("Dashboard")
        self.status_var.set("Settings saved.")

    # ------------------------------------------------------------ connection test
    def test_connection(self, verbose=False):
        url = self.s_url.get().strip() if verbose else self.cfg["url"]
        self.set_conn("wait", "Checking Tally...")
        if verbose:
            self.test_var.set("Checking...")

        def work():
            try:
                self.q.put(("conn_ok", core.list_companies(url), verbose))
            except core.TallyError as e:
                self.q.put(("conn_err", str(e), verbose))
        threading.Thread(target=work, daemon=True).start()

    def set_conn(self, state, text):
        color = {"ok": "#34D399", "err": "#F87171", "wait": "#FBBF24"}[state]
        self.dot.itemconfig(self.dot_id, fill=color)
        self.conn_var.set(text)

    # ------------------------------------------------------------ syncing
    def start_sync(self, kind):
        if self.syncing:
            return
        if not self.cfg["company"].strip():
            messagebox.showinfo("Choose a company", "Pick your company in Settings first.")
            self.show("Settings")
            return
        if kind == "Full" and not messagebox.askyesno(
                "Full resync",
                "This clears local ledgers and the vouchers in your date range, then reloads "
                "them from Tally. Continue?"):
            return
        self.syncing = True
        self._busy(True)
        cfg = dict(self.cfg)

        def work():
            try:
                res = core.do_sync(cfg, kind, lambda m: self.q.put(("progress", m)))
                self.q.put(("sync_ok", res, kind))
            except Exception as e:  # noqa: BLE001 - surface anything to the user
                msg = str(e)
                try:
                    core.log_run(cfg["db_path"], cfg["company"], kind, 0, 0, "Failed", msg[:300])
                except Exception:
                    pass
                self.q.put(("sync_err", msg, kind))
        threading.Thread(target=work, daemon=True).start()

    def _busy(self, on):
        state = "disabled" if on else "normal"
        self.btn_sync.configure(state=state)
        self.btn_full.configure(state=state)
        if on:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)

    def _schedule_auto(self):
        if self._auto_job:
            self.after_cancel(self._auto_job)
            self._auto_job = None
        if self.cfg["auto_enabled"]:
            mins = INTERVALS.get(self.cfg["interval"], 120)
            self._auto_job = self.after(mins * 60 * 1000, self._auto_tick)
            nxt = datetime.now() + timedelta(minutes=mins)
            self.next_var.set("Next auto sync: " + nxt.strftime("%d-%b %I:%M %p"))
        else:
            self.next_var.set("Auto sync is off")

    def _auto_tick(self):
        self._auto_job = None
        if not self.syncing and self.cfg["company"].strip():
            self.start_sync("Auto")
        self._schedule_auto()

    # ------------------------------------------------------------ queue from worker threads
    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                getattr(self, "_on_" + msg[0])(*msg[1:])
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _on_progress(self, text):
        self.status_var.set(text)

    def _on_sync_ok(self, res, kind):
        self.syncing = False
        self._busy(False)
        self.status_var.set(f"{kind} sync finished: {res['ledgers']:,} ledgers, "
                            f"{res['vouchers']:,} vouchers.")
        self.set_conn("ok", "Tally connected")
        self.refresh_dashboard()

    def _on_sync_err(self, text, kind):
        self.syncing = False
        self._busy(False)
        self.status_var.set("Sync failed - see Sync History")
        self.set_conn("err", "Tally not reachable")
        self.refresh_dashboard()
        if kind != "Auto":
            messagebox.showerror("Sync failed", text)

    def _on_conn_ok(self, companies, verbose):
        self.set_conn("ok", "Tally connected")
        self.company_cb["values"] = companies
        if verbose:
            self.test_lbl.configure(fg=GREEN)
            self.test_var.set(f"Connected - {len(companies)} company(ies) open")
            if companies and not self.s_company.get().strip():
                self.s_company.set(companies[0])
        elif companies and not self.cfg["company"]:
            self.s_company.set(companies[0])

    def _on_conn_err(self, text, verbose):
        self.set_conn("err", "Tally not reachable")
        if verbose:
            self.test_lbl.configure(fg=RED)
            self.test_var.set("Not connected")
            messagebox.showwarning("Cannot connect", text)

    # ------------------------------------------------------------ misc
    def _debounce(self, fn):
        if self._search_job:
            self.after_cancel(self._search_job)
        self._search_job = self.after(250, fn)

    def _export(self, default_name, header, rows):
        f = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=default_name,
                                         filetypes=[("CSV", "*.csv")])
        if not f:
            return
        try:
            core.export_csv(f, header, rows)
            messagebox.showinfo("Exported", f"{len(rows):,} rows saved to\n{os.path.basename(f)}")
        except OSError as e:
            messagebox.showerror("Export failed", str(e))


if __name__ == "__main__":
    App().mainloop()
