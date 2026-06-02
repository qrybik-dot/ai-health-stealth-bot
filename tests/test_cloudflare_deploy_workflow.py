from pathlib import Path
import unittest


class CloudflareDeployWorkflowTests(unittest.TestCase):
    def test_cloudflare_deploy_workflow_has_dry_run_and_deploy_guard(self):
        workflow = Path(".github/workflows/cloudflare_worker_deploy.yml").read_text(encoding="utf-8")

        self.assertIn("name: Cloudflare Worker Deploy", workflow)
        self.assertIn("npx wrangler@4.92.0 deploy --dry-run", workflow)
        self.assertIn("npx wrangler@4.92.0 deploy", workflow)
        self.assertIn("deploy blocked: Cloudflare secrets missing", workflow)
        self.assertIn("CLOUDFLARE_API_TOKEN", workflow)
        self.assertNotIn("CLOUDFLARE_ACCOUNT_ID", workflow)

    def test_wrangler_config_pins_account_id(self):
        config = Path("cloudflare/wrangler.jsonc").read_text(encoding="utf-8")

        self.assertIn('"account_id": "0c6d1b47eb7fa09887d0ecb17ccaf7e1"', config)


if __name__ == "__main__":
    unittest.main()
