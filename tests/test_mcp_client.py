from mcp_client import _parse_sse

def test_parse_sse_extracts_last_data():
    raw = 'event: message\ndata: {"id":1,"result":{"a":1}}\n\n'
    assert _parse_sse(raw)["result"]["a"] == 1

def test_parse_sse_none_on_empty():
    assert _parse_sse("retry: 1000\n\n") is None
