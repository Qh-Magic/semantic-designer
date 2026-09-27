"""Dependency-free local semantic model designer.

The JSON syntax used in model.yaml is valid YAML 1.2. Keeping the file JSON-shaped
lets the offline MVP avoid a third-party parser while remaining importable by YAML
tooling (Cube adapters can consume the generated YAML-shaped document).
"""
from __future__ import annotations

import copy, hashlib, json, os, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from dataclasses import dataclass
import ast

ROOT = Path(__file__).parent
# JSON is a YAML 1.2 subset; the file is intentionally JSON-shaped so this MVP
# stays dependency-free while remaining consumable by YAML tooling.
MODEL = ROOT / "model.yaml"
PUBLISHED = ROOT / "published"


@dataclass
class QueryError(Exception):
    code: str
    message: str

    def __str__(self):
        return f"{self.code}: {self.message}"


class SmlParseError(ValueError):
    pass


QUERY_OPERATORS = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "in": "IN"}
MAX_QUERY_LIMIT = 5000

def read_model(path=None):
    path = path or MODEL
    return json.loads(Path(path).read_text())

def write_model(model, path=None):
    path = path or MODEL
    Path(path).write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n")


def _sml_scalar(value):
    value = value.strip()
    if not value:
        return None
    if value in {"true", "True"}: return True
    if value in {"false", "False"}: return False
    if value in {"null", "Null", "~"}: return None
    if value.startswith("[") and value.endswith("]"):
        try: return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return [_sml_scalar(x) for x in value[1:-1].split(",") if x.strip()]
    if (value.startswith("\"") and value.endswith("\"")) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value


def _sml_yaml(text):
    """Parse the dependency-free YAML subset used by SML object files.

    The parser intentionally supports mappings, scalar lists, and list-of-maps,
    which covers the SML repository's object references without introducing a
    runtime dependency. Unsupported YAML is rejected loudly instead of guessed.
    """
    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if "\t" in raw[:indent]: raise SmlParseError(f"tabs are not supported at line {number}")
        if indent % 2: raise SmlParseError(f"indent must use two spaces at line {number}")
        lines.append((indent, raw.strip(), number))
    if not lines: return {}

    def parse_block(position, indent):
        if position >= len(lines) or lines[position][0] != indent:
            return {}, position
        is_list = lines[position][1].startswith("-")
        result = [] if is_list else {}
        while position < len(lines) and lines[position][0] == indent:
            content, number = lines[position][1], lines[position][2]
            if is_list:
                if not content.startswith("-"):
                    raise SmlParseError(f"mixed mapping/list at line {number}")
                item = content[1:].strip(); position += 1
                if not item:
                    value, position = parse_block(position, indent + 2); result.append(value); continue
                if ":" in item:
                    key, raw_value = item.split(":", 1); mapping = {key.strip(): _sml_scalar(raw_value)}
                    if position < len(lines) and lines[position][0] > indent:
                        nested, position = parse_block(position, indent + 2)
                        if not isinstance(nested, dict): raise SmlParseError(f"list item mapping expected at line {number}")
                        mapping.update(nested)
                    result.append(mapping)
                else:
                    result.append(_sml_scalar(item))
            else:
                if content.startswith("-") or ":" not in content:
                    raise SmlParseError(f"mapping entry expected at line {number}")
                key, raw_value = content.split(":", 1); key = key.strip(); position += 1
                if raw_value.strip(): result[key] = _sml_scalar(raw_value)
                elif position < len(lines) and lines[position][0] > indent:
                    result[key], position = parse_block(position, indent + 2)
                else: result[key] = None
        return result, position

    parsed, position = parse_block(0, lines[0][0])
    if position != len(lines): raise SmlParseError(f"unsupported YAML near line {lines[position][2]}")
    return parsed


def parse_sml_text(text, source="<memory>"):
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try: parsed = _sml_yaml(text)
        except (SmlParseError, TypeError, ValueError) as error: raise SmlParseError(f"{source}: {error}") from error
    if not isinstance(parsed, dict) or not parsed.get("object_type") or not parsed.get("unique_name"):
        raise SmlParseError(f"{source}: SML object requires unique_name and object_type")
    return parsed


