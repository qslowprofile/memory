#!/usr/bin/env python3
"""LLM 后端模块：为 memory-knowledge-auto-migrate skill 提供可选 AI 增强能力。

设计原则：
- 零新 pip 依赖：仅使用 Python 标准库
- 全部 LLM 调用为可选增强：任何失败均静默降级，不抛异常
- 支持从 ~/.openclaw/agents/main/agent/models.json 自动发现配置
- 支持环境变量覆盖：CATCLAW_LLM_BASE_URL / CATCLAW_LLM_API_KEY / CATCLAW_LLM_MODEL
- SSE 流解析：处理 "data:data: " 双前缀格式
"""

from __future__ import annotations

import json
import os
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


# ──────────────────────────────────────────────
# 抽象基类
# ──────────────────────────────────────────────

class LLMBackend:
    """抽象基类。所有方法失败时返回 None，不抛异常。"""

    def complete(
        self,
        prompt: str,
        max_tokens: int = 200,
        temperature: float = 0.0,
    ) -> Optional[str]:
        """发送 prompt，返回 completion 文本；失败返回 None。"""
        raise NotImplementedError

    def is_available(self) -> bool:
        """返回 True 表示该 backend 已正确初始化并可用。"""
        raise NotImplementedError


# ──────────────────────────────────────────────
# Noop 回退
# ──────────────────────────────────────────────

class NoopLLMBackend(LLMBackend):
    """无模型回退：始终返回 None，is_available() = False。"""

    def complete(
        self,
        prompt: str,
        max_tokens: int = 200,
        temperature: float = 0.0,
    ) -> Optional[str]:
        return None

    def is_available(self) -> bool:
        return False


# ──────────────────────────────────────────────
# Catclaw 后端
# ──────────────────────────────────────────────

_MODELS_JSON_DEFAULT = Path("~/.openclaw/agents/main/agent/models.json").expanduser()

_CATCLAW_PROVIDER_KEY = "kubeplex-maas"
_CATCLAW_DEFAULT_MODEL = "catclaw-proxy-model"
_CATCLAW_DEFAULT_BASE_URL = ""  # 无内置默认值，从 models.json 或环境变量获取
_CATCLAW_DEFAULT_API_KEY = "catpaw"


def _load_models_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except Exception:
        return None


def _discover_catclaw_config() -> Optional[Dict[str, Any]]:
    """尝试从 models.json 中读取 catclaw 配置。"""
    data = _load_models_json(_MODELS_JSON_DEFAULT)
    if not isinstance(data, dict):
        return None
    providers = data.get("providers")
    if not isinstance(providers, dict):
        return None

    # 优先查找 kubeplex-maas provider
    provider = providers.get(_CATCLAW_PROVIDER_KEY)
    if not isinstance(provider, dict):
        # 兜底：找第一个含 openai-completions api 或 catclaw base_url 的 provider
        found = None
        for v in providers.values():
            if not isinstance(v, dict):
                continue
            api = str(v.get("api", "")).lower()
            base_url = str(v.get("baseUrl", "") or v.get("base_url", "")).lower()
            if "openai" in api or "catclaw" in base_url or "mmc.sankuai" in base_url:
                found = v
                break
        if found is None:
            return None
        provider = found

    base_url = str(provider.get("baseUrl") or provider.get("base_url") or "").rstrip("/")
    api_key = str(provider.get("apiKey") or provider.get("api_key") or "")
    headers = provider.get("headers") or {}
    if not isinstance(headers, dict):
        headers = {}

    # 从 models 列表里取第一个 model id
    models_list = provider.get("models")
    model_id = _CATCLAW_DEFAULT_MODEL
    if isinstance(models_list, list) and models_list:
        first = models_list[0]
        if isinstance(first, dict):
            model_id = str(first.get("id") or _CATCLAW_DEFAULT_MODEL)
        elif isinstance(first, str):
            model_id = first

    return {
        "base_url": base_url or _CATCLAW_DEFAULT_BASE_URL,
        "api_key": api_key or _CATCLAW_DEFAULT_API_KEY,
        "model": model_id,
        "headers": dict(headers),
    }


def _extract_content_from_chunk(obj: Any) -> Optional[str]:
    """从单个 SSE chunk JSON 对象中提取 content 文本。"""
    if not isinstance(obj, dict):
        return None

    # 直接 content 字段（lastOne 格式）
    direct = obj.get("content")
    if isinstance(direct, str) and direct:
        return direct

    # OpenAI choices[0].delta.content 格式
    choices = obj.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0]
        if isinstance(choice, dict):
            delta = choice.get("delta") or {}
            if isinstance(delta, dict):
                c = delta.get("content")
                if isinstance(c, str):
                    return c
            # 非流式格式
            message = choice.get("message") or {}
            if isinstance(message, dict):
                c = message.get("content")
                if isinstance(c, str):
                    return c
    return None


