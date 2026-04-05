from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import yaml

from .credentials import get_openai_api_key, get_openai_api_key_encrypted, load_global_openai_profile

@dataclass
class OpenAIConfig:
    base_url: str
    api_key: str
    model: str


def load_openai_config() -> OpenAIConfig:
    """
    加载 OpenAI 兼容接口配置（按优先级）：
    1) 环境变量：AISE_OPENAI_{API_KEY,BASE_URL,MODEL}
    2) 当前仓库的 aise.yml: openai: {api_key, base_url, model}
    3) 本地文件（不建议提交到 git）：
       - .aise/openai.json|yaml
       - aise_openai.json|yaml
    """

    def _coalesce(a: str, b: str) -> str:
        return a if a else b

    base_url_env = os.environ.get("AISE_OPENAI_BASE_URL", "")
    api_key_env = os.environ.get("AISE_OPENAI_API_KEY", "")
    model_env = os.environ.get("AISE_OPENAI_MODEL", "")

    # defaults
    base_url = _coalesce(base_url_env, "https://api.openai.com/v1")
    model = _coalesce(model_env, "gpt-4.1-mini")
    api_key = api_key_env

    # 0) 全局 profile（非敏感：base_url/model）
    if not base_url_env or not model_env:
        gp = load_global_openai_profile()
        if gp:
            base_url = _coalesce(base_url_env, gp.base_url)
            model = _coalesce(model_env, gp.model)

    cwd = Path.cwd()

    def _load_mapping_from_path(p: Path) -> dict[str, Any] | None:
        if not p.exists() or not p.is_file():
            return None
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            return None
        try:
            if p.suffix.lower() in (".yml", ".yaml"):
                obj = yaml.safe_load(text) or {}
            else:
                obj = json.loads(text)
        except Exception:
            return None
        return obj if isinstance(obj, dict) else None

    # 2) aise.yml
    if not api_key:
        y = _load_mapping_from_path(cwd / "aise.yml")
        if isinstance(y, dict):
            o = y.get("openai") or y.get("llm") or {}
            if isinstance(o, dict):
                api_key = str(o.get("api_key") or o.get("apiKey") or api_key or "")
                base_url = _coalesce(str(o.get("base_url") or o.get("baseUrl") or ""), base_url)
                model = _coalesce(str(o.get("model") or ""), model)

    # 3) local config files
    if not api_key:
        candidates = [
            cwd / ".aise" / "openai.json",
            cwd / ".aise" / "openai.yaml",
            cwd / ".aise" / "openai.yml",
            cwd / "aise_openai.json",
            cwd / "aise_openai.yaml",
            cwd / "aise_openai.yml",
        ]
        for p in candidates:
            m = _load_mapping_from_path(p)
            if not isinstance(m, dict):
                continue
            api_key = str(m.get("api_key") or m.get("apiKey") or api_key or "")
            base_url = _coalesce(str(m.get("base_url") or m.get("baseUrl") or ""), base_url)
            model = _coalesce(str(m.get("model") or ""), model)
            if api_key:
                break

    # 4) keyring（敏感信息：api_key）
    if not api_key:
        api_key = get_openai_api_key(base_url=base_url)

    # 5) encrypted local store（keyring 不可用时的后备；要求 AISE_CRED_PASSPHRASE）
    if not api_key:
        api_key = get_openai_api_key_encrypted(base_url=base_url)

    if not api_key:
        raise RuntimeError(
            "缺少 OpenAI 配置：请设置环境变量 AISE_OPENAI_API_KEY，或在仓库根目录提供 aise.yml 的 openai.api_key，或提供 .aise/openai.json"
        )
    return OpenAIConfig(base_url=base_url, api_key=api_key, model=model)


class OpenAIClient:
    """
    极简 OpenAI 兼容 Chat Completions 客户端（支持 tools/function calling）。
    原型阶段：只实现我们需要的字段。
    """

    def __init__(self, config: OpenAIConfig):
        self.config = config
        self._client = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=60,
        )

    def close(self) -> None:
        self._client.close()

    def chat_completions(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": self.config.model, "messages": messages}
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice

        resp = self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        out = resp.json()

        # 兼容部分 OpenAI-compat 提供方：非 stream 返回可能缺失 message.content，
        # 但 stream delta 会给 content。此处在“无 tools”时做安全回退。
        try:
            msg = (((out.get("choices") or [{}])[0]).get("message") or {}) if isinstance(out, dict) else {}
            has_content = isinstance(msg, dict) and ("content" in msg) and isinstance(msg.get("content"), str)
        except Exception:
            has_content = False

        if has_content or tools:
            return out

        # stream fallback
        payload2 = dict(payload)
        payload2["stream"] = True
        content_parts: list[str] = []
        first_meta: dict[str, Any] | None = None
        usage: dict[str, Any] | None = None
        role = "assistant"

        with self._client.stream("POST", "/chat/completions", json=payload2) as r:
            r.raise_for_status()
            for raw_line in r.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8", "ignore") if isinstance(raw_line, (bytes, bytearray)) else raw_line
                line = line.strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try:
                    chunk = json.loads(line)
                except Exception:
                    continue
                if first_meta is None and isinstance(chunk, dict):
                    first_meta = {k: chunk.get(k) for k in ("id", "object", "created", "model")}
                if isinstance(chunk, dict) and isinstance(chunk.get("usage"), dict):
                    usage = chunk.get("usage")
                choices = chunk.get("choices") if isinstance(chunk, dict) else None
                if isinstance(choices, list) and choices:
                    delta = (choices[0].get("delta") or {}) if isinstance(choices[0], dict) else {}
                    if isinstance(delta, dict):
                        if isinstance(delta.get("role"), str):
                            role = delta["role"]
                        if isinstance(delta.get("content"), str):
                            content_parts.append(delta["content"])

        content = "".join(content_parts)
        meta = first_meta or {"id": out.get("id"), "object": out.get("object"), "created": out.get("created"), "model": out.get("model")}
        return {
            **{k: v for k, v in meta.items() if v is not None},
            "choices": [{"index": 0, "message": {"role": role, "content": content}, "finish_reason": "stop"}],
            "usage": usage or out.get("usage") or {},
        }
