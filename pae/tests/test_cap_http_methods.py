"""Test the generic capability methods without opening an engine or a socket."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pae_core import api, capsurface


SUPPORTED_GET = frozenset({
    "pae.health", "pae.status", "pae.config", "pae.persona",
    "pae.decisions", "pae.push.log", "pae.params", "pae.agent.log",
    "pae.audit", "pae.permissions", "pae.agent.schedule",
    "pae.metrics", "pae.page",
})


class CapabilityMethodTests(unittest.TestCase):
    def test_get_keeps_documented_queries_and_legacy_readers(self):
        documented = {
            cap["name"] for cap in capsurface.manifest_doc()["capabilities"]
            if cap.get("http", {}).get("method") == "GET"
        }
        self.assertEqual(SUPPORTED_GET, documented | {"pae.metrics", "pae.page"})
        for name in sorted(SUPPORTED_GET):
            with self.subTest(name=name), patch.object(
                capsurface, "handle", return_value=(200, {"ok": True})
            ) as handle:
                response = api.cap_call_get(name, x_pae_key="")
                self.assertEqual(response.status_code, 200)
                handle.assert_called_once_with(name, {}, "")

    def test_other_registered_capabilities_require_post_before_dispatch(self):
        other_names = {
            cap["name"] for cap in capsurface.manifest_doc()["capabilities"]
        } - SUPPORTED_GET
        self.assertTrue({"pae.outcomes", "pae.familiarity"} <= other_names)
        for name in sorted(other_names):
            with self.subTest(name=name), patch.object(
                capsurface, "handle", return_value=(200, {"stubbed": True})
            ) as handle:
                response = api.cap_call_get(name, x_pae_key="")
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response.headers.get("allow"), "POST")
                self.assertEqual(json.loads(response.body)["error"], "method_not_allowed")
                handle.assert_not_called()

    def test_get_unknown_capability_reports_not_found_before_dispatch(self):
        with patch.object(
            capsurface, "handle", return_value=(404, {"error": "unknown_capability"})
        ) as handle:
            response = api.cap_call_get("pae.test_missing", x_pae_key="")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(json.loads(response.body)["error"], "unknown_capability")
        handle.assert_not_called()

    def test_supported_get_keeps_key_and_scope_checks(self):
        cases = (
            ((None, None), 401, "unauthorized"),
            (("ext:test", ["write"]), 403, "scope_denied"),
        )
        for resolution, status, error in cases:
            with self.subTest(status=status), patch.object(
                capsurface.keys_mod, "resolve", return_value=resolution
            ) as resolve, patch.object(capsurface, "audit"), patch.object(
                capsurface, "_dispatch"
            ) as dispatch:
                response = api.cap_call_get("pae.params", x_pae_key="synthetic")
                self.assertEqual(response.status_code, status)
                self.assertEqual(json.loads(response.body)["error"], error)
                resolve.assert_called_once_with("synthetic")
                dispatch.assert_not_called()

    def test_post_still_forwards_body_key_and_response(self):
        body = {"evaluate_decisions": False}
        with patch.object(
            capsurface, "handle", return_value=(200, {"result": {"fixture": True}})
        ) as handle:
            response = api.cap_call_post("pae.outcomes", body=body, x_pae_key="synthetic")
        handle.assert_called_once_with("pae.outcomes", body, "synthetic")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body), {"result": {"fixture": True}})

    def test_manifest_remains_a_separate_get_route(self):
        route = next(
            route for route in api.app.routes if route.path == "/v1/cap/manifest"
        )
        self.assertIn("GET", route.methods)
        self.assertIs(route.endpoint, api.cap_manifest)
        self.assertIn("capabilities", route.endpoint())


if __name__ == "__main__":
    unittest.main(verbosity=2)
