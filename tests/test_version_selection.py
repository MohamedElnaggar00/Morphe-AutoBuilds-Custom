import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import utils


class VersionSelectionTests(unittest.TestCase):
    def setUp(self):
        utils._source_supported_targets_cache.clear()

    def test_stable_versions_are_preferred_over_experimental(self):
        targets = [
            {"version": "21.38.123", "is_experimental": True},
            {"version": "21.37.42", "is_experimental": True},
            {"version": "21.16.256", "is_experimental": False},
            {"version": "21.13.164", "is_experimental": False},
        ]

        selected = utils.select_preferred_patch_targets(targets)

        self.assertEqual(
            [target["version"] for target in selected],
            ["21.16.256", "21.13.164"],
        )
        self.assertTrue(all(not target["is_experimental"] for target in selected))

    def test_highest_stable_version_is_first(self):
        targets = [
            {"version": "2.9.0", "is_experimental": True},
            {"version": "2.3.0", "is_experimental": False},
            {"version": "2.8.0", "is_experimental": False},
            {"version": "2.1.0", "is_experimental": False},
        ]

        selected = utils.select_preferred_patch_targets(targets)

        self.assertEqual(selected[0]["version"], "2.8.0")

    def test_experimental_only_source_keeps_fallback(self):
        targets = [
            {"version": "9.3.0", "is_experimental": True},
            {"version": "9.2.0", "is_experimental": True},
        ]

        selected = utils.select_preferred_patch_targets(targets)

        self.assertEqual(
            [target["version"] for target in selected],
            ["9.3.0", "9.2.0"],
        )
        self.assertTrue(all(target["is_experimental"] for target in selected))

    def test_unknown_status_is_never_promoted_to_stable(self):
        targets = [
            {"version": "6.0.0", "is_experimental": None},
            {"version": "5.0.0", "is_experimental": False},
        ]

        selected = utils.select_preferred_patch_targets(targets)

        self.assertEqual([target["version"] for target in selected], ["5.0.0"])

    def test_mixed_duplicate_status_is_treated_as_experimental(self):
        targets = [
            {"version": "5.0.0", "is_experimental": False},
            {"version": "5.0.0", "is_experimental": True},
            {"version": "4.0.0", "is_experimental": False},
        ]

        selected = utils.select_preferred_patch_targets(targets)

        self.assertEqual([target["version"] for target in selected], ["4.0.0"])

    @patch("src.utils.fetch_json")
    @patch("src.utils.detect_release")
    def test_source_metadata_uses_exact_release_tag(self, mock_detect_release, mock_fetch_json):
        mock_detect_release.return_value = {"tag_name": "v1.44.0"}
        mock_fetch_json.return_value = {
            "patches": [
                {
                    "compatiblePackages": [
                        {
                            "packageName": "com.google.android.youtube",
                            "targets": [
                                {"version": "21.38.123", "isExperimental": True},
                                {"version": "21.16.256", "isExperimental": False},
                            ],
                        }
                    ]
                }
            ]
        }

        selected = utils.get_source_supported_targets(
            "com.google.android.youtube", "morphe"
        )

        self.assertEqual([target["version"] for target in selected], ["21.16.256"])
        self.assertEqual(mock_fetch_json.call_count, 1)
        called_url = mock_fetch_json.call_args.args[0]
        self.assertIn("/v1.44.0/patches-list.json", called_url)

    @patch("src.utils.fetch_json")
    @patch("src.utils.detect_release")
    def test_source_with_only_experimental_targets_remains_buildable(
        self, mock_detect_release, mock_fetch_json
    ):
        mock_detect_release.return_value = {"tag_name": "v9.0.0"}
        mock_fetch_json.return_value = {
            "patches": [
                {
                    "compatiblePackages": [
                        {
                            "packageName": "com.example.app",
                            "targets": [
                                {"version": "3.2.0", "isExperimental": True},
                                {"version": "3.1.0", "isExperimental": True},
                            ],
                        }
                    ]
                }
            ]
        }

        selected = utils.get_source_supported_targets(
            "com.example.app", "morphe"
        )

        self.assertEqual(
            [target["version"] for target in selected],
            ["3.2.0", "3.1.0"],
        )
        self.assertTrue(all(target["is_experimental"] for target in selected))

    def test_duplicate_version_codes_are_deduplicated(self):
        targets = [
            {
                "version": "1.2.0",
                "is_experimental": False,
                "version_codes": [100, 101],
            },
            {
                "version": "1.2.0",
                "is_experimental": False,
                "version_codes": [101, 102],
            },
        ]

        selected = utils.select_preferred_patch_targets(targets)

        self.assertEqual(selected[0]["version"], "1.2.0")
        self.assertEqual(selected[0]["version_codes"], [100, 101, 102])


    @patch("src.utils.fetch_json")
    @patch("src.utils.detect_release")
    def test_source_contract_preserves_arch_codes_and_constraints(
        self, mock_detect_release, mock_fetch_json
    ):
        mock_detect_release.return_value = {"tag_name": "v2.0.0"}
        mock_fetch_json.return_value = {
            "patches": [
                {
                    "compatiblePackages": [
                        {
                            "packageName": "com.example.app",
                            "apkFileType": "APK",
                            "signatures": ["AA:BB"],
                            "targets": [
                                {
                                    "version": "2.0.0",
                                    "versionCodes": {
                                        "ARM64_V8A": 2002,
                                        "ARMEABI_V7A": 2001,
                                    },
                                    "isExperimental": False,
                                    "minSdk": 23,
                                }
                            ],
                        }
                    ]
                }
            ]
        }

        selected = utils.get_source_supported_targets(
            "com.example.app", "morphe"
        )

        self.assertEqual(selected[0]["version_codes_by_arch"]["ARM64_V8A"], [2002])
        self.assertEqual(selected[0]["version_codes_by_arch"]["ARMEABI_V7A"], [2001])
        self.assertEqual(selected[0]["min_sdk"], 23)
        self.assertEqual(selected[0]["signatures"], ["aa:bb"])
        self.assertEqual(selected[0]["apk_file_types"], ["APK"])

    def test_selection_does_not_flatten_arch_specific_codes_into_an_unbounded_contract(self):
        targets = [
            {
                "version": "3.0.0",
                "version_codes": [3001, 3002],
                "version_codes_by_arch": {
                    "ARM64_V8A": [3002],
                    "ARMEABI_V7A": [3001],
                },
                "is_experimental": False,
            }
        ]

        selected = utils.select_preferred_patch_targets(targets)

        self.assertEqual(
            selected[0]["version_codes_by_arch"]["ARM64_V8A"], [3002]
        )
        self.assertEqual(
            selected[0]["version_codes_by_arch"]["ARMEABI_V7A"], [3001]
        )


    @patch("src.utils._apk_certificate_digests")
    @patch("src.utils._apk_badging")
    @patch("src.utils._artifact_base_apk")
    @patch("src.utils.check_apk_integrity")
    def test_source_artifact_validation_enforces_arch_specific_build_code(
        self, mock_integrity, mock_base, mock_badging, mock_certs
    ):
        mock_integrity.return_value = True
        mock_badging.return_value = {
            "package": "com.example.app",
            "version": "2.0.0",
            "version_code": 2001,
            "min_sdk": 23,
        }
        mock_certs.return_value = {"aabb"}
        with tempfile.NamedTemporaryFile(suffix=".apk") as handle:
            path = Path(handle.name)
            target = {
                "version": "2.0.0",
                "version_codes": [2001, 2002],
                "version_codes_by_arch": {
                    "ARM64_V8A": [2002],
                    "ARMEABI_V7A": [2001],
                },
                "min_sdk": 23,
                "signatures": ["aabb"],
                "apk_file_types": ["APK"],
            }
            mock_base.return_value = (path, None)

            valid, reasons = utils.validate_source_artifact(
                path, target, "com.example.app", "arm64-v8a"
            )

            self.assertFalse(valid)
            self.assertTrue(any("versionCode mismatch" in reason for reason in reasons), reasons)

    @patch("src.utils._apk_certificate_digests")
    @patch("src.utils._apk_badging")
    @patch("src.utils._artifact_base_apk")
    @patch("src.utils.check_apk_integrity")
    def test_source_artifact_validation_accepts_matching_contract(
        self, mock_integrity, mock_base, mock_badging, mock_certs
    ):
        mock_integrity.return_value = True
        mock_badging.return_value = {
            "package": "com.example.app",
            "version": "2.0.0",
            "version_code": 2002,
            "min_sdk": 23,
        }
        mock_certs.return_value = {"aabb"}
        with tempfile.NamedTemporaryFile(suffix=".apk") as handle:
            path = Path(handle.name)
            target = {
                "version": "2.0.0",
                "version_codes": [2001, 2002],
                "version_codes_by_arch": {"ARM64_V8A": [2002]},
                "min_sdk": 23,
                "signatures": ["aabb"],
                "apk_file_types": ["APK"],
            }
            mock_base.return_value = (path, None)

            valid, reasons = utils.validate_source_artifact(
                path, target, "com.example.app", "arm64-v8a"
            )

            self.assertTrue(valid, reasons)
            self.assertEqual(reasons, [])


if __name__ == "__main__":
    unittest.main()
