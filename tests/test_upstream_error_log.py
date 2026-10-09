"""上游失敗必須留下來源（2026-10-09：某家 400 只回通用字串、code/param 全空）。

`rag._rstatus` 是全部 LLM provider（openrouter／zen／nvidia／gemini／groq／
cohere／hf／mistral＋ollama）的唯一出口：非 2xx 在這裡記一筆（provider／model、
方法、URL、狀態、回應前 500 字）再 raise。沒這筆，下次同型 400 等於沒有來源可對。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

import httpx
import pytest

from app import rag as RAG


def _resp(status: int, body: str, **kw) -> httpx.Response:
    req = httpx.Request("POST", "https://router.example.com/v1/chat/completions", **kw)
    return httpx.Response(status, text=body, request=req)


def _logged(caplog) -> str:
    return "\n".join(rec.getMessage() for rec in caplog.records)


def test_rstatus_logs_source_on_400(caplog):
    r = _resp(400, '{"error":{"message":"The request contains invalid parameters."}}')
    with caplog.at_level(logging.WARNING, logger="ragdemo"):
        with pytest.raises(httpx.HTTPStatusError):
            RAG._rstatus(r, "hf/DeepSeek-V4.1-Flash")
    text = _logged(caplog)
    assert "hf/DeepSeek-V4.1-Flash" in text
    assert "400" in text
    assert "router.example.com" in text
    assert "invalid parameters" in text


def test_rstatus_success_stays_quiet(caplog):
    r = _resp(200, '{"ok":true}')
    with caplog.at_level(logging.WARNING, logger="ragdemo"):
        RAG._rstatus(r, "hf/DeepSeek-V4.1-Flash")
    assert "hf/DeepSeek-V4.1-Flash" not in _logged(caplog)


def test_rstatus_never_logs_headers(caplog):
    # 憑證全走 header（Authorization／api-key／CF token）：日誌裡出現一次就是外洩。
    r = _resp(400, "bad", headers={"Authorization": "Bearer SECRET-TOKEN"})
    with caplog.at_level(logging.WARNING, logger="ragdemo"):
        with pytest.raises(httpx.HTTPStatusError):
            RAG._rstatus(r, "hf/x")
    assert "SECRET-TOKEN" not in _logged(caplog)


def test_rstatus_marks_429_and_still_logs(caplog):
    r = _resp(429, "slow down")
    with caplog.at_level(logging.WARNING, logger="ragdemo"):
        with pytest.raises(httpx.HTTPStatusError):
            RAG._rstatus(r, "groq/openai/gpt-oss-120b")
    assert "429" in _logged(caplog)
    assert "groq/openai/gpt-oss-120b" in RAG._LIMITED
    RAG._LIMITED.clear()
