import json

import pytest

from mcp_client import McpClient, _parse_sse

def test_parse_sse_extracts_last_data():
    raw = 'event: message\ndata: {"id":1,"result":{"a":1}}\n\n'
    assert _parse_sse(raw)["result"]["a"] == 1

def test_parse_sse_none_on_empty():
    assert _parse_sse("retry: 1000\n\n") is None

def test_call_raises_runtimeerror_on_rpc_error(monkeypatch):
    c = McpClient.__new__(McpClient)  # skip network handshake
    sse = 'data: {"jsonrpc":"2.0","id":2,"error":{"code":-32000,"message":"boom"}}\n\n'
    monkeypatch.setattr(c, "_post", lambda payload: sse)
    with pytest.raises(RuntimeError, match="mcp rpc error"):
        c.call("some_tool", {})

def test_call_raises_runtimeerror_on_unparseable_content(monkeypatch):
    c = McpClient.__new__(McpClient)  # skip network handshake
    body = {"jsonrpc": "2.0", "id": 2,
            "result": {"content": [{"type": "text", "text": "<html>oops"}]}}
    monkeypatch.setattr(c, "_post", lambda payload: "data: " + json.dumps(body))
    with pytest.raises(RuntimeError, match="unparseable"):
        c.call("some_tool", {})
