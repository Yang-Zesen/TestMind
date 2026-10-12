import pytest

from testmind.infrastructure.graph_sync.runtime import GraphSettings


def test_graph_runtime_is_disabled_by_default():
    settings = GraphSettings.from_env({})
    assert not settings.enabled


def test_enabled_runtime_requires_separate_read_and_write_credentials():
    with pytest.raises(ValueError):
        GraphSettings.from_env({"GRAPH_SYNC_ENABLED": "true"})


def test_event_secret_requires_explicit_project_mapping():
    with pytest.raises(ValueError):
        GraphSettings.from_env({"GRAPH_EVENT_SECRET": "secret"})


def test_worker_loads_dotenv_without_overriding_exported_environment(tmp_path, monkeypatch):
    from testmind.infrastructure.graph_sync.runtime import load_worker_environment

    dotenv = tmp_path / ".env"
    dotenv.write_text("GRAPH_SYNC_ENABLED=true\nPOSTGRES_DSN=file-dsn\n", encoding="utf-8")
    monkeypatch.setenv("GRAPH_SYNC_ENABLED", "false")
    monkeypatch.delenv("GRAPH_SYNC_ENABLED")
    monkeypatch.setenv("POSTGRES_DSN", "exported-dsn")
    load_worker_environment(dotenv)
    import os

    assert os.environ["GRAPH_SYNC_ENABLED"] == "true"
    assert os.environ["POSTGRES_DSN"] == "exported-dsn"
