import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp_client import McpClient
cli = McpClient(os.environ.get("IDX_MCP_URL", "http://localhost:8001/mcp"))
p = cli.call("idx_fundamentals", {"code": "BBCA", "year": 2021, "periode": "audit"})
os.makedirs("tests/fixtures", exist_ok=True)
with open("tests/fixtures/bbca_2021_audit.json", "w", encoding="utf-8") as f:
    json.dump(p, f, ensure_ascii=False, indent=1)
print("keys:", sorted(p.keys()))
print(json.dumps(p.get("summary", {}), ensure_ascii=False)[:600])
print(json.dumps(p.get("recomputed", {}), ensure_ascii=False)[:600])
