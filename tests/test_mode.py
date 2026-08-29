from __future__ import annotations

from pathlib import Path

import pytest

from jscc.mode import (
    DEFAULT_MODE,
    ENV_VAR,
    InvalidModeError,
    Mode,
    resolve_db_path,
    resolve_mode,
)
from jscc.storage import (
    ModeMismatchError,
    _ensure_meta_table,
    connect,
    init_db,
    open_for_mode,
    read_mode_marker,
    schema_version,
    write_mode_marker,
)


def test_default_mode_is_synthetic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert resolve_mode() is Mode.synthetic
    assert DEFAULT_MODE is Mode.synthetic


def test_env_synthetic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "synthetic")
    assert resolve_mode() is Mode.synthetic


def test_env_real(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "real")
    assert resolve_mode() is Mode.real


def test_env_bogus_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "production")
    with pytest.raises(InvalidModeError, match="production"):
        resolve_mode()


def test_env_empty_string_is_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "")
    assert resolve_mode() is DEFAULT_MODE


def test_explicit_arg_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "real")
    assert resolve_mode(env_value="synthetic") is Mode.synthetic


def test_resolve_db_path_convention(tmp_path: Path) -> None:
    assert resolve_db_path(Mode.synthetic, tmp_path) == tmp_path / "synthetic.db"
    assert resolve_db_path(Mode.real, tmp_path) == tmp_path / "real.db"


def test_open_for_mode_stamps_marker_on_first_use(tmp_path: Path) -> None:
    conn = open_for_mode(Mode.synthetic, tmp_path)
    try:
        assert read_mode_marker(conn) is Mode.synthetic
        assert schema_version(conn) == 2
    finally:
        conn.close()


def test_open_for_mode_reopening_same_mode_ok(tmp_path: Path) -> None:
    open_for_mode(Mode.synthetic, tmp_path).close()
    conn = open_for_mode(Mode.synthetic, tmp_path)
    try:
        assert read_mode_marker(conn) is Mode.synthetic
    finally:
        conn.close()


def test_open_for_mode_cross_mode_raises(tmp_path: Path) -> None:
    """The core structural safety check: a DB stamped `real` cannot be opened as `synthetic`."""
    # This test uses a shared path deliberately — the point is that the same
    # underlying file, stamped once as real, refuses a synthetic reopen even
    # if someone flips JSCC_DATA later.
    real_path = tmp_path / "real.db"
    open_for_mode(Mode.real, tmp_path).close()
    assert real_path.exists()

    # Simulate a mode flip: point synthetic resolution at the same file.
    # We do this by copying, since resolve_db_path is convention-driven.
    (tmp_path / "synthetic.db").write_bytes(real_path.read_bytes())
    with pytest.raises(ModeMismatchError, match="stamped as 'real'"):
        open_for_mode(Mode.synthetic, tmp_path)


def test_populated_db_with_missing_marker_refused(tmp_path: Path) -> None:
    """C5 regression: a populated DB whose meta.mode row is missing must be
    refused rather than silently restamped with the caller's mode."""
    # Simulate the failure state: run full DDL, then delete the mode row.
    path = tmp_path / "synthetic.db"
    conn = connect(path)
    init_db(conn)
    write_mode_marker(conn, Mode.real)  # populate as real
    conn.execute("DELETE FROM meta WHERE key = 'mode'")
    conn.commit()
    conn.close()

    # Reopen as synthetic — must refuse, not silently restamp.
    with pytest.raises(ModeMismatchError, match="no mode marker"):
        open_for_mode(Mode.synthetic, tmp_path)

    # And still no marker was written (refusal was clean).
    conn2 = connect(path)
    try:
        assert read_mode_marker(conn2) is None
    finally:
        conn2.close()


def test_corrupt_marker_raises_mode_mismatch_not_valueerror(tmp_path: Path) -> None:
    """C6 regression: a tampered mode value must raise ModeMismatchError, not
    a bare ValueError that slips past `except ModeMismatchError:` guards."""
    path = tmp_path / "synthetic.db"
    conn = connect(path)
    init_db(conn)
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('mode', 'production')"
    )
    conn.commit()
    conn.close()

    with pytest.raises(ModeMismatchError, match="corrupt mode marker"):
        open_for_mode(Mode.synthetic, tmp_path)


def test_no_ddl_run_on_wrong_mode_db(tmp_path: Path) -> None:
    """C4 regression: opening a populated wrong-mode DB must not run init_db.
    Test: stamp a DB as real with an old (fake) schema version, try to open as
    synthetic, then confirm the schema version was not bumped."""
    path = tmp_path / "synthetic.db"
    conn = connect(path)
    init_db(conn)
    write_mode_marker(conn, Mode.real)
    # Force an obviously-wrong user_version to detect an unwanted DDL run.
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()

    with pytest.raises(ModeMismatchError):
        open_for_mode(Mode.synthetic, tmp_path)

    # Reopen bare and confirm user_version was NOT bumped by the refused open.
    conn2 = connect(path)
    try:
        assert schema_version(conn2) == 99
    finally:
        conn2.close()


def test_bare_meta_only_db_is_not_treated_as_populated(tmp_path: Path) -> None:
    """A DB containing ONLY the `meta` table (no user tables yet) is fresh and
    should proceed to full init. Guards against off-by-one in the populated check."""
    path = tmp_path / "synthetic.db"
    conn = connect(path)
    _ensure_meta_table(conn)
    conn.close()

    # Should succeed — no user tables means fresh.
    conn2 = open_for_mode(Mode.synthetic, tmp_path)
    try:
        assert read_mode_marker(conn2) is Mode.synthetic
        assert schema_version(conn2) == 2
    finally:
        conn2.close()


def test_open_for_mode_two_databases_coexist(tmp_path: Path) -> None:
    """Synthetic and real databases live side by side; opening one does not touch the other."""
    open_for_mode(Mode.synthetic, tmp_path).close()
    open_for_mode(Mode.real, tmp_path).close()
    assert (tmp_path / "synthetic.db").exists()
    assert (tmp_path / "real.db").exists()

    # Reopen each; markers unchanged.
    conn_s = open_for_mode(Mode.synthetic, tmp_path)
    conn_r = open_for_mode(Mode.real, tmp_path)
    try:
        assert read_mode_marker(conn_s) is Mode.synthetic
        assert read_mode_marker(conn_r) is Mode.real
    finally:
        conn_s.close()
        conn_r.close()
