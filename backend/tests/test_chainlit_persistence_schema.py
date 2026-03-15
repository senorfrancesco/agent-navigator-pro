import importlib
import os
import sqlite3
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def chainlit_persistence_module(monkeypatch):
    if "orchestrator.chainlit_app" in sys.modules:
        del sys.modules["orchestrator.chainlit_app"]
    module = importlib.import_module("orchestrator.chainlit_app")
    yield module
    sys.modules.pop("orchestrator.chainlit_app", None)


def test_bootstrap_chainlit_sqlite_schema_upgrades_existing_steps_table(tmp_path, chainlit_persistence_module):
    db_path = tmp_path / "chainlit.db"
    conninfo = f"sqlite+aiosqlite:///{db_path}"

    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE "steps" (
                "id" TEXT PRIMARY KEY,
                "name" TEXT,
                "type" TEXT,
                "threadId" TEXT
            );
            CREATE TABLE "threads" (
                "id" TEXT PRIMARY KEY,
                "createdAt" TEXT,
                "name" TEXT
            );
            CREATE TABLE "elements" (
                "id" TEXT PRIMARY KEY
            );
            """
        )
        conn.commit()

    chainlit_persistence_module._bootstrap_chainlit_sqlite_schema(conninfo)

    with sqlite3.connect(db_path) as conn:
        step_columns = {row[1] for row in conn.execute('PRAGMA table_info("steps")').fetchall()}
        thread_columns = {row[1] for row in conn.execute('PRAGMA table_info("threads")').fetchall()}
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert "command" in step_columns
    assert "defaultOpen" in step_columns
    assert "tags" in thread_columns
    assert "metadata" in thread_columns
    assert str(journal_mode).lower() == "wal"


@pytest.mark.asyncio
async def test_compatible_data_layer_serializes_thread_tags_as_json(chainlit_persistence_module):
    with patch(
        "chainlit.data.sql_alchemy.SQLAlchemyDataLayer.update_thread",
        new=AsyncMock(),
    ) as mock_update_thread:
        data_layer = object.__new__(chainlit_persistence_module._CompatibleSQLAlchemyDataLayer)
        await chainlit_persistence_module._CompatibleSQLAlchemyDataLayer.update_thread(
            data_layer,
            thread_id="thread-1",
            name="Thread Name",
            tags=["Agent Navigator"],
            metadata={"assistant_mode": "general_chat"},
        )

    kwargs = mock_update_thread.await_args.kwargs
    assert kwargs["thread_id"] == "thread-1"
    assert kwargs["name"] == "Thread Name"
    assert kwargs["tags"] == '["Agent Navigator"]'
    assert kwargs["metadata"] == {"assistant_mode": "general_chat"}


@pytest.mark.asyncio
async def test_compatible_data_layer_decodes_thread_tags_from_json(chainlit_persistence_module):
    with patch(
        "chainlit.data.sql_alchemy.SQLAlchemyDataLayer.get_all_user_threads",
        new=AsyncMock(
            return_value=[
                {
                    "id": "thread-1",
                    "tags": '["Agent Navigator"]',
                    "steps": [],
                    "elements": [],
                }
            ]
        ),
    ):
        data_layer = object.__new__(chainlit_persistence_module._CompatibleSQLAlchemyDataLayer)
        threads = await chainlit_persistence_module._CompatibleSQLAlchemyDataLayer.get_all_user_threads(
            data_layer,
            user_id="user-1",
        )

    assert threads
    assert threads[0]["tags"] == ["Agent Navigator"]


def test_compatible_data_layer_sets_sqlite_timeout_connect_args(chainlit_persistence_module, monkeypatch):
    captured = {}

    def fake_init(self, conninfo, connect_args=None, **kwargs):
        captured["conninfo"] = conninfo
        captured["connect_args"] = connect_args or {}
        captured["kwargs"] = kwargs
        self.engine = type("EngineHolder", (), {"sync_engine": object()})()

    monkeypatch.setenv("CHAINLIT_SQLITE_TIMEOUT_S", "45")

    with patch(
        "chainlit.data.sql_alchemy.SQLAlchemyDataLayer.__init__",
        new=fake_init,
    ), patch.object(
        chainlit_persistence_module.sqlalchemy_event,
        "listens_for",
        side_effect=lambda target, event_name: (lambda fn: fn),
    ):
        chainlit_persistence_module._CompatibleSQLAlchemyDataLayer(
            conninfo="sqlite+aiosqlite:///tmp/test.db",
            storage_provider=object(),
        )

    assert captured["conninfo"] == "sqlite+aiosqlite:///tmp/test.db"
    assert captured["connect_args"]["timeout"] == 45.0