def load_sml_repository(path):
    """Load SML object files into the designer's normalized model contract."""
    root = Path(path); objects = []
    for source in sorted(root.rglob("*.yaml")) + sorted(root.rglob("*.yml")):
        if any(part.startswith(".") for part in source.relative_to(root).parts): continue
        objects.append(parse_sml_text(source.read_text(), str(source)))
    allowed = {"catalog", "package", "connection", "dataset", "dimension", "metric", "calculation", "row_security", "model", "composite_model"}
    unknown = [item.get("object_type") for item in objects if item.get("object_type") not in allowed]
    if unknown: raise SmlParseError(f"unsupported object_type: {unknown[0]}")
    datasets = {item["unique_name"]: item for item in objects if item["object_type"] == "dataset"}
    metrics = [item for item in objects if item["object_type"] == "metric"]
    models = [item for item in objects if item["object_type"] in {"model", "composite_model"}]
    normalized_models = []
    for dataset_name, dataset in datasets.items():
        normalized_models.append({"id": dataset_name, "table": dataset.get("table", dataset.get("unique_name")), "grain": dataset.get("grain"), "columns": dataset.get("columns", [])})
    normalized_metrics = []
    for metric in metrics:
        dataset = metric.get("dataset")
        method = metric.get("calculation_method")
        column = metric.get("column")
        if not dataset or not method or not column: raise SmlParseError(f"metric {metric['unique_name']} requires dataset, column and calculation_method")
        normalized_metrics.append({"id": metric["unique_name"], "label": metric.get("label", metric["unique_name"]), "model": dataset, "expression": f"{method.upper()}({column})", "aggregation": method, "dimensions": []})
    normalized_views = [{"id": item["unique_name"], "label": item.get("label", item["unique_name"]), "models": item.get("datasets", []), "metrics": item.get("metrics", [])} for item in models]
    return {"model_id": "sml_repository", "version": 1, "status": "approved", "title": root.name, "models": normalized_models, "relationships": [], "metrics": normalized_metrics, "views": normalized_views, "sml_objects": len(objects)}

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


def _query_error(code, message):
    raise QueryError(code, message)


