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

if __name__ == "__main__": unittest.main()
