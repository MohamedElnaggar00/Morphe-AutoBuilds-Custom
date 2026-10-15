import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import downloader
from scripts import check_app_updates
from scripts import check_release_updates


SOURCE_ID = "gboard-pixelboard-thinking"
SOURCE_LABEL = "Gboard-PixelBoard @Thinking"
BUNDLE_URL = "https://raw.githubusercontent.com/Akshayykadam/PixelBoard/main/patches-bundle.json"


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class PixelBoardMppSourceTests(unittest.TestCase):
    def test_downloader_uses_versioned_mpp_manifest_with_morphe_tools(self):
        cli_release = {
            "assets": [
                {"name": "morphe-cli-1.18.3-all.jar", "browser_download_url": "https://example.invalid/cli.jar"}
            ]
        }
        universal_release = {
            "assets": [
                {"name": "patches-1.52.0.mpp", "browser_download_url": "https://example.invalid/universal.mpp"}
            ]
        }
        with (
            patch.object(
                downloader.session,
                "get",
                return_value=_FakeResponse({
                    "version": "1.0.7",
                    "download_url": "https://raw.githubusercontent.com/Akshayykadam/PixelBoard/main/patches/PixelBoard.mpp",
                }),
            ),
            patch.object(
                downloader,
                "download_resource",
                side_effect=lambda url, name=None: Path(name or "downloaded.bin"),
            ),
            patch.object(
                downloader.utils,
                "detect_github_release",
                side_effect=[cli_release, universal_release],
            ),
        ):
            files, name = downloader.download_from_mpp_manifest(BUNDLE_URL, SOURCE_LABEL)

        self.assertEqual(name, SOURCE_LABEL)
        file_names = [item.name for item in files]
        self.assertTrue(any(item.endswith("v1.0.7.mpp") for item in file_names))
        self.assertTrue(any(item.startswith("morphe-cli-") and item.endswith(".jar") for item in file_names))
        self.assertTrue(any(item.startswith("morphe-universal-") and item.endswith(".mpp") for item in file_names))

    def test_bundle_manifest_signature_changes_with_manifest_version(self):
        with patch.object(
            check_app_updates.provider_utils,
            "fetch_json",
            side_effect=[
                {"version": "1.0.7", "download_url": "https://example.invalid/PixelBoard.mpp"},
                {"version": "1.0.8", "download_url": "https://example.invalid/PixelBoard.mpp"},
            ],
        ):
            first = check_app_updates._fetch_bundle_signature(BUNDLE_URL)
            second = check_app_updates._fetch_bundle_signature(BUNDLE_URL)

        self.assertTrue(first.startswith("bundle:1.0.7@"))
        self.assertTrue(second.startswith("bundle:1.0.8@"))
        self.assertNotEqual(first, second)

    def test_source_signature_tracks_manifest_and_declared_upstream_repositories(self):
        source_config = [
            {"name": SOURCE_LABEL, "bundle_url": BUNDLE_URL},
            {"user": "MorpheApp", "repo": "morphe-cli", "tag": "latest"},
            {"user": "Akshayykadam", "repo": "PixelBoard", "tag": "latest"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp)
            (source_dir / f"{SOURCE_ID}.json").write_text(
                json.dumps(source_config), encoding="utf-8"
            )
            check_app_updates._source_sig_cache.pop(SOURCE_ID, None)
            with (
                patch.object(check_app_updates, "SOURCES_DIR", source_dir),
                patch.object(
                    check_app_updates,
                    "_fetch_bundle_signature",
                    return_value="bundle:1.0.7@manifest-digest",
                ),
                patch.object(
                    check_app_updates,
                    "fetch_repo_signature",
                    return_value="v1@2026-10-15",
                ),
            ):
                signature = check_app_updates.get_source_signature(SOURCE_ID)

        self.assertIn("bundle:1.0.7@manifest-digest", signature)
        self.assertIn("MorpheApp/morphe-cli@", signature)
        self.assertIn("Akshayykadam/PixelBoard@", signature)

    def test_release_planner_uses_manifest_version_not_upstream_release_tag(self):
        source_config = [
            {"name": SOURCE_LABEL, "bundle_url": BUNDLE_URL},
            {"user": "MorpheApp", "repo": "morphe-cli", "tag": "latest"},
            {"user": "Akshayykadam", "repo": "PixelBoard", "tag": "latest"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources_dir = root / "sources"
            sources_dir.mkdir()
            (sources_dir / f"{SOURCE_ID}.json").write_text(
                json.dumps(source_config), encoding="utf-8"
            )
            with (
                patch.object(check_release_updates, "ROOT", root),
                patch.object(
                    check_release_updates.legacy.provider_utils,
                    "fetch_json",
                    return_value={"version": "1.0.7", "download_url": "https://example.invalid/PixelBoard.mpp"},
                ),
            ):
                version = check_release_updates.source_patch_version(SOURCE_ID)

        self.assertEqual(version, "1.0.7")


if __name__ == "__main__":
    unittest.main()