def _parse_sse_response(body: str) -> Optional[str]:
    """解析 SSE 流响应，返回完整累积文本。

    格式特征：
    - 每行形如 `data:data: {json}` 或 `data: {json}`
    - 最后一行为 `data:data: [DONE]` 或 `data: [DONE]`
    - 最后一个非 DONE chunk 可能有 `lastOne: true`，`content` 字段是完整文本
    """
    last_content: Optional[str] = None
    accumulated: List[str] = []

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # 剥离 SSE 前缀，支持 "data:data: " 和 "data: " 两种形式
        payload = line
        if payload.startswith("data:data:"):
            payload = payload[len("data:data:"):].lstrip(" ")
        elif payload.startswith("data:"):
            payload = payload[len("data:"):].lstrip(" ")
        else:
            continue

        if payload == "[DONE]":
            break

        try:
            obj = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            continue

        # 优先：lastOne=true 的 content 字段（完整累积文本）
        if obj.get("lastOne") is True:
            content = _extract_content_from_chunk(obj)
            if content is not None:
                last_content = content
            break

        # 普通 delta chunk
        content = _extract_content_from_chunk(obj)
        if content:
            accumulated.append(content)

    if last_content is not None:
        return last_content
    if accumulated:
        return "".join(accumulated)
    return None


class CatclawLLMBackend(LLMBackend):
    """从 models.json 自动读取配置，调用 catclaw SSE 接口。

    优先级（高→低）：
    1. 环境变量：CATCLAW_LLM_BASE_URL / CATCLAW_LLM_API_KEY / CATCLAW_LLM_MODEL
    2. ~/.openclaw/agents/main/agent/models.json
    3. 内置默认值
    """

    def __init__(self) -> None:
        cfg = _discover_catclaw_config() or {}

        self._base_url: str = (
            os.environ.get("CATCLAW_LLM_BASE_URL")
            or cfg.get("base_url")
            or _CATCLAW_DEFAULT_BASE_URL
        ).rstrip("/")

        self._api_key: str = (
            os.environ.get("CATCLAW_LLM_API_KEY")
            or cfg.get("api_key")
            or _CATCLAW_DEFAULT_API_KEY
        )

        self._model: str = (
            os.environ.get("CATCLAW_LLM_MODEL")
            or cfg.get("model")
            or _CATCLAW_DEFAULT_MODEL
        )

        self._extra_headers: Dict[str, str] = dict(cfg.get("headers") or {})

        # 验证最低条件
        self._available: bool = bool(self._base_url and self._api_key and self._model)
        if not self._available:
            import sys
            print(
                "[llm_backend] LLM backend 未配置（base_url/api_key/model 缺失），"
                "已回退规则模式。可通过环境变量 CATCLAW_LLM_BASE_URL / "
                "CATCLAW_LLM_API_KEY / CATCLAW_LLM_MODEL 显式配置。",
                file=sys.stderr,
            )

    def __repr__(self) -> str:
        masked_key = (self._api_key[:4] + "****") if len(self._api_key) > 4 else "****"
        return f"CatclawLLMBackend(model={self._model!r}, available={self._available}, key={masked_key})"

    def is_available(self) -> bool:
        return self._available

    def complete(
        self,
        prompt: str,
        max_tokens: int = 200,
        temperature: float = 0.0,
    ) -> Optional[str]:
        """调用 LLM 并返回 completion 文本，任何异常均返回 None。"""
        if not self._available:
            return None
        try:
            return self._do_complete(prompt, max_tokens=max_tokens, temperature=temperature)
        except Exception:
            return None

    def _do_complete(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        url = f"{self._base_url}/chat/completions"
        conversation_id = self._extra_headers.get("X-Conversation-Id") or str(uuid.uuid4())

        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._api_key,
            "X-Conversation-Id": conversation_id,
        }
        # 合并 models.json 中的额外 headers
        for k, v in self._extra_headers.items():
            if k and v:
                headers[k] = str(v)

        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw_body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            try:
                exc.read()
            except Exception:
                pass
            return None
        except Exception:
            return None

        result = _parse_sse_response(raw_body)
        if result is not None:
            return result.strip() or None
        return None


# ──────────────────────────────────────────────
# 工厂函数
# ──────────────────────────────────────────────

def get_default_backend() -> LLMBackend:
    """尝试初始化 CatclawLLMBackend；失败则返回 NoopLLMBackend。

    此函数永远不抛异常，适合在任何脚本的顶层安全调用。
    """
    try:
        backend = CatclawLLMBackend()
        if backend.is_available():
            return backend
        # 已在 CatclawLLMBackend.__init__ 里打印了降级原因
        return NoopLLMBackend()
    except Exception:
        return NoopLLMBackend()
