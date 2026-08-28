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
    open_for_mode,
    read_mode_marker,
    schema_version,
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
