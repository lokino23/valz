import json, threading, urllib.request

class McpClient:
    """Minimal streamable-HTTP MCP client for one idx-mcp endpoint."""
    def __init__(self, url, timeout=60):
        self.url, self.timeout, self._sid, self._lock = url, timeout, None, threading.Lock()
        self._init()

    def _post(self, payload):
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self._sid: h["Mcp-Session-Id"] = self._sid
        req = urllib.request.Request(self.url, json.dumps(payload).encode(), h)
        r = urllib.request.urlopen(req, timeout=self.timeout)
        self._sid = r.headers.get("mcp-session-id") or self._sid
        return r.read().decode("utf-8", "replace")

    def _init(self):
        self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "valz", "version": "1"}}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def call(self, name, args):
        body = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": name, "arguments": args}}
        d = _parse_sse(self._post(body))
        if d is None: raise RuntimeError(f"mcp: no data for {name}")
        if "error" in d:
            raise RuntimeError(f"mcp rpc error: {json.dumps(d['error'])[:200]}")
        res = d.get("result", {})
        if isinstance(res.get("structuredContent"), dict):
            return res["structuredContent"]
        try:
            out = json.loads(res["content"][0]["text"])
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            ctx = json.dumps(d)[:200]
            raise RuntimeError(f"mcp {name}: unparseable content ({exc}): {ctx}") from exc
        if isinstance(out, dict) and "error" in out:
            raise RuntimeError(f"mcp tool error: {out['error'][:200]}")
        return out

def _parse_sse(text):
    data = None
    for ln in text.splitlines():
        if ln.startswith("data:"):
            try: data = json.loads(ln[5:].strip())
            except json.JSONDecodeError: pass
    return data
