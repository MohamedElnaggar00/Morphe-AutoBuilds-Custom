import unittest

from scripts import check_release_updates as planner
from src.__main__ import _release_has_exact_build


class ReleaseIdentityTests(unittest.TestCase):
    def test_x_release_is_found_by_exact_source_metadata(self):
        release = {
            "name": "X - piko-newx",
            "tag_name": "build-x-v12.29.1-prod.01-patch-v3.49.0-piko-newx-138",
            "published_at": "2026-10-03T09:29:34Z",
            "body": (
                "- **Application:** x\n"
                "- **Application version:** 12.29.1-prod.01\n"
                "- **Patch source:** piko-newx\n"
                "- **Patch version:** 3.49.0\n"
                "- **Architecture:** arm64-v8a\n"
                "- **APK:** x-arm64-v8a-piko-patches-patch-v3.49.0-app-v12.29.1-prod.01.apk\n"
            ),
            "assets": [{
                "name": "x-arm64-v8a-piko-patches-patch-v3.49.0-app-v12.29.1-prod.01.apk"
            }],
        }

        previous = planner.latest_build_for("x", "piko-newx", "arm64-v8a", [release])

        self.assertIsNotNone(previous)
        self.assertEqual(previous["patch_version"], "3.49.0")
        self.assertEqual(previous["app_version"], "12.29.1-prod.01")

    def test_gboard_release_is_found_even_when_asset_uses_display_name(self):
        release = {
            "name": "Gboard - jasonwu1994",
            "tag_name": (
                "build-gboard-v18.0.3.954559732-release-arm64-v8a-"
                "patch-v3.11.0-jasonwu1994-138"
            ),
            "published_at": "2026-10-03T09:28:35Z",
            "body": (
                "- **Application:** gboard\n"
                "- **Application version:** 18.0.3.954559732-release-arm64-v8a\n"
                "- **Patch source:** jasonwu1994\n"
                "- **Patch version:** 3.11.0\n"
                "- **Architecture:** arm64-v8a\n"
                "- **APK:** gboard-arm64-v8a-morphe-patches-patch-v3.11.0-"
                "app-v18.0.3.954559732-release-arm64-v8a.apk\n"
            ),
            "assets": [{
                "name": (
                    "gboard-arm64-v8a-morphe-patches-patch-v3.11.0-"
                    "app-v18.0.3.954559732-release-arm64-v8a.apk"
                )
            }],
        }

        previous = planner.latest_build_for(
            "gboard", "jasonwu1994", "arm64-v8a", [release]
        )

        self.assertIsNotNone(previous)
        self.assertEqual(previous["patch_version"], "3.11.0")
        self.assertEqual(
            previous["app_version"],
            "18.0.3.954559732-release-arm64-v8a",
        )

    def test_same_app_patch_arch_from_another_source_is_not_a_match(self):
        release = {
            "name": "X - piko-newx",
            "tag_name": "build-x-v12.29.1-prod.01-patch-v3.49.0-piko-newx-138",
            "published_at": "2026-10-03T09:29:34Z",
            "body": (
                "- **Application:** x\n"
                "- **Application version:** 12.29.1-prod.01\n"
                "- **Patch source:** piko-newx\n"
                "- **Patch version:** 3.49.0\n"
                "- **Architecture:** arm64-v8a\n"
            ),
            "assets": [{
                "name": "x-arm64-v8a-piko-patches-patch-v3.49.0-app-v12.29.1-prod.01.apk"
            }],
        }

        previous = planner.latest_build_for("x", "another-source", "arm64-v8a", [release])

        self.assertIsNone(previous)

    def test_legacy_source_display_name_can_still_be_resolved(self):
        release = {
            "name": "X - piko-newx",
            "tag_name": "build-x-v12.29.1-prod.01-patch-v3.49.0-piko-newx-138",
            "published_at": "2026-10-03T09:29:34Z",
            "body": "",
            "assets": [{
                "name": "x-arm64-v8a-piko-patches-patch-v3.49.0-app-v12.29.1-prod.01.apk"
            }],
        }

        previous = planner.latest_build_for("x", "piko-newx", "arm64-v8a", [release])

        self.assertIsNotNone(previous)
        self.assertEqual(previous["patch_version"], "3.49.0")

    def test_build_skip_requires_exact_source_identity(self):
        release = {
            "tag_name": "build-x-v12.29.1-prod.01-patch-v3.49.0-piko-newx-138",
            "body": (
                "- **Application:** x\n"
                "- **Application version:** 12.29.1-prod.01\n"
                "- **Patch source:** piko-newx\n"
                "- **Patch version:** 3.49.0\n"
                "- **Architecture:** arm64-v8a\n"
            ),
            "assets": [{
                "name": "x-arm64-v8a-piko-patches-patch-v3.49.0-app-v12.29.1-prod.01.apk"
            }],
        }

        matched, asset = _release_has_exact_build(
            release, "x", "piko-newx", "arm64-v8a", "3.49.0", "12.29.1-prod.01"
        )
        self.assertTrue(matched)
        self.assertEqual(asset, "x-arm64-v8a-piko-patches-patch-v3.49.0-app-v12.29.1-prod.01.apk")

        matched, _ = _release_has_exact_build(
            release, "x", "another-source", "arm64-v8a", "3.49.0", "12.29.1-prod.01"
        )
        self.assertFalse(matched)

    def test_build_skip_rejects_wrong_source_display_name(self):
        release = {
            "tag_name": "build-x-v12.29.1-prod.01-patch-v3.49.0-piko-newx-138",
            "body": (
                "- **Application:** x\n"
                "- **Application version:** 12.29.1-prod.01\n"
                "- **Patch source:** piko-newx\n"
                "- **Patch version:** 3.49.0\n"
                "- **Architecture:** arm64-v8a\n"
            ),
            "assets": [{
                "name": "x-arm64-v8a-other-patches-patch-v3.49.0-app-v12.29.1-prod.01.apk"
            }],
        }

        matched, _ = _release_has_exact_build(
            release, "x", "piko-newx", "arm64-v8a", "3.49.0", "12.29.1-prod.01"
        )
        self.assertFalse(matched)


if __name__ == "__main__":
    unittest.main()
