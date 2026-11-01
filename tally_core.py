"""
tally_core.py - Tally connector + local SQLite storage (no GUI, standard library only).
Used by tally_sync_app.py, but can also be imported on its own.
"""
import csv
import json
import os
import re
import socket
import sqlite3
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import closing
from datetime import date, datetime
from xml.sax.saxutils import escape

APP_DIR = os.path.join(os.path.expanduser("~"), ".tally_sync")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")


class TallyError(Exception):
    """Raised with a user-friendly message."""


# ------------------------------------------------------------------ config
def default_config():
    today = date.today()
    start_year = today.year if today.month >= 4 else today.year - 1
    return {
        "url": "http://localhost:9000",
        "company": "",
        "db_path": os.path.join(APP_DIR, "tally_local.db"),
        "date_from": f"01-04-{start_year}",
        "date_to": f"31-03-{start_year + 1}",
        "auto_enabled": False,
        "interval": "2 hours",
    }


def load_config():
    cfg = default_config()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    os.makedirs(APP_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ------------------------------------------------------------------ helpers
def ui_to_tally(s):
    """'01-04-2026' -> '20260401' (raises TallyError on bad input)."""
    try:
        return datetime.strptime(s.strip(), "%d-%m-%Y").strftime("%Y%m%d")
    except ValueError:
        raise TallyError(f"Invalid date '{s}'. Use DD-MM-YYYY, e.g. 01-04-2026.")


def iso_to_ui(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d-%m-%Y")
    except (ValueError, TypeError):
        return iso or ""


def inr(x):
    """Format a number with Indian digit grouping: 1,25,000.00"""
    neg = x < 0
    whole, frac = f"{abs(x):.2f}".split(".")
    if len(whole) > 3:
        head, tail, parts = whole[:-3], whole[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return ("-" if neg else "") + whole + "." + frac


def _text(node, tag):
    el = node.find(tag)
    return (el.text or "").strip() if el is not None and el.text else ""


def _num(value):
    try:
        return float(re.sub(r"[^\d.\-]", "", value) or 0)
    except ValueError:
        return 0.0


# ------------------------------------------------------------------ Tally connector
def _collection_xml(coll_id, tally_type, fields, company=None, d_from=None, d_to=None):
    sv = "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
    if company:
        sv += f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>"
    if d_from and d_to:
        sv += f"<SVFROMDATE>{d_from}</SVFROMDATE><SVTODATE>{d_to}</SVTODATE>"
    return f"""<ENVELOPE>
  <HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE><ID>{coll_id}</ID></HEADER>
  <BODY><DESC>
    <STATICVARIABLES>{sv}</STATICVARIABLES>
    <TDL><TDLMESSAGE>
      <COLLECTION NAME="{coll_id}" ISMODIFY="No">
        <TYPE>{tally_type}</TYPE><FETCH>{fields}</FETCH>
      </COLLECTION>
    </TDLMESSAGE></TDL>
  </DESC></BODY>
</ENVELOPE>"""


def _post(url, xml_body, timeout=120):
    req = urllib.request.Request(
        url, data=xml_body.encode("utf-8"),
        headers={"Content-Type": "text/xml; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        raise TallyError(
            f"Cannot reach Tally at {url}.\nMake sure Tally is running and "
            f"Client/Server is enabled (F1 > Settings > Connectivity), port 9000.\n({e})")
    # Tally often emits characters that are illegal in XML
    raw = re.sub(r"&#(?:[0-8]|1[1-2]|1[4-9]|2[0-9]|3[0-1]);", "", raw)
    raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise TallyError("Tally returned an unreadable response. "
                         "Check that no dialog box is open in Tally.")
    err = root.find(".//LINEERROR")
    if err is not None and err.text:
        raise TallyError(f"Tally error: {err.text.strip()}")
    return root


def list_companies(url):
    root = _post(url, _collection_xml("SyncCompanies", "Company", "Name"), timeout=10)
    names = []
    for el in root.iter("COMPANY"):
        n = el.get("NAME") or _text(el, "NAME")
        if n and n not in names:
            names.append(n)
    return names


def fetch_ledgers(url, company):
    root = _post(url, _collection_xml(
        "SyncLedgers", "Ledger", "Name, Parent, OpeningBalance, ClosingBalance", company))
    rows = []
    for el in root.iter("LEDGER"):
        name = el.get("NAME") or _text(el, "NAME")
        if name:
            rows.append((name, _text(el, "PARENT"),
                         _num(_text(el, "OPENINGBALANCE")), _num(_text(el, "CLOSINGBALANCE"))))
    return rows


def fetch_vouchers(url, company, d_from, d_to):
    root = _post(url, _collection_xml(
        "SyncVouchers", "Voucher",
        "Date, VoucherTypeName, VoucherNumber, PartyLedgerName, Amount, Narration, MasterID, AlterID",
        company, d_from, d_to))
    rows = []
    for el in root.iter("VOUCHER"):
        mid = _text(el, "MASTERID")
        if not mid:
            continue
        d = _text(el, "DATE")
        try:
            iso = datetime.strptime(d, "%Y%m%d").date().isoformat()
        except ValueError:
            iso = None
        rows.append((mid, iso, _text(el, "VOUCHERTYPENAME"), _text(el, "VOUCHERNUMBER"),
                     _text(el, "PARTYLEDGERNAME"), _num(_text(el, "AMOUNT")),
                     _text(el, "NARRATION"), int(_num(_text(el, "ALTERID")))))
    return rows


# ------------------------------------------------------------------ local database
def _connect(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    init_db(conn)
    return conn


def init_db(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ledgers (
        name TEXT PRIMARY KEY, parent TEXT,
        opening_balance REAL, closing_balance REAL, synced_at TEXT);
    CREATE TABLE IF NOT EXISTS vouchers (
        master_id TEXT PRIMARY KEY, date TEXT, voucher_type TEXT,
        voucher_number TEXT, party TEXT, amount REAL, narration TEXT,
        alter_id INTEGER, synced_at TEXT);
    CREATE TABLE IF NOT EXISTS sync_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, company TEXT,
        ledgers INTEGER, vouchers INTEGER, ran_at TEXT);
    """)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sync_log)")}
    for col, ddl in (("kind", "TEXT"), ("status", "TEXT"), ("message", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE sync_log ADD COLUMN {col} {ddl}")
    conn.commit()


def log_run(path, company, kind, n_led, n_vch, status, message=""):
    with closing(_connect(path)) as conn:
        conn.execute(
            "INSERT INTO sync_log (company, ledgers, vouchers, ran_at, kind, status, message) "
            "VALUES (?,?,?,?,?,?,?)",
            (company, n_led, n_vch, datetime.now().isoformat(timespec="seconds"),
             kind, status, message))
        conn.commit()


def do_sync(cfg, kind, say=lambda m: None):
    """kind: 'Manual' | 'Auto' | 'Full'. Returns dict with counts."""
    url, company, path = cfg["url"].strip(), cfg["company"].strip(), cfg["db_path"]
    d_from, d_to = ui_to_tally(cfg["date_from"]), ui_to_tally(cfg["date_to"])
    if d_from > d_to:
        raise TallyError("'From' date must be before 'To' date.")
    if not company:
        raise TallyError("Company name is not set. Open Settings first.")

    say("Fetching ledgers from Tally...")
    ledgers = fetch_ledgers(url, company)
    say(f"Got {len(ledgers)} ledgers. Fetching vouchers...")
    vouchers = fetch_vouchers(url, company, d_from, d_to)
    say(f"Got {len(vouchers)} vouchers. Saving to local database...")

    now = datetime.now().isoformat(timespec="seconds")
    iso_from = datetime.strptime(d_from, "%Y%m%d").date().isoformat()
    iso_to = datetime.strptime(d_to, "%Y%m%d").date().isoformat()
    with closing(_connect(path)) as conn:
        if kind == "Full":  # also removes rows that were deleted in Tally
            conn.execute("DELETE FROM ledgers")
            conn.execute("DELETE FROM vouchers WHERE date BETWEEN ? AND ?", (iso_from, iso_to))
        conn.executemany(
            """INSERT INTO ledgers VALUES (?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET parent=excluded.parent,
               opening_balance=excluded.opening_balance,
               closing_balance=excluded.closing_balance, synced_at=excluded.synced_at""",
            [r + (now,) for r in ledgers])
        conn.executemany(
            """INSERT INTO vouchers VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(master_id) DO UPDATE SET date=excluded.date,
               voucher_type=excluded.voucher_type, voucher_number=excluded.voucher_number,
               party=excluded.party, amount=excluded.amount, narration=excluded.narration,
               alter_id=excluded.alter_id, synced_at=excluded.synced_at""",
            [r + (now,) for r in vouchers])
        conn.commit()
    log_run(path, company, kind, len(ledgers), len(vouchers), "Success")
    return {"ledgers": len(ledgers), "vouchers": len(vouchers)}


# ------------------------------------------------------------------ queries for the UI
def get_stats(path):
    with closing(_connect(path)) as conn:
        n_led = conn.execute("SELECT COUNT(*) FROM ledgers").fetchone()[0]
        n_vch = conn.execute("SELECT COUNT(*) FROM vouchers").fetchone()[0]
        last = conn.execute(
            "SELECT ran_at, status, message FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
    return n_led, n_vch, last


def get_history(path, limit=200):
    with closing(_connect(path)) as conn:
        return conn.execute(
            "SELECT ran_at, COALESCE(kind,''), ledgers, vouchers, COALESCE(status,'Success'), "
            "COALESCE(message,'') FROM sync_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def query_ledgers(path, search=""):
    with closing(_connect(path)) as conn:
        like = f"%{search}%"
        return conn.execute(
            "SELECT name, parent, opening_balance, closing_balance FROM ledgers "
            "WHERE name LIKE ? OR parent LIKE ? ORDER BY name", (like, like)).fetchall()


def voucher_types(path):
    with closing(_connect(path)) as conn:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT voucher_type FROM vouchers WHERE voucher_type<>'' ORDER BY 1")]


def query_vouchers(path, search="", vtype="", limit=None, offset=0):
    where, args = [], []
    if search:
        where.append("(party LIKE ? OR voucher_number LIKE ? OR narration LIKE ?)")
        args += [f"%{search}%"] * 3
    if vtype and vtype != "All types":
        where.append("voucher_type = ?")
        args.append(vtype)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    sql = (f"SELECT date, voucher_type, voucher_number, party, amount, narration "
           f"FROM vouchers {w} ORDER BY date DESC, master_id DESC")
    if limit:
        sql += " LIMIT ? OFFSET ?"
        args_q = args + [limit, offset]
    else:
        args_q = args
    with closing(_connect(path)) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM vouchers {w}", args).fetchone()[0]
        rows = conn.execute(sql, args_q).fetchall()
    return total, rows


def export_csv(filename, header, rows):
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig opens cleanly in Excel
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
