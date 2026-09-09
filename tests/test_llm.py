"""llm.py 测试：本地假 HTTP 端点（127.0.0.1 随机端口），无真实网络。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from writersroom import llm
from writersroom.core import WritersRoomError


class _Handler(BaseHTTPRequestHandler):
    status = 200
    response = {"choices": [{"message": {"content": "pong"}}]}

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps(self.response).encode("utf-8")
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # 静默
        pass


@pytest.fixture
def fake_server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def cfg_for(url, model="fake-model"):
    return {"base_url": url.rstrip("/") + "/v1", "api_key": "not-required", "model": model}


def test_resolve_config_disabled(monkeypatch):
    for k in ("WRITERSROOM_LLM_BASE_URL", "WRITERSROOM_LLM_API_KEY",
              "WRITERSROOM_LLM_MODEL", "OPENAI_BASE_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert llm.resolve_config() is None


def test_resolve_config_priority(monkeypatch):
    monkeypatch.setenv("WRITERSROOM_LLM_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://ignored:1/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    cfg = llm.resolve_config(model="m1")
    assert cfg["base_url"] == "http://localhost:11434/v1"  # 自动补 /v1
    assert cfg["api_key"] == "sk-x" and cfg["model"] == "m1"
    cfg2 = llm.resolve_config(base_url="http://cli:9/v1/")  # CLI 优先 + 去尾斜杠不重复补
    assert cfg2["base_url"] == "http://cli:9/v1"


def test_check_ok(fake_server, capsys):
    out = llm.check(cfg_for(fake_server))
    assert out["ok"] and out["endpoint"].endswith("/v1") and out["latency_ms"] >= 0


def test_check_http_error(fake_server):
    _Handler.status = 404
    try:
        with pytest.raises(WritersRoomError) as ei:
            llm.check(cfg_for(fake_server))
        assert "HTTP 404" in ei.value.message
        assert "WRITERSROOM_LLM_BASE_URL" in (ei.value.hint or "")
    finally:
        _Handler.status = 200


def test_check_connection_refused():
    cfg = cfg_for("http://127.0.0.1:1")  # 端口 1 必拒绝
    with pytest.raises(WritersRoomError) as ei:
        llm.check(cfg)
    assert "LLM 端点不可用" in ei.value.message and ei.value.hint


def test_check_requires_model(fake_server):
    with pytest.raises(WritersRoomError, match="缺少模型名"):
        llm.check(cfg_for(fake_server, model=None))


def test_chat_returns_content_and_logs(fake_server, capsys):
    content = llm.chat(cfg_for(fake_server), "sys", "user")
    assert content == "pong"
    err = capsys.readouterr().err
    assert "[llm]" in err and "字节" in err


def test_chat_unconfigured():
    with pytest.raises(WritersRoomError, match="LLM 未配置"):
        llm.chat(None, "s", "u")
