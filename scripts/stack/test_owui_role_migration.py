"""Tests for owui_role_migration.py (model-roles, item mr-consumers).

Hermetic: every test builds a throwaway SQLite database with Open WebUI 0.11.0's
own `model` and `config` DDL (copied from a live 0.11.0 webui.db, read-only) and
the shapes the live rows have. The real proof is the webui.db COPY in the item's
test plan.
"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


import owui_role_migration as m

MODEL_DDL = """CREATE TABLE "model" (
	id TEXT NOT NULL,
	user_id TEXT NOT NULL,
	base_model_id TEXT,
	name TEXT NOT NULL,
	meta TEXT NOT NULL,
	params TEXT NOT NULL,
	created_at INTEGER NOT NULL,
	updated_at INTEGER NOT NULL,
	is_active BOOLEAN DEFAULT 1 NOT NULL,
	CONSTRAINT pk_model PRIMARY KEY (id)
)"""
CONFIG_DDL = """CREATE TABLE config (
	"key" TEXT NOT NULL,
	value JSON NOT NULL,
	updated_at BIGINT,
	PRIMARY KEY ("key")
)"""

ORDER = ["arena-model", "code", "qwen36-27b", "gemma3:4b", "qllama/bge-m3:latest", "bge-m3", "general"]


def make_db(tmp_path: Path, *, presets=None, config=None, wal=False) -> Path:
    db = tmp_path / "webui.db"
    c = sqlite3.connect(db)
    if wal:  # the live webui.db is in WAL mode
        c.execute("PRAGMA journal_mode=wal")
    c.execute(MODEL_DDL)
    c.execute(CONFIG_DDL)
    rows = presets if presets is not None else [
        ("code", "qwen36-27b"), ("research", "qwen36-27b"), ("nothink-preset", "qwen36-27b:nothink"),
        ("gemma-preset", "gemma3:4b"), ("qwen36-27b", None), ("bge-m3", None),
    ]
    for i, (pid, base) in enumerate(rows):
        c.execute("INSERT INTO model VALUES (?,?,?,?,?,?,?,?,?)",
                  (pid, "u1", base, pid.title(), '{"description": "x"}', '{"temperature": 0.7}',
                   1700000000 + i, 1700000100 + i, 1))
    cfg = config if config is not None else {
        "rag.embedding_engine": "openai",
        "rag.embedding_model": "bge-m3-f16.gguf",
        "ui.model_order_list": ORDER,
        "ui.default_models": "general",
        "task.model.default": "",
    }
    for k, v in cfg.items():
        c.execute("INSERT INTO config VALUES (?,?,?)", (k, json.dumps(v), 1787230684))
    c.commit()
    c.close()
    return db


def snapshot(db: Path) -> dict:
    """Every cell of both tables with its storage class - the byte-identity oracle."""
    c = sqlite3.connect(db)
    out = {}
    for t, key in (("model", "id"), ("config", "key")):
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
        sel = ", ".join(f'typeof("{x}"), "{x}"' for x in cols)
        for row in c.execute(f'SELECT "{key}", {sel} FROM "{t}"'):
            out[(t, row[0])] = row[1:]
    c.close()
    return out


def run_err(argv) -> tuple[int, str, str]:
    import contextlib
    o, e = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
        rc = m.main([str(a) for a in argv])
    return rc, o.getvalue(), e.getvalue()


def run(argv) -> tuple[int, str]:
    buf = io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(buf):
        rc = m.main([str(a) for a in argv])
    return rc, buf.getvalue()


def test_dry_run_prints_the_exact_cells_and_writes_nothing(tmp_path):
    db = make_db(tmp_path)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    rc, out = run(["--db", db])
    assert rc == 0
    assert "DRY RUN" in out
    assert "model.code" in out and "model.research" in out and "model.nothink-preset" in out
    assert "local-large:nothink" in out
    assert "config.rag.embedding_model" in out and "local-embed" in out
    assert "config.ui.model_order_list" in out
    assert "gemma-preset" not in out
    assert "4 cell(s) would change" not in out and "5 cell(s) would change" in out
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert not list(tmp_path.glob("*.json"))


def test_apply_changes_only_the_planned_cells_and_a_second_run_changes_nothing(tmp_path):
    db = make_db(tmp_path)
    s0 = snapshot(db)
    rf = tmp_path / "restore.json"
    rc, out = run(["--db", db, "--apply", "--restore-file", rf])
    assert rc == 0, out
    s1 = snapshot(db)
    changed = {k for k in s0 if s0[k] != s1[k]}
    assert changed == {("model", "code"), ("model", "research"), ("model", "nothink-preset"),
                       ("config", "rag.embedding_model"), ("config", "ui.model_order_list")}
    c = sqlite3.connect(db)
    assert c.execute("SELECT base_model_id FROM model WHERE id='code'").fetchone()[0] == "local-large"
    assert c.execute("SELECT base_model_id FROM model WHERE id='nothink-preset'").fetchone()[0] == \
        "local-large:nothink"
    assert json.loads(c.execute("SELECT value FROM config WHERE key='rag.embedding_model'").fetchone()[0]) \
        == "local-embed"
    order = json.loads(c.execute("SELECT value FROM config WHERE key='ui.model_order_list'").fetchone()[0])
    assert order == ["arena-model", "code", "local-large", "gemma3:4b", "local-embed", "general"]
    # only the named column moved in each changed row
    for k in changed:
        cols = [i for i, (a, b) in enumerate(zip(s0[k], s1[k])) if a != b]
        assert len(cols) == 1, (k, cols)
    c.close()
    rc2, out2 = run(["--db", db, "--apply", "--restore-file", tmp_path / "restore2.json"])
    assert rc2 == 0 and "nothing to change" in out2
    assert snapshot(db) == s1
    assert not (tmp_path / "restore2.json").exists()


def test_restore_puts_every_cell_back_byte_identical(tmp_path):
    db = make_db(tmp_path)
    s0 = snapshot(db)
    rf = tmp_path / "restore.json"
    assert run(["--db", db, "--apply", "--restore-file", rf])[0] == 0
    rc, out = run(["--db", db, "--restore", rf])
    assert rc == 0 and "would be restored" in out
    assert snapshot(db) != s0  # the dry run wrote nothing
    rc, out = run(["--db", db, "--restore", rf, "--apply"])
    assert rc == 0 and "restored." in out
    assert snapshot(db) == s0
    rc, out = run(["--db", db, "--restore", rf, "--apply"])
    assert rc == 0 and "nothing to restore" in out


def test_restore_refuses_whole_when_a_cell_moved_since(tmp_path):
    db = make_db(tmp_path)
    rf = tmp_path / "restore.json"
    assert run(["--db", db, "--apply", "--restore-file", rf])[0] == 0
    c = sqlite3.connect(db)
    c.execute("UPDATE model SET base_model_id='gemma3:4b' WHERE id='research'")
    c.commit()
    c.close()
    s = snapshot(db)
    for extra in ([], ["--apply"]):                  # the dry run refuses too, not "would restore"
        rc, out, err = run_err(["--db", db, "--restore", rf, *extra])
        assert rc == 1, extra
        assert "model.research.base_model_id (now gemma3:4b)" in err and "would be restored" not in out
    assert snapshot(db) == s
    # the documented way out: put the drifted cell back by hand, then the restore completes
    c = sqlite3.connect(db)
    c.execute("UPDATE model SET base_model_id='qwen36-27b' WHERE id='research'")
    c.commit()
    c.close()
    rc, out = run(["--db", db, "--restore", rf, "--apply"])
    assert rc == 0 and "1 cell(s) already hold their original value" in out


def test_apply_refuses_without_a_restore_file_or_onto_an_existing_one(tmp_path):
    db = make_db(tmp_path)
    s = snapshot(db)
    assert run(["--db", db, "--apply"])[0] == 1
    rf = tmp_path / "exists.json"
    rf.write_text("keep me", encoding="utf-8")
    assert run(["--db", db, "--apply", "--restore-file", rf])[0] == 1
    assert rf.read_text(encoding="utf-8") == "keep me"
    assert snapshot(db) == s


def _hold_open(db: Path, *, write: str | None = None) -> subprocess.Popen:
    """Another PROCESS with the database open, as an idle Open WebUI keeps its pooled
    connections; with `write`, it commits that statement first. Stops when stdin closes."""
    code = ("import sqlite3, sys\n"
            f"c = sqlite3.connect({str(db)!r})\n"
            "c.execute('select count(*) from model').fetchone()\n"
            + (f"c.execute({write!r}); c.commit()\n" if write else "")
            + "print('ready', flush=True)\nsys.stdin.read()\n")
    p = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "ready"
    return p


def _files(d: Path) -> dict:
    return {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(d.iterdir()) if f.is_file()}


def test_apply_and_restore_are_refused_while_another_process_has_the_db_open(tmp_path):
    db = make_db(tmp_path, wal=True)
    rf = tmp_path / "r.json"
    p = _hold_open(db)
    try:
        rc, out, err = run_err(["--db", db, "--apply", "--restore-file", rf, "--lock-timeout", "0.2"])
        assert rc == 1 and "another process holds a lock" in err
        assert not rf.exists()
    finally:
        p.stdin.close()
        p.wait()
    s = snapshot(db)
    assert s[("model", "code")][5] == "qwen36-27b"                   # nothing written
    assert run(["--db", db, "--apply", "--restore-file", rf])[0] == 0  # it goes once the holder is gone
    p = _hold_open(db)
    try:
        rc, _out, err = run_err(["--db", db, "--restore", rf, "--apply", "--lock-timeout", "0.2"])
        assert rc == 1 and "another process holds a lock" in err
    finally:
        p.stdin.close()
        p.wait()
    assert snapshot(db)[("model", "code")][5] == "local-large"


def test_dry_run_creates_nothing_beside_a_wal_mode_db(tmp_path):
    db = make_db(tmp_path, wal=True)
    before = _files(tmp_path)
    assert set(before) == {"webui.db"}
    rc, out = run(["--db", db])
    assert rc == 0 and "5 cell(s) would change" in out
    assert _files(tmp_path) == before                               # no -wal, no -shm, same bytes


def test_dry_run_reads_an_unclean_wal_through_a_copy_and_leaves_it_alone(tmp_path):
    db = make_db(tmp_path, wal=True)
    # a committed change that lives ONLY in the WAL: the writer dies without closing
    code = ("import sqlite3, os\n"
            f"c = sqlite3.connect({str(db)!r})\n"
            "c.execute(\"UPDATE model SET base_model_id='qwen36-27b' WHERE id='gemma-preset'\")\n"
            "c.commit()\nos._exit(0)\n")
    subprocess.run([sys.executable, "-c", code], check=True)
    assert (tmp_path / "webui.db-wal").stat().st_size > 0
    before = _files(tmp_path)
    rc, out = run(["--db", db])
    assert rc == 0 and "temporary COPY" in out
    assert "model.gemma-preset" in out and "6 cell(s) would change" in out   # the WAL row is seen
    assert _files(tmp_path) == before                               # the live files are untouched
    rf = tmp_path / "r.json"
    rc, out = run(["--db", db, "--apply", "--restore-file", rf])    # apply recovers the WAL itself
    assert rc == 0 and "6 cell(s) to change" in out
    assert snapshot(db)[("model", "gemma-preset")][5] == "local-large"


def test_a_value_json_dumps_cannot_reproduce_is_refused_not_reformatted(tmp_path):
    db = make_db(tmp_path)
    c = sqlite3.connect(db)
    c.execute("UPDATE config SET value=? WHERE key='ui.model_order_list'", ('["code","qwen36-27b"]',))
    c.commit()
    c.close()
    s = snapshot(db)
    rc, _ = run(["--db", db, "--apply", "--restore-file", tmp_path / "r.json"])
    assert rc == 1
    assert snapshot(db) == s and not (tmp_path / "r.json").exists()


def test_embedding_model_is_left_alone_when_the_engine_is_not_openai(tmp_path):
    db = make_db(tmp_path, config={"rag.embedding_engine": "", "rag.embedding_model": "bge-m3"})
    rc, out = run(["--db", db])
    assert rc == 0
    assert "not changed" in out and "config.rag.embedding_model" not in out


def test_other_config_keys_naming_an_old_id_are_reported_not_changed(tmp_path):
    db = make_db(tmp_path, config={"rag.embedding_engine": "openai", "task.model.default": "qwen36-27b"})
    s = snapshot(db)
    rc, out = run(["--db", db, "--apply", "--restore-file", tmp_path / "r.json"])
    assert rc == 0
    assert "'task.model.default' mentions qwen36-27b: not changed" in out
    changed = {k for k, v in snapshot(db).items() if s[k] != v}
    assert all(t == "model" for t, _ in changed)


def test_order_list_keeps_a_role_that_is_already_listed(tmp_path):
    db = make_db(tmp_path, config={"ui.model_order_list": ["local-large", "a", "qwen36-27b", "b",
                                                           "bge-m3", "local-embed"]})
    assert run(["--db", db, "--apply", "--restore-file", tmp_path / "r.json"])[0] == 0
    c = sqlite3.connect(db)
    order = json.loads(c.execute("SELECT value FROM config WHERE key='ui.model_order_list'").fetchone()[0])
    c.close()
    assert order == ["local-large", "a", "b", "local-embed"]


def test_a_failure_after_the_restore_file_rolls_back_and_removes_it(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    s0 = snapshot(db)
    real = m._untyped
    calls = {"n": 0}

    def boom(t):
        calls["n"] += 1
        if calls["n"] == 3:                          # mid-way through the UPDATEs
            raise sqlite3.OperationalError("disk I/O error (simulated)")
        return real(t)

    monkeypatch.setattr(m, "_untyped", boom)
    rf = tmp_path / "r.json"
    rc, _out, err = run_err(["--db", db, "--apply", "--restore-file", rf])
    assert rc == 1 and "simulated" in err
    assert snapshot(db) == s0 and not rf.exists()


def test_not_an_open_webui_database_is_refused(tmp_path):
    db = tmp_path / "other.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE config (id INTEGER, data TEXT)")
    c.execute("CREATE TABLE model (id TEXT, base_model_id TEXT)")
    c.commit()
    c.close()
    assert run(["--db", db])[0] == 1


def test_restore_file_records_storage_classes(tmp_path):
    db = make_db(tmp_path)
    rf = tmp_path / "restore.json"
    assert run(["--db", db, "--apply", "--restore-file", rf])[0] == 0
    rec = json.loads(rf.read_text(encoding="utf-8"))
    assert rec["format"] == m.RESTORE_FORMAT
    kinds = {(c["table"], c["key"]): (c["before"]["type"], c["after"]["type"]) for c in rec["cells"]}
    assert kinds[("model", "code")] == ("text", "text")
    assert kinds[("config", "rag.embedding_model")] == ("text", "text")
    assert {c["key"] for c in rec["cells"]} == {"code", "research", "nothink-preset",
                                               "rag.embedding_model", "ui.model_order_list"}


def test_restore_compares_the_storage_class_not_only_the_bytes(tmp_path):
    """A cell holding the migrated TEXT as a BLOB of the same bytes is neither value: the restore
    refuses it (dry run and apply) rather than treat equal-looking bytes as its own write."""
    db = make_db(tmp_path)
    rf = tmp_path / "restore.json"
    assert run(["--db", db, "--apply", "--restore-file", rf])[0] == 0
    c = sqlite3.connect(db)
    c.execute("UPDATE model SET base_model_id = CAST('local-large' AS BLOB) WHERE id='code'")
    c.commit()
    c.close()
    s = snapshot(db)
    assert s[("model", "code")][4:6] == ("blob", b"local-large")
    for extra in ([], ["--apply"]):
        rc, _out, err = run_err(["--db", db, "--restore", rf, *extra])
        assert rc == 1 and "model.code.base_model_id" in err, extra
    assert snapshot(db) == s


def test_same_is_storage_class_exact():
    """`_same` is the restore's comparison: equal value AND equal SQLite storage class (Python type),
    so INTEGER 1 is not REAL 1.0 even though 1 == 1.0, and TEXT is never a BLOB of its bytes."""
    assert m._same("local-large", "local-large")
    assert not m._same(1, 1.0) and not m._same(1, True)
    assert not m._same("local-large", b"local-large") and not m._same(None, "")
    for v in ("x", b"x", 1, 1.5, None):                     # a restore file round-trips each class
        assert m._same(m._untyped(m._typed(v)), v)
