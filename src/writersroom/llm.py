"""可选 LLM 适配（docs/design/spec.md §4.9）。

- 默认关闭：未配置时 resolve_config 返回 None，一切确定性功能不受影响。
- 协议：OpenAI 兼容 POST {base_url}/chat/completions（调研域 F：Ollama :11434 /
  llama.cpp :8080 / LM Studio :1234 均兼容；本地端点默认免鉴权，api_key 用占位值）。
- 只用标准库 urllib，零新增依赖；全部网络调用显式走 cfg，无隐式默认端点。
- 任何失败都转为 WritersRoomError（含端点、原因、可操作 hint），禁止伪造成功。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

from .core import WritersRoomError

TIMEOUT_S = 30

CONFIG_HINT = ("设置 WRITERSROOM_LLM_BASE_URL（如 http://localhost:11434/v1）与 "
               "WRITERSROOM_LLM_MODEL 后重试；或放弃 --use-llm，使用纯确定性模式")


def resolve_config(base_url: str | None = None, api_key: str | None = None,
                   model: str | None = None) -> dict | None:
    """配置解析：CLI 参数 > WRITERSROOM_LLM_* > OPENAI_*；全空 → None（未启用）。

    base_url 缺省补 /v1 后缀；api_key 缺省填占位（本地端点「required but ignored」）。
    """
    base_url = base_url or os.environ.get("WRITERSROOM_LLM_BASE_URL") \
        or os.environ.get("OPENAI_BASE_URL")
    api_key = api_key or os.environ.get("WRITERSROOM_LLM_API_KEY") \
        or os.environ.get("OPENAI_API_KEY")
    model = model or os.environ.get("WRITERSROOM_LLM_MODEL")
    if not base_url:
        return None
    base_url = base_url.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    return {"base_url": base_url, "api_key": api_key or "not-required", "model": model}


def _post(cfg: dict, path: str, payload: dict) -> tuple[dict, int]:
    """POST JSON，返回 (响应体, 发送字节数)；一切失败 → WritersRoomError。"""
    url = cfg["base_url"] + path
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg['api_key']}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data, len(body)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:200]
        raise WritersRoomError(
            f"LLM 端点返回 HTTP {e.code}：{cfg['base_url']}（{detail}）", CONFIG_HINT,
        ) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise WritersRoomError(
            f"LLM 端点不可用：{cfg['base_url']}（{reason}）", CONFIG_HINT,
        ) from e
    except json.JSONDecodeError as e:
        raise WritersRoomError(
            f"LLM 端点返回非 JSON：{cfg['base_url']}", CONFIG_HINT,
        ) from e


def check(cfg: dict) -> dict:
    """连通性检查：最小 chat 请求。成功返回 {ok, endpoint, model, latency_ms, bytes_sent}。"""
    if not cfg:
        raise WritersRoomError("LLM 未配置", CONFIG_HINT)
    if not cfg.get("model"):
        raise WritersRoomError(
            f"已配置端点 {cfg['base_url']} 但缺少模型名",
            "设置 WRITERSROOM_LLM_MODEL（如 qwen3:8b 等本地已拉取的模型名）",
        )
    started = time.monotonic()
    data, sent = _post(cfg, "/chat/completions", {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    })
    latency = int((time.monotonic() - started) * 1000)
    if not isinstance(data, dict) or "choices" not in data:
        raise WritersRoomError(
            f"LLM 端点响应缺少 choices 字段：{cfg['base_url']}", CONFIG_HINT)
    return {"ok": True, "endpoint": cfg["base_url"], "model": cfg["model"],
            "latency_ms": latency, "bytes_sent": sent}


def chat(cfg: dict, system_prompt: str, user_prompt: str, max_tokens: int = 2048) -> str:
    """发送一轮对话，返回 content。发送前在 stderr 记录端点与字节数（隐私可见性）。"""
    if not cfg:
        raise WritersRoomError("LLM 未配置", CONFIG_HINT)
    if not cfg.get("model"):
        raise WritersRoomError("缺少模型名",
                               "设置 WRITERSROOM_LLM_MODEL 后重试")
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
    }
    data, sent = _post(cfg, "/chat/completions", payload)
    print(f"[llm] POST {cfg['base_url']}/chat/completions 发送 {sent} 字节", file=sys.stderr)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise WritersRoomError(
            f"LLM 响应结构异常：{cfg['base_url']}", CONFIG_HINT) from e
