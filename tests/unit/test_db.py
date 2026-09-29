"""utils/db.py DB 连接层测试。"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from emotion_core.utils import db


class TestExpand:
    def test_expands_home(self):
        result = db._expand("~/.dbconfig")
        assert "~" not in result
        assert result.startswith("/")


class TestReadSecrets:
    def test_parses_valid_config(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".dbconfig", delete=False) as f:
            f.write("$RDSHOST=127.0.0.1\n")
            f.write("$DB_PW=testpassword\n")
            f.write("# comment line\n")
            f.write("not a config line\n")
            temp_path = f.name

        try:
            # Reset cache
            db._secrets_cache = None
            db._secrets_mtime = 0.0

            with patch.object(db, "_DBCONFIG_FILE", temp_path):
                result = db._read_secrets()

            assert "$RDSHOST" in result
            assert result["$RDSHOST"] == "127.0.0.1"
            assert "$DB_PW" in result
            assert result["$DB_PW"] == "testpassword"
            assert "# comment" not in result
        finally:
            Path(temp_path).unlink()

    def test_missing_file_raises(self):
        db._secrets_cache = None
        db._secrets_mtime = 0.0
        with patch.object(db, "_DBCONFIG_FILE", "/nonexistent/path"):
            with pytest.raises(RuntimeError, match="读不到"):
                db._read_secrets()


class TestReadDbconfig:
    def test_valid_config(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".dbconfig", delete=False) as f:
            f.write("$RDSHOST=127.0.0.1\n")
            f.write("$DB_PW=testpassword\n")
            temp_path = f.name

        try:
            db._secrets_cache = None
            db._secrets_mtime = 0.0

            with patch.object(db, "_DBCONFIG_FILE", temp_path):
                result = db.read_dbconfig()

            assert result["host"] == "127.0.0.1"
            assert result["password"] == "testpassword"
            assert result["port"] == 5432
            assert result["dbname"] == "emotion_core"
            assert result["user"] == "postgres"
        finally:
            Path(temp_path).unlink()

    def test_missing_host_raises(self):
        db._secrets_cache = None
        db._secrets_mtime = 0.0
        with patch.object(db, "_DBCONFIG_FILE", "/nonexistent"):
            with pytest.raises(RuntimeError):
                db.read_dbconfig()


class TestTransaction:
    def test_commits_on_success(self):
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("emotion_core.utils.db.psycopg.connect", return_value=mock_conn):
            with db.transaction() as conn:
                assert conn is mock_conn

            mock_conn.commit.assert_called_once()

    def test_rollback_on_exception(self):
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("emotion_core.utils.db.psycopg.connect", return_value=mock_conn):
            try:
                with db.transaction() as conn:
                    raise ValueError("test error")
            except ValueError:
                pass

            mock_conn.rollback.assert_called_once()
