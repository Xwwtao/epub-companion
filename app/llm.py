"""LLM 调用封装：所有请求都经由 complete_json，统一处理 JSON 解析、重试和开发模式日志。"""

import json
import logging
from datetime import datetime, timezone
from typing import Protocol

from openai import OpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)

# 摘要要求忠于原文，温度取低一些
PARAMS = {"temperature": 0.3}


class LLMError(Exception):
    """模型调用失败或返回内容不可用；消息直接展示给用户。"""


class LLMClient(Protocol):
    model: str

    def chat(self, messages: list[dict], **params) -> str: ...


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def chat(self, messages: list[dict], **params) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            response_format={"type": "json_object"},
            **params,
        )
        return response.choices[0].message.content or ""


def complete_json(client: LLMClient, kind: str, messages: list[dict], meta: dict, validate) -> dict:
    """发送请求并解析 JSON；内容不合格时重试一次。validate(data) 不合格时抛 ValueError。"""
    last_error: Exception | None = None
    for _attempt in range(2):
        _log_request(client, kind, messages, meta)
        try:
            content = client.chat(messages, **PARAMS)
        except Exception as e:
            raise LLMError(f"调用模型失败：{e}") from e
        try:
            data = json.loads(content)
            validate(data)
            return data
        except ValueError as e:
            last_error = e
            logger.warning("模型返回内容不合格，%s：%s", "重试" if _attempt == 0 else "放弃", e)
    raise LLMError(f"模型返回的内容无法解析：{last_error}")


def _log_request(client: LLMClient, kind: str, messages: list[dict], meta: dict) -> None:
    settings = get_settings()
    if not settings.is_development:
        return
    # 只记录请求内容，不记录 key 和请求头
    record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        **meta,
        "model": client.model,
        "params": PARAMS,
        "messages": messages,
    }
    log_dir = settings.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "llm_requests.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
