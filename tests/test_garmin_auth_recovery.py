import os
import tempfile
import unittest
from unittest.mock import patch

import main
import scripts.gist_upload as gist_upload


class FakeGarth:
    def __init__(self):
        self.loaded = []

    def loads(self, value):
        self.loaded.append(value)

    def dumps(self):
        return "new-tokenstore"


class FakeGarmin:
    def __init__(self, login_error=None):
        self.garth = FakeGarth()
        self.login_calls = []
        self.login_error = login_error

    def login(self, tokenstore=None):
        self.login_calls.append(tokenstore)
        if self.login_error:
            raise self.login_error
        return True


class GarminAuthRecoveryTests(unittest.TestCase):
    def test_tokenstore_is_preferred_before_password_login(self):
        api = FakeGarmin()
        with patch.object(main, "get_garmin_auth_state", return_value={"tokenstore": "cached-token"}), \
             patch.object(main, "_complete_garmin_session_from_tokens"), \
             patch.object(main, "upsert_garmin_auth_state") as persist:
            result = main._authenticate_garmin(api)

        self.assertEqual(result["method"], "token")
        self.assertEqual(api.garth.loaded, ["cached-token"])
        self.assertEqual(api.login_calls, [])
        persist.assert_called_once_with({"tokenstore": "new-tokenstore"})

    def test_password_fallback_is_visible_when_tokens_missing(self):
        api = FakeGarmin()
        with patch.object(main, "get_garmin_auth_state", return_value={}), \
             patch.object(main, "upsert_garmin_auth_state"), \
             self.assertLogs(main.log.name, level="WARNING") as logs:
            result = main._authenticate_garmin(api)

        self.assertEqual(result["method"], "password")
        self.assertEqual(api.login_calls, [None])
        self.assertIn("garmin_auth_password_fallback_used", "\n".join(logs.output))

    def test_password_fallback_can_be_blocked(self):
        api = FakeGarmin()
        with patch.object(main, "get_garmin_auth_state", return_value={}), \
             patch.dict(os.environ, {"GARMIN_PASSWORD_FALLBACK": "0"}, clear=False):
            with self.assertRaises(main.GarminAuthError):
                main._authenticate_garmin(api)

        self.assertEqual(api.login_calls, [])

    def test_garmin_429_becomes_actionable_rate_limit_error(self):
        api = FakeGarmin(login_error=RuntimeError("429 Too Many Requests"))
        with patch.object(main, "get_garmin_auth_state", return_value={}):
            with self.assertRaises(main.GarminRateLimitError) as caught:
                main._authenticate_garmin(api)

        self.assertIn("429", str(caught.exception))

    def test_ci_sync_requires_tokenstore_before_auth(self):
        with patch.dict(os.environ, {"CI": "true"}, clear=False), \
             patch.object(main, "get_garmin_auth_state", return_value={}):
            with self.assertRaises(main.GarminAuthError) as caught:
                main._guard_ci_tokenstore_requirement()
        self.assertIn("CI tokenstore guard", str(caught.exception))

    def test_recovery_gist_upload_requires_success_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with open("cache.json", "w", encoding="utf-8") as f:
                    f.write("{}")
                with patch.dict(
                    os.environ,
                    {
                        "CACHE_GIST_ID": "gist-id",
                        "GIST_TOKEN": "token",
                        "REQUIRE_RECOVERY_UPLOAD_OK": "1",
                    },
                    clear=False,
                ), patch.object(gist_upload.requests, "patch") as patch_request:
                    with self.assertRaises(RuntimeError):
                        gist_upload.main()
                    patch_request.assert_not_called()
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
