"""Dependency-free local semantic model designer.

The JSON syntax used in model.yaml is valid YAML 1.2. Keeping the file JSON-shaped
lets the offline MVP avoid a third-party parser while remaining importable by YAML
tooling (Cube adapters can consume the generated YAML-shaped document).
"""
from __future__ import annotations

import copy, json, os, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent
# JSON is a YAML 1.2 subset; the file is intentionally JSON-shaped so this MVP
# stays dependency-free while remaining consumable by YAML tooling.
MODEL = ROOT / "model.yaml"
PUBLISHED = ROOT / "published"

def read_model(path=None):
    path = path or MODEL
    return json.loads(Path(path).read_text())

def write_model(model, path=None):
    path = path or MODEL
    Path(path).write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n")

def validate(model):
    errors, warnings = [], []
    required = ["model_id", "version", "status", "models", "relationships", "metrics", "views"]
    errors += [f"missing required field: {x}" for x in required if x not in model]
    model_ids = {x.get("id") for x in model.get("models", [])}
    model_cols = {f"{m.get('id')}.{c}" for m in model.get("models", []) for c in m.get("columns", [])}
    for r in model.get("relationships", []):
        if not r.get("id") or not r.get("from") or not r.get("to"):
            errors.append("relationship requires id, from and to")
        if r.get("from") not in model_cols: errors.append(f"relationship from column not found: {r.get('from')}")
        if r.get("to") not in model_cols: errors.append(f"relationship to column not found: {r.get('to')}")
        if r.get("type") not in {"one_to_one", "one_to_many", "many_to_one"}:
            errors.append(f"unsupported relationship type: {r.get('type')}")
    metric_ids = set()
    for m in model.get("metrics", []):
        if m.get("id") in metric_ids: errors.append(f"duplicate metric id: {m.get('id')}")
        metric_ids.add(m.get("id"))
        if m.get("model") not in model_ids: errors.append(f"metric model not found: {m.get('model')}")
        for d in m.get("dimensions", []):
            if d not in model_cols: errors.append(f"metric dimension not found: {d}")
    for v in model.get("views", []):
        for mid in v.get("metrics", []):
            if mid not in metric_ids: errors.append(f"view metric not found: {mid}")
        unknown = set(v.get("models", [])) - model_ids
        errors += [f"view model not found: {x}" for x in sorted(unknown)]
    if any(r.get("type") == "many_to_many" for r in model.get("relationships", [])):
        warnings.append("many-to-many relationships require an explicit bridge model")
    if not model.get("relationships") and len(model_ids) > 1:
        warnings.append("multiple models have no declared relationship")
    return {"valid": not errors, "errors": errors, "warnings": warnings}

def transition(model, action, payload=None):
    """Apply a review lifecycle transition without performing I/O."""
    payload = payload or {}
    result = validate(model)
    if action == "review":
        if not result["valid"]: return None, result
        updated = copy.deepcopy(model); updated["status"] = "in_review"
        updated.setdefault("audit", {})["reviewers"] = payload.get("reviewers", [])
        return updated, None
    if action == "approve":
        if model.get("status") != "in_review": return None, {"error": "model must be in_review"}
        updated = copy.deepcopy(model); updated["status"] = "approved"; return updated, None
    if action == "publish":
        if not result["valid"] or model.get("status") != "approved":
            return None, {"error": "model must be valid and approved before publish", **result}
        updated = copy.deepcopy(model); updated["status"] = "published"
        updated.setdefault("audit", {})["published_version"] = updated["version"]
        return updated, None
    return None, {"error": f"unsupported transition: {action}"}

def cube_yaml(model):
    """Return a Cube-compatible YAML-shaped JSON document for deterministic export."""
    cubes = []
    for m in model.get("models", []):
        cube = {"name": m["id"], "sql_table": m["table"], "dimensions": [], "measures": []}
        for c in m.get("columns", []):
            if c == m.get("grain"): cube["dimensions"].append({"name": c, "sql": c, "type": "string", "primary_key": True})
            else: cube["dimensions"].append({"name": c, "sql": c, "type": "string"})
        for metric in model.get("metrics", []):
            if metric.get("model") == m["id"]: cube["measures"].append({"name": metric["id"], "sql": metric.get("expression", ""), "type": metric.get("aggregation", "sum")})
        cube["joins"] = []
        for r in model.get("relationships", []):
            left = r["from"].split(".")[0]; right = r["to"].split(".")[0]
            if left == m["id"]: cube["joins"].append({"name": right, "relationship": r["type"], "sql": f"${{CUBE}}.{r['from'].split('.')[-1]} = ${{{right}}}.{r['to'].split('.')[-1]}"})
        cubes.append(cube)
    return {"cubes": cubes, "views": copy.deepcopy(model.get("views", [])), "source_model_version": model.get("version")}

def git_status():
    try:
        return subprocess.run(["git", "status", "--short", str(ROOT)], capture_output=True, text=True).stdout.splitlines()
    except OSError: return []

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, content_type="application/json"):
        data = payload if isinstance(payload, bytes) else (json.dumps(payload, ensure_ascii=False).encode() if content_type == "application/json" else payload.encode())
        self.send_response(code); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/api/model": return self._send(200, read_model())
        if route == "/api/validation": return self._send(200, validate(read_model()))
        if route == "/api/git": return self._send(200, {"files": git_status()})
        if route == "/api/cube": return self._send(200, cube_yaml(read_model()))
        if route == "/": return self._send(200, (ROOT / "static/index.html").read_text(), "text/html; charset=utf-8")
        return self._send(404, {"error": "not found"})
    def do_PUT(self):
        if urlparse(self.path).path != "/api/model": return self._send(404, {"error": "not found"})
        model = self._body(); result = validate(model)
        if not result["valid"]: return self._send(422, result)
        if model.get("status") in {"approved", "published"}:
            model["status"] = "draft"
        write_model(model); return self._send(200, model)
    def do_POST(self):
        route = urlparse(self.path).path; model = read_model()
        if route == "/api/review":
            payload = self._body() if self.headers.get("Content-Length") else {}
            updated, error = transition(model, "review", payload)
            if error: return self._send(422, error)
            write_model(updated); return self._send(200, updated)
        if route == "/api/publish":
            updated, error = transition(model, "publish")
            if error: return self._send(409, error)
            PUBLISHED.mkdir(exist_ok=True)
            if (PUBLISHED / f"v{updated['version']}.json").exists():
                return self._send(409, {"error": "published version already exists; increment model.version"})
            write_model(updated); (PUBLISHED / f"v{updated['version']}.json").write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n"); (PUBLISHED / f"v{updated['version']}.cube.yaml").write_text(json.dumps(cube_yaml(updated), ensure_ascii=False, indent=2) + "\n")
            return self._send(200, updated)
        if route == "/api/approve":
            updated, error = transition(model, "approve")
            if error: return self._send(409, error)
            write_model(updated); return self._send(200, updated)
        if route == "/api/rollback":
            requested = self._body().get("version"); source = PUBLISHED / f"v{requested}.json"
            if not source.exists(): return self._send(404, {"error": "published version not found"})
            restored = json.loads(source.read_text()); restored["status"] = "approved"; write_model(restored); return self._send(200, restored)
        return self._send(404, {"error": "not found"})

def main():
    port = int(os.environ.get("SEMANTIC_DESIGNER_PORT", "8787")); print(f"semantic designer: http://127.0.0.1:{port}"); ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
if __name__ == "__main__": main()
