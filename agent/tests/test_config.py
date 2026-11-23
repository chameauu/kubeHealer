"""`config.py` — the repo-root `.env` is a best-effort default, never an override.

The loader reads the same syntax the deployment uses (`KEY=value`, `export`,
quotes and comments) and never adds a dependency. Tests copy the parsing rules
onto a temporary file; they never read the real `.env`, which holds a secret.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import config


def test_env_path_is_the_directory_containing_agent() -> None:
    source = Path(config.__file__).resolve()

    assert config.ENV_PATH == source.parents[2] / ".env"
    assert (config.ENV_PATH.parent / "agent").is_dir()


def test_dotenv_loader_sets_only_missing_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "LLM_PROVIDER=azure-v1\nLLM_API_KEY=from-file\nLLM_MODEL=gpt-4.1-mini\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "from-environment")

    config._load_dotenv(path)

    assert os.environ["LLM_PROVIDER"] == "azure-v1"
    assert os.environ["LLM_API_KEY"] == "from-environment"
    assert os.environ["LLM_MODEL"] == "gpt-4.1-mini"


def test_dotenv_loader_parses_quotes_export_and_comments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# a comment\n"
        "\n"
        "export LLM_BASE_URL='https://example.invalid/openai/v1'\n"
        'LLM_MODEL="gpt-test"  # trailing comment\n'
        "not a pair\n",
        encoding="utf-8",
    )
    for name in ("LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)

    config._load_dotenv(path)

    assert os.environ["LLM_BASE_URL"] == "https://example.invalid/openai/v1"
    assert os.environ["LLM_MODEL"] == "gpt-test"


def test_dotenv_loader_ignores_a_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("KUBEHEAL_DOTENV_PROBE", raising=False)

    config._load_dotenv(tmp_path / "absent.env")

    assert "KUBEHEAL_DOTENV_PROBE" not in os.environ
