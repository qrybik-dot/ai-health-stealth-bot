import unittest
from unittest import mock

import requests

from scripts import gist_upload


class _Response:
    def __init__(self, status_code: int, text: str = "", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class GistUploadRetryTests(unittest.TestCase):
    def test_retries_transient_http_failures_then_succeeds(self):
        responses = [_Response(502), _Response(503), _Response(200)]

        with mock.patch.object(gist_upload.requests, "patch", side_effect=responses) as patch_call, mock.patch.object(
            gist_upload.time, "sleep"
        ) as sleep_call:
            result = gist_upload._patch_gist("gist-id", "token", {"files": {}})

        self.assertEqual(result.status_code, 200)
        self.assertEqual(patch_call.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep_call.call_args_list], [2, 4])

    def test_does_not_retry_non_transient_http_failure(self):
        with mock.patch.object(gist_upload.requests, "patch", return_value=_Response(401)) as patch_call, mock.patch.object(
            gist_upload.time, "sleep"
        ) as sleep_call:
            result = gist_upload._patch_gist("gist-id", "token", {"files": {}})

        self.assertEqual(result.status_code, 401)
        self.assertEqual(patch_call.call_count, 1)
        sleep_call.assert_not_called()

    def test_retries_network_error_then_succeeds(self):
        responses = [requests.Timeout("timeout"), _Response(200)]

        with mock.patch.object(gist_upload.requests, "patch", side_effect=responses) as patch_call, mock.patch.object(
            gist_upload.time, "sleep"
        ) as sleep_call:
            result = gist_upload._patch_gist("gist-id", "token", {"files": {}})

        self.assertEqual(result.status_code, 200)
        self.assertEqual(patch_call.call_count, 2)
        self.assertEqual([call.args[0] for call in sleep_call.call_args_list], [2])

    def test_respects_retry_after_seconds(self):
        responses = [_Response(429, headers={"Retry-After": "7"}), _Response(200)]

        with mock.patch.object(gist_upload.requests, "patch", side_effect=responses), mock.patch.object(
            gist_upload.time, "sleep"
        ) as sleep_call:
            result = gist_upload._patch_gist("gist-id", "token", {"files": {}})

        self.assertEqual(result.status_code, 200)
        sleep_call.assert_called_once_with(7)


if __name__ == "__main__":
    unittest.main()
