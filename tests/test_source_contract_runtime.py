import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import apkmirror, downloader, gplaydl, utils


class SourceContractRuntimeTests(unittest.TestCase):
    def test_verified_asset_debloat_failure_identifies_only_matching_patch(self):
        from src.__main__ import _verified_asset_debloat_patch

        asset_error = (
            "FAILED: Remove content credential and card scanner assets\\n"
            "Core Asset De-bloat: found 2 target files, but no reviewed path set matched"
        )
        creation_error = (
            "FAILED: Remove creation tools\\n"
            "Studio & Creation De-bloat: found 1 target files, but no reviewed path set matched"
        )
        self.assertEqual(
            _verified_asset_debloat_patch(asset_error),
            "Remove content credential and card scanner assets",
        )
        self.assertEqual(
            _verified_asset_debloat_patch(creation_error),
            "Remove creation tools",
        )
        self.assertIsNone(_verified_asset_debloat_patch("Patching aborted: fingerprint mismatch"))
        self.assertIsNone(_verified_asset_debloat_patch("no reviewed path set matched"))
        self.assertIsNone(
            _verified_asset_debloat_patch(
                "FAILED: Playback quality\\nUnknown patch error: no reviewed path set matched"
            )
        )

    def test_resource_patch_retry_bound_is_two(self):
        # Each guarded patch can be disabled at most once per build attempt.
        from pathlib import Path
        import re

        source = Path("src/__main__.py").read_text(encoding="utf-8")
        self.assertRegex(source, r"max_resource_retries\s*=\s*2")
        self.assertIn("_verified_asset_debloat_patch(retry_output)", source)
        self.assertIn("not next_patch", source)  # other patch errors must remain fatal
    def test_aapt2_badging_parses_version_code_and_min_sdk(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "package: name='com.example.app' versionCode='2024701030' "
                "versionName='47.1.3'\n"
                "sdkVersion:'23'\n"
            ),
            stderr="",
        )

        with patch("src.utils._find_aapt2", return_value="/fake/aapt2"),              patch("src.utils.subprocess.run", return_value=completed):
            info = utils._apk_badging(Path("example.apk"))

        self.assertEqual(info["package"], "com.example.app")
        self.assertEqual(info["version"], "47.1.3")
        self.assertEqual(info["version_code"], 2024701030)
        self.assertEqual(info["min_sdk"], 23)

    def test_aapt2_badging_falls_back_to_apkanalyzer_for_min_sdk(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command[0] == "/fake/aapt2":
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=(
                        "package: name='com.example.app' versionCode='100' "
                        "versionName='1.0.0'\n"
                    ),
                    stderr="",
                )
            if command[0] == "/fake/apkanalyzer":
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout="28\n",
                    stderr="",
                )
            raise AssertionError(f"unexpected command: {command}")

        with patch("src.utils._find_aapt2", return_value="/fake/aapt2"), \
             patch("src.utils._find_apkanalyzer", return_value="/fake/apkanalyzer"), \
             patch("src.utils._find_aapt", return_value=None), \
             patch("src.utils.subprocess.run", side_effect=fake_run):
            info = utils._apk_badging(Path("example.apk"))

        self.assertEqual(info["package"], "com.example.app")
        self.assertEqual(info["version"], "1.0.0")
        self.assertEqual(info["version_code"], 100)
        self.assertEqual(info["min_sdk"], 28)
        self.assertEqual(
            calls,
            [
                ["/fake/aapt2", "dump", "badging", "example.apk"],
                ["/fake/apkanalyzer", "manifest", "min-sdk", "example.apk"],
            ],
        )

    def test_apkm_is_accepted_when_source_requires_xapk(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "example.apkm"
            artifact.write_bytes(b"not-a-real-apkm")

            with patch("src.utils.check_apk_integrity", return_value=True), \
                 patch("src.utils._artifact_base_apk", return_value=(artifact, None)), \
                 patch(
                     "src.utils._apk_badging",
                     return_value={
                         "package": "com.example.app",
                         "version": "1.0.0",
                         "version_code": 100,
                         "min_sdk": 29,
                     },
                 ):
                valid, reasons = utils.validate_source_artifact(
                    artifact,
                    {
                        "version": "1.0.0",
                        "version_codes": [100],
                        "min_sdk": 29,
                        "apk_file_types": ["XAPK_REQUIRED"],
                    },
                    "com.example.app",
                    "arm64-v8a",
                )

            self.assertTrue(valid, reasons)
            self.assertEqual(reasons, [])

    def test_xapk_is_accepted_when_source_requires_xapk(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "example.xapk"
            artifact.write_bytes(b"not-a-real-xapk")

            with patch("src.utils.check_apk_integrity", return_value=True), \
                 patch("src.utils._artifact_base_apk", return_value=(artifact, None)), \
                 patch(
                     "src.utils._apk_badging",
                     return_value={
                         "package": "com.example.app",
                         "version": "1.0.0",
                         "version_code": 100,
                         "min_sdk": 29,
                     },
                 ):
                valid, reasons = utils.validate_source_artifact(
                    artifact,
                    {
                        "version": "1.0.0",
                        "version_codes": [100],
                        "min_sdk": 29,
                        "apk_file_types": ["XAPK_REQUIRED"],
                    },
                    "com.example.app",
                    "arm64-v8a",
                )

            self.assertTrue(valid, reasons)
            self.assertEqual(reasons, [])

    def test_merged_google_play_apk_is_accepted_for_xapk_contract_only_when_opted_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "usbhotspot-gplaydl.apk"
            artifact.write_bytes(b"not-a-real-apk")

            target = {
                "version": "1.7",
                "version_codes": [7],
                "min_sdk": 21,
                "apk_file_types": ["XAPK_REQUIRED"],
            }

            with patch("src.utils.check_apk_integrity", return_value=True),                  patch("src.utils._artifact_base_apk", return_value=(artifact, None)),                  patch(
                     "src.utils._apk_badging",
                     return_value={
                         "package": "kha.prog.usbhotspot",
                         "version": "1.7",
                         "version_code": 7,
                         "min_sdk": 21,
                     },
                 ):
                valid, reasons = utils.validate_source_artifact(
                    artifact, target, "kha.prog.usbhotspot", "arm64-v8a",
                    allow_merged_play_apk=True,
                )
                self.assertTrue(valid, reasons)

                valid, reasons = utils.validate_source_artifact(
                    artifact, target, "kha.prog.usbhotspot", "arm64-v8a",
                    allow_merged_play_apk=False,
                )
                self.assertFalse(valid)
                self.assertTrue(any("artifact type mismatch" in r for r in reasons))


    def test_google_play_abi_scan_allows_apk_without_native_libraries(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "base.apk"
            with __import__("zipfile").ZipFile(artifact, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"manifest")
                archive.writestr("res/values/strings.xml", b"resources")

            self.assertEqual(gplaydl._native_abis(artifact), set())

    def test_google_play_abi_scan_detects_native_abis(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "base.apk"
            with __import__("zipfile").ZipFile(artifact, "w") as archive:
                archive.writestr("lib/arm64-v8a/libexample.so", b"native")
                archive.writestr("lib/x86_64/libexample.so", b"native")

            self.assertEqual(
                gplaydl._native_abis(artifact),
                {"arm64-v8a", "x86_64"},
            )

    def test_apkm_is_accepted_when_source_allows_apk(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "example.apkm"
            artifact.write_bytes(b"not-a-real-apkm")

            with patch("src.utils.check_apk_integrity", return_value=True), \
                 patch(
                     "src.utils._artifact_base_apk",
                     return_value=(artifact, None),
                 ), \
                 patch(
                     "src.utils._apk_badging",
                     return_value={
                         "package": "com.example.app",
                         "version": "1.0.0",
                         "version_code": 100,
                         "min_sdk": 23,
                     },
                 ):
                valid, reasons = utils.validate_source_artifact(
                    artifact,
                    {
                        "version": "1.0.0",
                        "version_codes": [100],
                        "min_sdk": 23,
                        "apk_file_types": ["APK_REQUIRED"],
                    },
                    "com.example.app",
                    "arm64-v8a",
                )

            self.assertTrue(valid, reasons)
            self.assertEqual(reasons, [])

    def test_apkmirror_stops_after_cloudflare_block(self):
        calls = []

        def blocked_variant(*args, **kwargs):
            calls.append(args[0])
            apkmirror._blocked_by_cloudflare = True
            return None, False

        try:
            with patch.object(
                apkmirror,
                "_get_api_variant_urls",
                return_value=[
                    ("https://example.invalid/variant-1", "1.0.0"),
                    ("https://example.invalid/variant-2", "1.0.0"),
                ],
            ), patch.object(
                apkmirror,
                "_download_from_variant_page",
                side_effect=blocked_variant,
            ), patch.object(
                apkmirror,
                "_get_direct_release_page",
            ) as direct_release:
                result = apkmirror.get_download_link(
                    "1.0.0",
                    "example",
                    {
                        "package": "com.example.app",
                        "type": "APK",
                        "arch": "arm64-v8a",
                        "dpi": "nodpi",
                        "name": "example",
                        "org": "example",
                    },
                    "arm64-v8a",
                )

            self.assertIsNone(result)
            self.assertEqual(len(calls), 1)
            direct_release.assert_not_called()
        finally:
            apkmirror._blocked_by_cloudflare = False


    def test_apkmirror_binary_download_reuses_resolving_session_and_referer(self):
        variant_url = "https://www.apkmirror.com/apk/example/variant/"
        download_page_url = "https://www.apkmirror.com/apk/example/download/"
        file_url = "https://download.apkmirror.com/files/example.apkm"
        variant_response = __import__("types").SimpleNamespace(
            content=(
                b'<a class="downloadButton" href="/apk/example/download/">Download</a>'
            ),
            text="variant",
            headers={},
            url=variant_url,
            raise_for_status=lambda: None,
        )
        download_response = __import__("types").SimpleNamespace(
            content=(
                b'<a id="download-link" href="https://download.apkmirror.com/files/example.apkm">File</a>'
            ),
            text="download",
            headers={},
            url=download_page_url,
            raise_for_status=lambda: None,
        )
        binary_response = __import__("types").SimpleNamespace(
            url=file_url,
            headers={"content-length": "4"},
            raise_for_status=lambda: None,
            iter_content=lambda chunk_size: iter([b"test"]),
        )

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(apkmirror, "_cf_get", side_effect=[variant_response, download_response]), \
             patch.object(apkmirror, "_variant_matches_extra_criteria", return_value=True), \
             patch.object(apkmirror._apkmirror_session, "get", return_value=binary_response) as get_file:
            resolved, readable = apkmirror._download_from_variant_page(
                variant_url,
                {"type": "BUNDLE"},
                "1.0.0",
            )
            self.assertEqual(resolved, file_url)
            self.assertTrue(readable)

            output = Path(tmp) / "example.apkm"
            result = apkmirror.download_resource(file_url, name=str(output))

            self.assertEqual(result, output)
            self.assertEqual(output.read_bytes(), b"test")
            self.assertEqual(
                get_file.call_args.kwargs["headers"]["Referer"],
                download_page_url,
            )
            self.assertTrue(get_file.call_args.kwargs["stream"])
            self.assertGreaterEqual(get_file.call_args.kwargs["timeout"], 1)

    def test_downloader_routes_apkmirror_binary_through_provider_session(self):
        package_version = "12.35.0-prod.01"
        file_url = "https://download.apkmirror.com/files/x.apkm"
        output = Path("x-test.apkm")
        try:
            with patch.dict("os.environ", {"SOURCE": "piko-newx"}), \
                 patch.object(utils, "get_source_supported_targets", return_value=[]), \
                 patch.object(apkmirror, "get_download_link", return_value=file_url), \
                 patch.object(apkmirror, "download_resource", return_value=output) as provider_download:
                result = downloader.download_platform(
                    "x",
                    "apkmirror",
                    "morphe-cli.jar",
                    "patches-3.59.0.mpp",
                    arch="arm64-v8a",
                    override_version=package_version,
                )

            self.assertEqual(result, (output, package_version, [package_version]))
            provider_download.assert_called_once_with(file_url)
        finally:
            output.unlink(missing_ok=True)

    def test_source_targets_load_patches_list_release_asset(self):
        package = "com.twitter.android"
        source = "piko-newx"
        asset_url = "https://downloads.example.invalid/patches-list.json"
        manifest = {
            "packages": [
                {
                    "packageName": package,
                    "signatures": [],
                    "apkFileType": "BUNDLE",
                    "targets": [
                        {
                            "version": "12.35.0-prod.01",
                            "description": "Arm64 builds 123456789",
                        }
                    ],
                }
            ]
        }
        release = {
            "tag_name": "v3.59.0",
            "assets": [
                {
                    "name": "patches-list.json",
                    "browser_download_url": asset_url,
                }
            ],
        }

        fetch_calls = []

        def fake_fetch_json(url):
            fetch_calls.append(url)
            if url == asset_url:
                return manifest
            raise AssertionError(f"raw metadata fallback should not run, got: {url}")

        with patch.dict(utils._source_supported_targets_cache, {}, clear=True), \
             patch.object(utils, "detect_release", return_value=release), \
             patch.object(utils, "fetch_json", side_effect=fake_fetch_json):
            targets = utils.get_source_supported_targets(package, source)
            self.assertEqual(fetch_calls, [asset_url])
            self.assertEqual(len(targets), 1)
            self.assertEqual(targets[0]["version"], "12.35.0-prod.01")
            self.assertEqual(
                targets[0]["version_codes_by_arch"]["ARM64_V8A"],
                [123456789],
            )
            self.assertEqual(
                utils.get_source_supported_version_codes(package, source, "arm64-v8a"),
                {"12.35.0-prod.01": [123456789]},
            )

if __name__ == "__main__":
    unittest.main()