def _sha256(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _filter_fields(node):
    if not isinstance(node, dict):
        return []
    fields = []
    if node.get("kind") in {"comparison", "between", "is_null"}:
        field = node.get("field", {}).get("name") if isinstance(node.get("field"), dict) else None
        if field:
            fields.append(field)
    for child in node.get("children", []):
        fields.extend(_filter_fields(child))
    return fields


def _cube_filters(node):
    if not isinstance(node, dict):
        return []
    if node.get("kind") in {"and", "or"}:
        return [item for child in node.get("children", []) for item in _cube_filters(child)]
    if node.get("kind") != "comparison":
        _query_error("INCOMPATIBLE_SELECTIONS", "cube target currently supports comparison filters only")
    field = node.get("field", {}).get("name")
    operator = node.get("operator")
    value = node.get("value")
    if not field or operator not in {"eq", "neq", "gt", "gte", "lt", "lte", "in"}:
        _query_error("INVALID_FILTER", "unsupported cube filter")
    values = value if isinstance(value, list) else [value]
    cube_operator = {"eq": "equals", "neq": "notEquals", "gt": "gt", "gte": "gte", "lt": "lt", "lte": "lte", "in": "equals"}[operator]
    return [{"member": field, "operator": cube_operator, "values": values}]


def _model_columns(model):
    return {f"{item['id']}.{column}" for item in model.get("models", []) for column in item.get("columns", [])}


def _resolve_model(request, model):
    ref = request.get("model")
    if not isinstance(ref, dict) or not ref.get("name") or not ref.get("release"):
        _query_error("INVALID_QUERY_SHAPE", "model.name and model.release are required")
    if ref["name"] not in {v.get("id") for v in model.get("views", [])}:
        _query_error("MODEL_NOT_FOUND", f"semantic model not found: {ref['name']}")
    expected_releases = {str(model.get("version")), f"v{model.get('version')}"}
    if ref["release"] not in expected_releases:
        _query_error("RELEASE_NOT_FOUND", f"model release not found: {ref['release']}")
    return next(v for v in model["views"] if v["id"] == ref["name"])


def _validate_principal(request):
    principal = request.get("principal")
    if not isinstance(principal, dict) or not principal.get("subject") or principal.get("authn") not in {"oidc", "service_account", "internal"}:
        _query_error("INVALID_QUERY_SHAPE", "principal.subject and supported principal.authn are required")
    return principal


def _resolve_field(field, model_columns):
    if not isinstance(field, dict) or field.get("kind") not in {"dimension", "attribute"} or field.get("name") not in model_columns:
        _query_error("UNKNOWN_SEMANTIC_OBJECT", f"unknown field: {field}")
    source, column = field["name"].split(".", 1)
    alias = "o" if source == "orders" else "c" if source == "customers" else source
    return f"{alias}.{column}"


def _compile_filter(node, model_columns, parameters):
    if not isinstance(node, dict):
        _query_error("INVALID_FILTER", "filter must be an expression object")
    kind = node.get("kind")
    if kind in {"and", "or"}:
        children = node.get("children")
        if not isinstance(children, list) or not children:
            _query_error("INVALID_FILTER", f"{kind} requires children")
        glue = f" {kind.upper()} "
        return "(" + glue.join(_compile_filter(child, model_columns, parameters) for child in children) + ")"
    if kind == "comparison":
        field = _resolve_field(node.get("field"), model_columns)
        operator = node.get("operator")
        if operator not in QUERY_OPERATORS:
            _query_error("INVALID_FILTER", f"unsupported operator: {operator}")
        value = node.get("value")
        if operator == "in" and (not isinstance(value, list) or not value):
            _query_error("INVALID_FILTER", "in requires a non-empty list")
        if operator != "in" and isinstance(value, list):
            _query_error("INVALID_FILTER", "scalar comparison requires a scalar value")
        if operator == "in":
            placeholders = []
            for item in value:
                key = f"p{len(parameters)}"; parameters[key] = item; placeholders.append(f":{key}")
            return f"{field} IN ({', '.join(placeholders)})"
        key = f"p{len(parameters)}"; parameters[key] = value
        return f"{field} {QUERY_OPERATORS[operator]} :{key}"
    if kind == "between":
        field = _resolve_field(node.get("field"), model_columns)
        lower_key = f"p{len(parameters)}"; parameters[lower_key] = node.get("lower")
        upper_key = f"p{len(parameters)}"; parameters[upper_key] = node.get("upper")
        return f"{field} BETWEEN :{lower_key} AND :{upper_key}"
    if kind == "is_null":
        field = _resolve_field(node.get("field"), model_columns)
        return f"{field} IS {'NOT ' if node.get('negated') else ''}NULL"
    _query_error("INVALID_FILTER", f"unsupported filter kind: {kind}")


def compile_query(request, model):
    """Compile the public Query AST into a parameterized Trino query.

    This is intentionally a small vertical slice: it supports the repository's
    sales model, while keeping the seam independent from the eventual SML loader.
    """
    if not isinstance(request, dict) or not isinstance(request.get("select"), list) or not request["select"]:
        _query_error("INVALID_QUERY_SHAPE", "select must be a non-empty list")
    if len(request["select"]) > 30:
        _query_error("INVALID_QUERY_SHAPE", "select exceeds maximum size")
    principal = _validate_principal(request)
    view = _resolve_model(request, model)
    if request.get("limit") is None or not isinstance(request["limit"], int) or not 1 <= request["limit"] <= MAX_QUERY_LIMIT:
        _query_error("INVALID_QUERY_SHAPE", f"limit must be between 1 and {MAX_QUERY_LIMIT}")

    model_columns = _model_columns(model)
    metrics = {item["id"]: item for item in model.get("metrics", []) if item.get("id") in view.get("metrics", [])}
    selections, group_by, joins, lineage = [], [], set(), []
    for item in request["select"]:
        if not isinstance(item, dict) or item.get("kind") not in {"dimension", "measure"} or not item.get("name"):
            _query_error("INVALID_QUERY_SHAPE", "select items require kind and name")
        name = item["name"]
        if item["kind"] == "measure":
            metric = metrics.get(name)
            if not metric: _query_error("UNKNOWN_SEMANTIC_OBJECT", f"unknown measure: {name}")
            expression = metric.get("expression", "")
            if not expression.startswith("SUM(") or not expression.endswith(")"):
                _query_error("INCOMPATIBLE_SELECTIONS", f"unsupported metric expression: {name}")
            column = expression[4:-1].strip()
            source = metric.get("model")
            if f"{source}.{column}" not in model_columns:
                _query_error("UNKNOWN_SEMANTIC_OBJECT", f"metric column not found: {source}.{column}")
            selections.append(f"SUM(o.{column}) AS {name}")
            lineage.append(f"{source}.{column}")
            continue
        if name not in model_columns:
            _query_error("UNKNOWN_SEMANTIC_OBJECT", f"unknown dimension: {name}")
        source, column = name.split(".", 1)
        alias = "o" if source == "orders" else "c" if source == "customers" else source
        grain = item.get("grain")
        expression = f"{alias}.{column}"
        if grain:
            if grain not in {"day", "month", "quarter", "year"}:
                _query_error("INVALID_QUERY_SHAPE", f"unsupported grain: {grain}")
            expression = f"date_trunc('{grain}', {expression})"
        selections.append(f"{expression} AS {column}_{grain or 'value'}")
        lineage.append(name)
        group_by.append(str(len(selections)))
        if source != "orders": joins.add(source)

    if not request.get("time") and not request.get("where"):
        _query_error("INVALID_QUERY_SHAPE", "bounded query requires time or where")

    parameters = {}; predicates = []
    if request.get("where"):
        predicates.append(_compile_filter(request["where"], model_columns, parameters))
        lineage.extend(_filter_fields(request["where"]))
        if "customers." in json.dumps(request["where"]): joins.add("customers")
    time = request.get("time")
    if time:
        if time.get("bounds") != "inclusive_exclusive" or not time.get("from") or not time.get("to"):
            _query_error("INVALID_TIME_WINDOW", "time requires [from,to) inclusive_exclusive bounds")
        field = time.get("dimension")
        if field not in model_columns: _query_error("UNKNOWN_SEMANTIC_OBJECT", f"unknown time dimension: {field}")
        source, column = field.split(".", 1); alias = "o" if source == "orders" else "c" if source == "customers" else source
        from_key = f"p{len(parameters)}"; parameters[from_key] = time["from"]
        to_key = f"p{len(parameters)}"; parameters[to_key] = time["to"]
        predicates.append(f"{alias}.{column} >= :{from_key} AND {alias}.{column} < :{to_key}")
        lineage.append(field)
        if source != "orders": joins.add(source)

    # Model-level security rules can be added without changing the public AST.
    for policy in model.get("row_security", []):
        if policy.get("field") and policy.get("groups"):
            if not set(principal.get("groups", [])) & set(policy["groups"]):
                _query_error("UNAUTHORIZED_FIELD", "principal is not allowed by row security policy")
            key = f"p{len(parameters)}"; parameters[key] = policy.get("value")
            policy_source, policy_column = policy["field"].split(".", 1)
            policy_alias = "o" if policy_source == "orders" else "c" if policy_source == "customers" else policy_source
            predicates.append(f"{policy_alias}.{policy_column} = :{key}")
            lineage.append(policy["field"])

    join_sql = ""
    if "customers" in joins:
        join_sql = " JOIN mart.dim_customers c ON o.customer_id = c.customer_id"
    sql = "SELECT " + ", ".join(selections) + " FROM mart.fct_orders o" + join_sql
    if predicates: sql += " WHERE " + " AND ".join(f"({p})" for p in predicates)
    if group_by: sql += " GROUP BY " + ", ".join(group_by)
    sql += f" LIMIT {request['limit']}"
    semantic_ir = {
        "model": view["id"],
        "release": request["model"]["release"],
        "select": selections,
        "predicates": predicates,
        "group_by": group_by,
        "joins": sorted(joins),
        "lineage": sorted(set(lineage)),
    }
    policy_hash = _sha256({"policies": model.get("row_security", []), "principal_groups": sorted(principal.get("groups", []))})
    metadata = {
        "model": view["id"],
        "release": request["model"]["release"],
        "principal": principal["subject"],
        "query_ast_hash": _sha256(request),
        "semantic_ir_hash": _sha256(semantic_ir),
        "policy_hash": policy_hash,
        "lineage": semantic_ir["lineage"],
    }
    target = request.get("options", {}).get("target", "trino")
    if target == "cube":
        dimensions = [f"{item['name']}.{item['grain']}" if item.get("grain") else item["name"] for item in request["select"] if item.get("kind") == "dimension"]
        measures = [item["name"] for item in request["select"] if item.get("kind") == "measure"]
        cube_query = {"measures": measures, "dimensions": dimensions, "filters": _cube_filters(request.get("where")) if request.get("where") else [], "limit": request["limit"]}
        return {"target": "cube", "query": cube_query, "semantic_ir": semantic_ir, "metadata": metadata}
    if target != "trino":
        _query_error("INVALID_QUERY_SHAPE", f"unsupported query target: {target}")
    return {"target": "trino", "sql": sql, "parameters": parameters, "semantic_ir": semantic_ir, "metadata": metadata}

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
        if route == "/api/query":
            try:
                return self._send(200, compile_query(self._body(), model))
            except QueryError as error:
                return self._send(422, {"code": error.code, "error": error.message})
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
