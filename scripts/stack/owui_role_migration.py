#!/usr/bin/env python3
"""owui_role_migration.py - move Open WebUI's stored model references onto the model ROLES.

WHY (model-roles, item mr-consumers, operator decisions R1-R5, 2026-09-28)
--------------------------------------------------------------------------
Every service now asks the LiteLLM gateway for a ROLE (`local-large`,
`local-small`, `local-embed`, ...; inference/README.md) instead of a concrete
local model id. Open WebUI keeps three such references in its DATABASE, not in
any file this repo ships, so moving them is a landing step and this script is it:

  model.base_model_id      every preset built on a concrete chat id
                           (qwen36-27b -> local-large, qwen36-27b:nothink ->
                           local-large:nothink). Only that column changes;
                           the preset's id, name, params, grants and chats
                           are untouched (chats reference the PRESET id).
  config 'rag.embedding_model'
                           the RAG embedding model, when the engine is
                           `openai` (LiteLLM) and the value is a bge-m3 name
                           -> local-embed. Same model, same 1024-dim vectors,
                           so nothing is re-embedded.
  config 'ui.model_order_list'
                           each concrete id in the picker order is replaced
                           in place by its role (a role already in the list
                           keeps its own position and the old entry goes).

The old names stay registered in LiteLLM until item mr-retire (R5), so a row
this script does not reach still works.

SAFETY
------
* DRY RUN BY DEFAULT: opens the database read-only (`mode=ro`), prints the
  before/after table of exactly the cells it would change, and writes nothing.
* `--apply` needs `--restore-file PATH` (a new file; never overwritten). The
  restore file is written and flushed BEFORE the database is touched; then one
  transaction re-reads every planned cell, refuses if any changed since the plan,
  and writes. A second `--apply` finds nothing to do and writes nothing.
* `--restore PATH [--apply]` puts every recorded cell back byte-identical (same
  SQLite storage class, same bytes); it refuses - writing nothing - if any cell
  no longer holds the value the migration wrote, unless it already holds the
  original (then that cell is skipped).
* A JSON config value is rewritten only if Python's `json.dumps` reproduces the
  stored text exactly (that is how Open WebUI 0.11's SQLAlchemy JSON column
  writes it); otherwise the script refuses rather than reformat a value.
* Open WebUI reads `rag.embedding_model` once at startup, and writing under a
  running instance races it, so the landing runs this with Open WebUI STOPPED.
  A non-empty `<db>-wal` file is the sign of a running (or crashed) instance:
  `--apply` refuses unless `--wal-ok` says the operator has checked.

THREAT MODEL: this changes which model NAME Open WebUI's stored rows send. It does
not defend against anyone editing the database or this script.

USAGE
-----
  python owui_role_migration.py --db webui.db                       # dry run
  python owui_role_migration.py --db webui.db --apply --restore-file r.json
  python owui_role_migration.py --db webui.db --restore r.json      # dry run
  python owui_role_migration.py --db webui.db --restore r.json --apply

Exit codes: 0 done / nothing to do, 1 refused (nothing written), 2 usage error.
Stdlib only (runs inside the openwebui image or any python3 >= 3.8).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sqlite3
import sys
from typing import Any

CHAT_ROLES = {
    "qwen36-27b": "local-large",
    "qwen36-27b:nothink": "local-large:nothink",
}
EMBED_NAMES = ("bge-m3", "bge-m3-f16.gguf", "qllama/bge-m3:latest")
EMBED_ROLE = "local-embed"
ALL_ROLES = dict(CHAT_ROLES, **{n: EMBED_ROLE for n in EMBED_NAMES})
OLD_IDS = tuple(ALL_ROLES)

RESTORE_FORMAT = "owui-role-migration/1"


class Refused(Exception):
    """The script will not proceed; nothing has been written."""


# ---------------------------------------------------------------------------
# cells: one changeable value, addressed by table + key column + key + column
# ---------------------------------------------------------------------------

def _typed(value: Any) -> dict:
    """A SQLite value with its storage class, JSON-safe and exact."""
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bytes):
        return {"type": "blob", "value": value.hex()}
    if isinstance(value, int):
        return {"type": "integer", "value": value}
    if isinstance(value, float):
        return {"type": "real", "value": repr(value)}
    return {"type": "text", "value": value}


def _untyped(t: dict) -> Any:
    kind = t["type"]
    if kind == "null":
        return None
    if kind == "blob":
        return bytes.fromhex(t["value"])
    if kind == "integer":
        return int(t["value"])
    if kind == "real":
        return float(t["value"])
    if kind == "text":
        return str(t["value"])
    raise Refused(f"restore file: unknown storage class {kind!r}")


def _read_cell(conn: sqlite3.Connection, table: str, keycol: str, key: str, column: str) -> Any:
    rows = conn.execute(f'SELECT "{column}" FROM "{table}" WHERE "{keycol}" = ?', (key,)).fetchall()
    if len(rows) != 1:
        raise Refused(f"{table}.{keycol}={key!r}: expected exactly one row, found {len(rows)}")
    return rows[0][0]


def _same(a: Any, b: Any) -> bool:
    return type(a) is type(b) and a == b


# ---------------------------------------------------------------------------
# planning (read-only)
# ---------------------------------------------------------------------------

def _config_json(conn: sqlite3.Connection, key: str) -> tuple[Any, Any] | None:
    """(raw stored value, parsed JSON) for a config key, or None if absent."""
    rows = conn.execute('SELECT value FROM config WHERE key = ?', (key,)).fetchall()
    if not rows:
        return None
    raw = rows[0][0]
    if not isinstance(raw, str):
        raise Refused(f"config {key!r} is stored as {type(raw).__name__}, not JSON text; refusing")
    try:
        parsed = json.loads(raw)
    except ValueError as e:
        raise Refused(f"config {key!r} is not valid JSON ({e}); refusing") from e
    if json.dumps(parsed) != raw:
        raise Refused(f"config {key!r}: json.dumps does not reproduce the stored text, so a "
                      "rewrite would reformat the value; refusing (nothing written)")
    return raw, parsed


def _check_schema(conn: sqlite3.Connection) -> None:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("model", "config"):
        if t not in tables:
            raise Refused(f"no `{t}` table - is this an Open WebUI database?")
    cfg_cols = [r[1] for r in conn.execute("PRAGMA table_info(config)")]
    if "key" not in cfg_cols or "value" not in cfg_cols:
        raise Refused("the `config` table has no key/value columns: this is not Open WebUI 0.11's "
                      f"per-key config (columns: {cfg_cols}); refusing")
    model_cols = [r[1] for r in conn.execute("PRAGMA table_info(model)")]
    for c in ("id", "base_model_id"):
        if c not in model_cols:
            raise Refused(f"the `model` table has no `{c}` column; refusing")


def plan(conn: sqlite3.Connection) -> tuple[list[dict], list[str]]:
    """The cells to change, and notes about references this script leaves alone."""
    _check_schema(conn)
    changes: list[dict] = []
    notes: list[str] = []

    # 1. presets on a concrete id
    for pid, base in conn.execute(
            f'SELECT id, base_model_id FROM model WHERE base_model_id IN ({",".join("?" * len(OLD_IDS))}) '
            "ORDER BY id", OLD_IDS):
        changes.append({"what": "preset", "table": "model", "keycol": "id", "key": pid,
                        "column": "base_model_id", "before": _typed(base),
                        "after": _typed(ALL_ROLES[base]),
                        "show": (base, ALL_ROLES[base])})

    # 2. the RAG embedding model
    got = _config_json(conn, "rag.embedding_model")
    if got is not None:
        raw, value = got
        engine = _config_json(conn, "rag.embedding_engine")
        engine_value = engine[1] if engine else None
        if isinstance(value, str) and value in EMBED_NAMES:
            if engine_value != "openai":
                notes.append(f"rag.embedding_model is {value!r} but rag.embedding_engine is "
                             f"{engine_value!r}, not 'openai' (LiteLLM): not changed")
            else:
                changes.append({"what": "rag", "table": "config", "keycol": "key",
                                "key": "rag.embedding_model", "column": "value",
                                "before": _typed(raw), "after": _typed(json.dumps(EMBED_ROLE)),
                                "show": (value, EMBED_ROLE)})

    # 3. the model picker order
    got = _config_json(conn, "ui.model_order_list")
    if got is not None:
        raw, value = got
        if isinstance(value, list) and all(isinstance(x, str) for x in value):
            new: list[str] = []
            moved: list[str] = []
            for x in value:
                y = ALL_ROLES.get(x, x)
                if y != x:
                    moved.append(f"{x} -> {y}")
                if y in new:
                    continue  # the role is already listed earlier: the old entry just goes
                if y != x and y in value:
                    continue  # the role is listed later in its own right: keep that position
                new.append(y)
            if new != value:
                changes.append({"what": "order", "table": "config", "keycol": "key",
                                "key": "ui.model_order_list", "column": "value",
                                "before": _typed(raw), "after": _typed(json.dumps(new)),
                                "show": ("; ".join(moved), f"{len(value)} -> {len(new)} entries")})
        else:
            notes.append("ui.model_order_list is not a list of strings: not changed")

    # 4. anything else in config that still names an old id (reported, not changed)
    handled = {"rag.embedding_model", "ui.model_order_list"}
    for key, raw in conn.execute("SELECT key, value FROM config ORDER BY key"):
        if key in handled:
            continue
        text = raw if isinstance(raw, str) else str(raw)
        hits = sorted({o for o in OLD_IDS if o in text})
        if hits:
            notes.append(f"config {key!r} mentions {', '.join(hits)}: not changed by this script")
    return changes, notes


def role_rows_note(conn: sqlite3.Connection) -> str | None:
    have = {r[0] for r in conn.execute(
        "SELECT id FROM model WHERE id IN ('local-large','local-large:nothink','local-embed')")}
    if "local-large" not in have:
        return ("no `local-large` model row yet (the mr-gateway label sync has not run here): "
                "presets still work once LiteLLM lists local-large; Open WebUI shows the role id "
                "until `stack.py labels` names it")
    return None


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------

def _short(v: Any, n: int = 60) -> str:
    s = v if isinstance(v, str) else json.dumps(v)
    return s if len(s) <= n else s[: n - 3] + "..."


def table(changes: list[dict], title: str) -> str:
    rows = [("what", "row", "column", "before", "after")]
    for c in changes:
        b, a = c["show"]
        rows.append((c["what"], f'{c["table"]}.{c["key"]}', c["column"], _short(b), _short(a)))
    widths = [max(len(r[i]) for r in rows) for i in range(5)]
    out = [title]
    for i, r in enumerate(rows):
        out.append("  " + "  ".join(r[j].ljust(widths[j]) for j in range(5)).rstrip())
        if i == 0:
            out.append("  " + "  ".join("-" * w for w in widths))
    return "\n".join(out)


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# write paths
# ---------------------------------------------------------------------------

def _wal_guard(db: str, wal_ok: bool) -> None:
    wal = db + "-wal"
    if os.path.exists(wal) and os.path.getsize(wal) > 0 and not wal_ok:
        raise Refused(f"{wal} is not empty: Open WebUI may be running (or stopped uncleanly). Stop it "
                      "first (the landing plan's order), or pass --wal-ok if you have checked it is "
                      "stopped. Nothing written.")


def _ro_uri(db: str) -> str:
    from urllib.request import pathname2url
    return "file:" + pathname2url(os.path.abspath(db)) + "?mode=ro"


def _connect_rw(db: str) -> sqlite3.Connection:
    if not os.path.isfile(db):
        raise Refused(f"{db} is not a file")
    conn = sqlite3.connect(db, isolation_level=None, timeout=5)
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _write_cells(conn: sqlite3.Connection, cells: list[tuple[dict, Any, Any]]) -> None:
    """cells: (cell, expected current value, new value). All-or-nothing."""
    conn.execute("BEGIN IMMEDIATE")  # a lock failure here raises before anything is written
    try:
        for c, expect, _new in cells:
            cur = _read_cell(conn, c["table"], c["keycol"], c["key"], c["column"])
            if not _same(cur, expect):
                raise Refused(f'{c["table"]}.{c["key"]}.{c["column"]} changed since the plan '
                              f"(now {_short(cur)!s}); nothing written")
        for c, _expect, new in cells:
            conn.execute(f'UPDATE "{c["table"]}" SET "{c["column"]}" = ? WHERE "{c["keycol"]}" = ?',
                         (new, c["key"]))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def migrate(db: str, apply: bool, restore_file: str | None, wal_ok: bool, out=None) -> int:
    out = out or sys.stdout
    if apply and not restore_file:
        raise Refused("--apply needs --restore-file PATH (a new file the rollback reads)")
    if apply and restore_file and os.path.exists(restore_file):
        raise Refused(f"{restore_file} already exists; refusing to overwrite a restore file")
    uri = _ro_uri(db)
    if not os.path.isfile(db):
        raise Refused(f"{db} is not a file")
    ro = sqlite3.connect(uri, uri=True)
    try:
        changes, notes = plan(ro)
        rnote = role_rows_note(ro)
    finally:
        ro.close()
    mode = "APPLY" if apply else "DRY RUN (nothing written; --apply to write)"
    print(f"owui_role_migration: {mode}\n  database: {db}", file=out)
    for n in notes + ([rnote] if rnote else []):
        print(f"  note: {n}", file=out)
    if not changes:
        print("  nothing to change: every reference this script owns already names a role.", file=out)
        return 0
    print(table(changes, f"  {len(changes)} cell(s) {'to change' if apply else 'would change'}:"), file=out)
    if not apply:
        return 0
    _wal_guard(db, wal_ok)
    record = {
        "format": RESTORE_FORMAT,
        "database": os.path.abspath(db),
        "created_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cells": [{k: c[k] for k in ("what", "table", "keycol", "key", "column", "before", "after")}
                  for c in changes],
    }
    with open(restore_file, "x", encoding="utf-8", newline="\n") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    conn = _connect_rw(db)
    try:
        _write_cells(conn, [(c, _untyped(c["before"]), _untyped(c["after"])) for c in changes])
    except (Refused, sqlite3.Error):
        conn.close()
        os.remove(restore_file)  # the transaction rolled back: nothing to restore
        raise
    conn.close()
    print(f"  written. restore file: {restore_file} (sha256 {_sha(restore_file)})", file=out)
    return 0


def restore(db: str, restore_file: str, apply: bool, wal_ok: bool, out=None) -> int:
    out = out or sys.stdout
    with open(restore_file, encoding="utf-8") as f:
        record = json.load(f)
    if record.get("format") != RESTORE_FORMAT:
        raise Refused(f"{restore_file} is not a {RESTORE_FORMAT} restore file")
    cells = record.get("cells") or []
    uri = _ro_uri(db)
    ro = sqlite3.connect(uri, uri=True)
    todo: list[tuple[dict, Any, Any]] = []
    skipped = 0
    try:
        for c in cells:
            if c["table"] not in ("model", "config") or c["column"] not in ("base_model_id", "value") \
                    or c["keycol"] not in ("id", "key"):
                raise Refused(f"restore file names a cell outside this script's scope: {c}")
            cur = _read_cell(ro, c["table"], c["keycol"], c["key"], c["column"])
            before, after = _untyped(c["before"]), _untyped(c["after"])
            if _same(cur, before):
                skipped += 1
                continue
            if not _same(cur, after):
                raise Refused(f'{c["table"]}.{c["key"]}.{c["column"]} holds neither the migrated nor the '
                              f"original value (now {_short(cur)}); refusing the whole restore")
            todo.append((c, after, before))
    finally:
        ro.close()
    mode = "APPLY" if apply else "DRY RUN (nothing written; --apply to write)"
    print(f"owui_role_migration --restore: {mode}\n  database: {db}\n  restore file: {restore_file}", file=out)
    if skipped:
        print(f"  {skipped} cell(s) already hold their original value: skipped", file=out)
    if not todo:
        print("  nothing to restore.", file=out)
        return 0
    shown = [dict(c, show=(_short(_untyped(c["after"])), _short(_untyped(c["before"])))) for c, _a, _b in todo]
    print(table(shown, f"  {len(todo)} cell(s) {'to restore' if apply else 'would be restored'}:"), file=out)
    if not apply:
        return 0
    _wal_guard(db, wal_ok)
    conn = _connect_rw(db)
    try:
        _write_cells(conn, todo)
    finally:
        conn.close()
    print("  restored.", file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Move Open WebUI's stored model references onto the model roles "
                                             "(dry run by default).")
    ap.add_argument("--db", required=True, help="path to webui.db (Open WebUI stopped for --apply)")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run, read-only)")
    ap.add_argument("--restore-file", help="with --apply: the NEW file that records every changed cell")
    ap.add_argument("--restore", metavar="RESTORE_FILE", help="put back the cells a restore file recorded")
    ap.add_argument("--wal-ok", action="store_true", help="write even though <db>-wal is not empty")
    args = ap.parse_args(argv)
    try:
        if args.restore:
            if args.restore_file:
                ap.error("--restore and --restore-file do not combine")
            return restore(args.db, args.restore, args.apply, args.wal_ok)
        return migrate(args.db, args.apply, args.restore_file, args.wal_ok)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    except sqlite3.Error as e:
        print(f"REFUSED: sqlite: {e} (nothing written)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
