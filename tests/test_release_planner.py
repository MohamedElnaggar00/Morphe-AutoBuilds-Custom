import unittest
from unittest.mock import patch

from scripts import check_release_updates as planner

class ReleasePlannerTests(unittest.TestCase):
    ITEM = {"app_name": "youtube", "source": "morphe", "arch": "arm64-v8a"}

    def previous(self, patch_version="1.45.0", app_version="21.16.256",
                 source_signature_digest="abcdef1234567890"):
        return {"patch_version": patch_version, "app_version": app_version,
                "source_signature_digest": source_signature_digest}

    @patch("scripts.check_release_updates._patch_source_signature_digest", return_value="abcdef1234567890")
    @patch("scripts.check_release_updates.expected_app_version", return_value="21.16.256")
    @patch("scripts.check_release_updates.source_patch_version", return_value="1.45.0")
    def test_exact_match_is_skipped(self, _patch, _app, _sig):
        build, reason, *_ = planner.needs_build(self.ITEM, self.previous())
        self.assertFalse(build)
        self.assertEqual(reason, "up to date")

    @patch("scripts.check_release_updates._patch_source_signature_digest", return_value="abcdef1234567890")
    @patch("scripts.check_release_updates.expected_app_version", return_value="21.16.256")
    @patch("scripts.check_release_updates.source_patch_version", return_value="1.46.0")
    def test_patch_update_triggers_build(self, _patch, _app, _sig):
        build, reason, *_ = planner.needs_build(self.ITEM, self.previous())
        self.assertTrue(build)
        self.assertIn("patch update", reason)

    @patch("scripts.check_release_updates._patch_source_signature_digest", return_value="abcdef1234567890")
    @patch("scripts.check_release_updates.expected_app_version", return_value="21.17.0")
    @patch("scripts.check_release_updates.source_patch_version", return_value="1.45.0")
    def test_app_update_triggers_build(self, _patch, _app, _sig):
        build, reason, *_ = planner.needs_build(self.ITEM, self.previous())
        self.assertTrue(build)
        self.assertIn("app update", reason)

    @patch("scripts.check_release_updates._patch_source_signature_digest", return_value="1234567890abcdef")
    @patch("scripts.check_release_updates.expected_app_version", return_value="21.16.256")
    @patch("scripts.check_release_updates.source_patch_version", return_value="1.45.0")
    def test_same_patch_version_with_changed_source_triggers_build(self, _patch, _app, _sig):
        build, reason, *_ = planner.needs_build(self.ITEM, self.previous())
        self.assertTrue(build)
        self.assertEqual(reason, "patch source content/metadata changed")

    @patch("scripts.check_release_updates._patch_source_signature_digest", return_value="abcdef1234567890")
    @patch("scripts.check_release_updates.expected_app_version", return_value="21.16.256")
    @patch("scripts.check_release_updates.source_patch_version", return_value="1.45.0")
    def test_missing_history_triggers_build(self, _patch, _app, _sig):
        build, reason, *_ = planner.needs_build(self.ITEM, None)
        self.assertTrue(build)
        self.assertIn("no previous release", reason)

    def test_release_title_parser_reads_source_signature(self):
        releases = [{
            "name": "youtube v21.16.256 — Patch v1.45.0 — morphe — arm64-v8a",
            "tag_name": "build-youtube-test",
            "published_at": "2026-10-02T08:00:00Z",
            "body": "<!-- morphe-build-meta: source_signature_digest=abcdef1234567890 -->",
            "assets": [{"name": "youtube-arm64-v8a-morphe-patch-v1.45.0-app-v21.16.256.apk"}],
        }]
        found = planner.latest_build_for("youtube", "morphe", "arm64-v8a", releases)
        self.assertIsNotNone(found)
        self.assertEqual(found["source_signature_digest"], "abcdef1234567890")

    def test_devanced_build_is_explicitly_arm64(self):
        matrix = planner.legacy.build_full_matrix()
        entries = [item for item in matrix if item["app_name"] == "messenger" and item["source"] == "devanced"]
        self.assertEqual(entries, [{"app_name": "messenger", "source": "devanced", "arch": "arm64-v8a"}])

if __name__ == "__main__":
    unittest.main()
