from pathlib import Path
import unittest


class RecoveryWorkflowTests(unittest.TestCase):
    def test_recovery_workflow_exposes_garmin_audit(self):
        workflow = Path(".github/workflows/recovery.yml").read_text(encoding="utf-8")

        self.assertIn("- garmin-audit", workflow)
        self.assertIn("Garmin audit", workflow)
        self.assertIn("python main.py garmin-audit", workflow)
        self.assertIn('GARMIN_PASSWORD_FALLBACK: "0"', workflow)

    def test_recovery_workflow_exposes_manual_auth_refresh(self):
        workflow = Path(".github/workflows/recovery.yml").read_text(encoding="utf-8")

        self.assertIn("- garmin-auth-refresh", workflow)
        self.assertIn("Garmin auth refresh", workflow)
        self.assertIn("python main.py garmin-auth-refresh", workflow)
        self.assertIn('GARMIN_PASSWORD_FALLBACK: "1"', workflow)
        self.assertIn("inputs.operation == 'garmin-auth-refresh'", workflow)


if __name__ == "__main__":
    unittest.main()
