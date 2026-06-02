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
        self.assertIn("CLOUDFLARE_ACCOUNT_ID", workflow)


if __name__ == "__main__":
    unittest.main()
