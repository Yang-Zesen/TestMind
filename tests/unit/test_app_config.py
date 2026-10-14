import pytest

from testmind.core.config import get_config


def test_app_port_defaults_to_26104(monkeypatch):
    monkeypatch.delenv("APP_PORT", raising=False)
    config = get_config.__globals__["Config"].from_env()
    assert config.app_port == 26104


def test_app_port_is_read_from_environment(monkeypatch):
    monkeypatch.setenv("APP_PORT", "9090")
    config = get_config.__globals__["Config"].from_env()
    assert config.app_port == 9090


def test_ai_node_timeout_is_configurable(monkeypatch):
    monkeypatch.setenv("AI_NODE_TIMEOUT_SECONDS", "2.5")
    config = get_config.__globals__["Config"].from_env()
    assert config.ai_node_timeout_seconds == 2.5


def test_ai_node_timeout_rejects_non_finite_values():
    with pytest.raises(ValueError):
        get_config.__globals__["Config"](ai_node_timeout_seconds=float("inf"))


def test_model_defaults_are_project_local_even_before_weights_exist(monkeypatch, tmp_path):
    from testmind.core import config as module

    monkeypatch.delenv("BGE_M3_PATH", raising=False)
    monkeypatch.delenv("BGE_RERANKER_BASE", raising=False)
    monkeypatch.setattr(module, "_ROOT_DIR", tmp_path / "checkout")
    config = module.Config.from_env()
    assert config.embedding_model == str(tmp_path / "checkout/pretrained_model/bge-m3")
    assert config.reranker_model == str(tmp_path / "checkout/pretrained_model/bge-reranker-base")


def test_relative_model_paths_are_anchored_to_checkout_not_working_directory(monkeypatch, tmp_path):
    from testmind.core import config as module

    monkeypatch.setattr(module, "_ROOT_DIR", tmp_path / "checkout")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BGE_M3_PATH", "pretrained_model/bge-m3")
    monkeypatch.setenv("BGE_RERANKER_BASE", "pretrained_model/bge-reranker-base")
    config = module.Config.from_env()
    assert config.embedding_model == str(tmp_path / "checkout/pretrained_model/bge-m3")
    assert config.reranker_model == str(tmp_path / "checkout/pretrained_model/bge-reranker-base")


def test_default_models_do_not_probe_other_checkouts(monkeypatch, tmp_path):
    from pathlib import Path

    from testmind.core import config as module

    monkeypatch.delenv("BGE_M3_PATH", raising=False)
    monkeypatch.delenv("BGE_RERANKER_BASE", raising=False)
    monkeypatch.setattr(module, "_ROOT_DIR", tmp_path)

    def forbidden_probe(*args):
        raise AssertionError("Model resolution must not search other repositories")

    monkeypatch.setattr(Path, "is_dir", forbidden_probe)
    assert module.Config.from_env().embedding_model == str(tmp_path / "pretrained_model/bge-m3")


def test_mock_knowledge_auth_is_explicit_and_development_only(monkeypatch):
    from testmind.core.config import Config

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("KNOWLEDGE_AUTH_MODE", "mock")
    assert Config.from_env().knowledge_auth_mode == "mock"

    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError):
        Config.from_env()
