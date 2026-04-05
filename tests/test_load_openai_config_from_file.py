from __future__ import annotations

import json
from pathlib import Path

import pytest

from aise.llm_openai import load_openai_config


def test_load_openai_config_from_aise_openai_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AISE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AISE_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("AISE_OPENAI_MODEL", raising=False)

    (tmp_path / "aise_openai.json").write_text(
        json.dumps(
            {
                "api_key": "dummy",
                "base_url": "https://example.com/v1",
                "model": "gpt-4.1-mini",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    cfg = load_openai_config()
    assert cfg.api_key == "dummy"
    assert cfg.base_url == "https://example.com/v1"
    assert cfg.model == "gpt-4.1-mini"

