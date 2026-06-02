from pathlib import Path
import unittest


class OpsHealthWorkflowTests(unittest.TestCase):
    def test_ops_health_workflow_has_expected_sections(self):
        workflow = Path(".github/workflows/ops_health.yml").read_text(encoding="utf-8")

        self.assertIn("name: Ops Health Summary", workflow)
        self.assertIn("Cache and today summary", workflow)
        self.assertIn("Push registry summary", workflow)
        self.assertIn("Schedule summary", workflow)
        self.assertIn("Telegram webhook summary", workflow)
        self.assertIn("GITHUB_STEP_SUMMARY", workflow)

    def test_ops_health_avoids_synthetic_worker_post(self):
        workflow = Path(".github/workflows/ops_health.yml").read_text(encoding="utf-8")

        self.assertIn("getWebhookInfo", workflow)
        self.assertNotIn("synthetic-command", workflow)
        self.assertNotIn("synthetic-facts-callback", workflow)
        self.assertNotIn("urllib.request.Request(\n              webhook_url", workflow)


if __name__ == "__main__":
    unittest.main()
