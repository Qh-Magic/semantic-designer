import json, tempfile, unittest
from pathlib import Path
import server

class DesignerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.model = self.tmp / "model.yaml"
        self.original = server.MODEL; server.MODEL = self.model
        server.write_model(json.loads((Path(__file__).parent / "model.yaml").read_text()), self.model)
    def tearDown(self): server.MODEL = self.original
    def test_relationship_and_metric_are_valid(self):
        result = server.validate(server.read_model())
        self.assertTrue(result["valid"]); self.assertEqual(result["errors"], [])
    def test_missing_relationship_column_blocks_publish_input(self):
        model = server.read_model(); model["relationships"][0]["to"] = "customers.nope"
        result = server.validate(model)
        self.assertFalse(result["valid"]); self.assertIn("relationship to column not found", result["errors"][0])
    def test_cube_export_has_join_and_measure(self):
        exported = server.cube_yaml(server.read_model()); cubes = {x["name"]: x for x in exported["cubes"]}
        self.assertEqual(cubes["orders"]["measures"][0]["name"], "net_sales")
        self.assertEqual(cubes["orders"]["joins"][0]["relationship"], "many_to_one")
    def test_duplicate_metric_is_rejected(self):
        model = server.read_model(); model["metrics"].append(dict(model["metrics"][0]))
        self.assertFalse(server.validate(model)["valid"])

    def test_review_approve_publish_lifecycle(self):
        model = server.read_model()
        reviewed, error = server.transition(model, "review", {"reviewers": ["data-owner"]})
        self.assertIsNone(error); self.assertEqual(reviewed["status"], "in_review")
        approved, error = server.transition(reviewed, "approve")
        self.assertIsNone(error); self.assertEqual(approved["status"], "approved")
        published, error = server.transition(approved, "publish")
        self.assertIsNone(error); self.assertEqual(published["status"], "published")

    def test_publish_requires_approval(self):
        model = server.read_model(); model["status"] = "draft"
        _, error = server.transition(model, "publish")
        self.assertEqual(error["error"], "model must be valid and approved before publish")

    def test_query_compiler_generates_parameterized_trino_sql(self):
        request = {
            "model": {"name": "sales", "release": "v1"},
            "select": [
                {"kind": "dimension", "name": "orders.order_date", "grain": "month"},
                {"kind": "measure", "name": "net_sales"},
            ],
            "where": {
                "kind": "comparison",
                "field": {"kind": "attribute", "name": "customers.region"},
                "operator": "eq",
                "value": "TW",
            },
            "time": {
                "dimension": "orders.order_date",
                "grain": "month",
                "from": "2026-01-01",
                "to": "2026-10-01",
                "bounds": "inclusive_exclusive",
                "timezone": "Asia/Taipei",
            },
            "limit": 100,
            "principal": {"subject": "user-1", "groups": [], "authn": "internal"},
        }
        compiled = server.compile_query(request, server.read_model())
        self.assertIn("date_trunc('month', o.order_date)", compiled["sql"])
        self.assertIn("SUM(o.net_amount)", compiled["sql"])
        self.assertIn(":p0", compiled["sql"])
        self.assertEqual(compiled["parameters"]["p0"], "TW")
        self.assertNotIn("TW", compiled["sql"])
        self.assertEqual(compiled["metadata"]["release"], "v1")
        self.assertTrue(compiled["metadata"]["query_ast_hash"].startswith("sha256:"))
        self.assertTrue(compiled["metadata"]["semantic_ir_hash"].startswith("sha256:"))
        self.assertIn("orders.net_amount", compiled["metadata"]["lineage"])
        self.assertIn("orders.order_date", compiled["metadata"]["lineage"])

    def test_query_compiler_applies_group_row_security_and_policy_hash(self):
        model = server.read_model()
        model["row_security"] = [{"field": "customers.region", "groups": ["sales-tw"], "value": "TW"}]
        request = {
            "model": {"name": "sales", "release": "v1"},
            "select": [{"kind": "measure", "name": "net_sales"}],
            "where": {
                "kind": "comparison",
                "field": {"kind": "attribute", "name": "customers.region"},
                "operator": "eq",
                "value": "TW",
            },
            "limit": 100,
            "principal": {"subject": "user-1", "groups": ["sales-tw"], "authn": "internal"},
        }
        compiled = server.compile_query(request, model)
        self.assertIn("c.region = :p1", compiled["sql"])
        self.assertTrue(compiled["metadata"]["policy_hash"].startswith("sha256:"))

    def test_query_compiler_rejects_release_not_in_model(self):
        request = {
            "model": {"name": "sales", "release": "v999"},
            "select": [{"kind": "measure", "name": "net_sales"}],
            "limit": 100,
            "principal": {"subject": "user-1", "groups": [], "authn": "internal"},
        }
        with self.assertRaises(server.QueryError) as caught:
            server.compile_query(request, server.read_model())
        self.assertEqual(caught.exception.code, "RELEASE_NOT_FOUND")

    def test_query_compiler_emits_cube_query_from_same_ast(self):
        request = {
            "model": {"name": "sales", "release": "v1"},
            "select": [
                {"kind": "dimension", "name": "orders.order_date", "grain": "month"},
                {"kind": "measure", "name": "net_sales"},
            ],
            "where": {
                "kind": "comparison",
                "field": {"kind": "attribute", "name": "customers.region"},
                "operator": "eq",
                "value": "TW",
            },
            "limit": 100,
            "options": {"target": "cube"},
            "principal": {"subject": "user-1", "groups": [], "authn": "internal"},
        }
        compiled = server.compile_query(request, server.read_model())
        self.assertEqual(compiled["target"], "cube")
        self.assertEqual(compiled["query"]["measures"], ["net_sales"])
        self.assertEqual(compiled["query"]["dimensions"], ["orders.order_date.month"])
        self.assertEqual(compiled["query"]["filters"][0]["member"], "customers.region")
        self.assertEqual(compiled["query"]["filters"][0]["values"], ["TW"])

    def test_query_compiler_rejects_unknown_measure(self):
        request = {
            "model": {"name": "sales", "release": "v1"},
            "select": [{"kind": "measure", "name": "secret_metric"}],
            "limit": 100,
            "principal": {"subject": "user-1", "groups": [], "authn": "internal"},
        }
        with self.assertRaises(server.QueryError) as caught:
            server.compile_query(request, server.read_model())
        self.assertEqual(caught.exception.code, "UNKNOWN_SEMANTIC_OBJECT")

    def test_query_compiler_rejects_unbounded_query(self):
        request = {
            "model": {"name": "sales", "release": "v1"},
            "select": [{"kind": "measure", "name": "net_sales"}],
            "principal": {"subject": "user-1", "groups": [], "authn": "internal"},
        }
        with self.assertRaises(server.QueryError) as caught:
            server.compile_query(request, server.read_model())
        self.assertEqual(caught.exception.code, "INVALID_QUERY_SHAPE")

    def test_sml_parser_loads_model_dataset_and_metric_files(self):
        root = self.tmp / "sml"
        (root / "sales").mkdir(parents=True)
        (root / "sales" / "orders.dataset.yaml").write_text(
            "unique_name: orders\nobject_type: dataset\ntable: mart.fct_orders\ngrain: order_id\ncolumns:\n  - order_id\n  - order_date\n  - net_amount\n"
        )
        (root / "sales" / "net-sales.metric.yaml").write_text(
            "unique_name: net_sales\nobject_type: metric\nlabel: Net Sales\ncalculation_method: sum\ndataset: orders\ncolumn: net_amount\n"
        )
        (root / "sales" / "sales.model.yaml").write_text(
            "unique_name: sales\nobject_type: model\nlabel: Sales\ndatasets:\n  - orders\nmetrics:\n  - net_sales\n"
        )
        parsed = server.load_sml_repository(root)
        self.assertEqual(parsed["views"][0]["id"], "sales")
        self.assertEqual(parsed["models"][0]["table"], "mart.fct_orders")
        self.assertEqual(parsed["metrics"][0]["expression"], "SUM(net_amount)")

    def test_sml_parser_rejects_unknown_object_type(self):
        root = self.tmp / "sml"
        root.mkdir(parents=True)
        (root / "bad.yaml").write_text("unique_name: bad\nobject_type: unknown\n")
        with self.assertRaises(server.SmlParseError) as caught:
            server.load_sml_repository(root)
        self.assertIn("unsupported object_type", str(caught.exception))

if __name__ == "__main__": unittest.main()
