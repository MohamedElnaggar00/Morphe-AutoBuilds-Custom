import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src import gplaydl, utils


PACKAGE = "com.facebook.katana"
VERSION = "582.0.0.50.54"
ARM64_CODE = 475417104
ARMV7_CODE = 475417036


class SourceVersionCodeTests(unittest.TestCase):
    def setUp(self):
        self.target = {
            "version": VERSION,
            "version_codes": [ARMV7_CODE, ARM64_CODE],
            "version_codes_by_arch": {
                "ARM64_V8A": [ARM64_CODE],
                "ARMEABI_V7A": [ARMV7_CODE],
            },
        }

    def test_arm64_uses_only_arm64_code(self):
        with patch(
            "src.utils.get_source_supported_targets",
            return_value=[self.target],
        ):
            self.assertEqual(
                utils.get_source_supported_version_codes(
                    PACKAGE, "hushfacebook", "arm64-v8a"
                ),
                {VERSION: [ARM64_CODE]},
            )

    def test_armv7_uses_only_armv7_code(self):
        with patch(
            "src.utils.get_source_supported_targets",
            return_value=[self.target],
        ):
            self.assertEqual(
                utils.get_source_supported_version_codes(
                    PACKAGE, "hushfacebook", "armeabi-v7a"
                ),
                {VERSION: [ARMV7_CODE]},
            )

    def test_missing_abi_mapping_does_not_fall_back_to_other_abis(self):
        with patch(
            "src.utils.get_source_supported_targets",
            return_value=[self.target],
        ):
            self.assertEqual(
                utils.get_source_supported_version_codes(
                    PACKAGE, "hushfacebook", "x86_64"
                ),
                {VERSION: []},
            )

    def test_generic_codes_remain_supported_when_source_has_no_abi_map(self):
        generic_target = {
            "version": VERSION,
            "version_codes": [ARM64_CODE],
        }
        with patch(
            "src.utils.get_source_supported_targets",
            return_value=[generic_target],
        ):
            self.assertEqual(
                utils.get_source_supported_version_codes(
                    PACKAGE, "generic-source", "arm64-v8a"
                ),
                {VERSION: [ARM64_CODE]},
            )


class CompatibilityProfileTests(unittest.TestCase):
    def test_mismatched_profile_version_code_is_skipped(self):
        api = types.ModuleType("gplaydl.api")

        class AppNotAvailableError(Exception):
            pass

        class AppNotSupportedError(Exception):
            pass

        class AuthExpiredError(Exception):
            pass

        class PlayAPIError(Exception):
            pass

        api.AppNotAvailableError = AppNotAvailableError
        api.AppNotSupportedError = AppNotSupportedError
        api.AuthExpiredError = AuthExpiredError
        api.PlayAPIError = PlayAPIError
        api.get_details = lambda package, auth: SimpleNamespace(
            version_string=VERSION,
            version_code=ARMV7_CODE if auth == "wrong-abi" else ARM64_CODE,
        )
        api.purchase = lambda package, code, auth: "delivery-token"
        api.get_delivery = lambda package, code, auth, token: SimpleNamespace(
            gzipped_url=None,
            gzipped_size=None,
            download_url="https://example.invalid/base.apk",
            cookies=None,
            sha256=None,
            sha1=None,
            splits=[],
        )

        auth_module = types.ModuleType("gplaydl.auth")
        auth_module.fetch_token_for_profile = lambda profile: profile["token"]

        download_module = types.ModuleType("gplaydl.download")
        download_module.DownloadSpec = lambda **kwargs: SimpleNamespace(**kwargs)
        download_calls = []

        def download_batch(specs):
            download_calls.append(specs)
            specs[0].dest.write_bytes(b"test artifact")

        download_module.download_batch = download_batch

        profiles_module = types.ModuleType("gplaydl.profiles")
        profiles_module.get_compat_profiles = lambda arch: [
            ("Wrong ABI", {"UserReadableName": "Wrong ABI", "token": "wrong-abi"}),
            ("Correct ABI", {"UserReadableName": "Correct ABI", "token": "correct-abi"}),
        ]

        fake_package = types.ModuleType("gplaydl")
        fake_package.__path__ = []
        modules = {
            "gplaydl": fake_package,
            "gplaydl.api": api,
            "gplaydl.auth": auth_module,
            "gplaydl.download": download_module,
            "gplaydl.profiles": profiles_module,
        }

        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, modules):
            result = gplaydl._download_with_compat_profiles(
                PACKAGE, VERSION, ARM64_CODE, "arm64-v8a", Path(tmp)
            )

            self.assertEqual(len(download_calls), 1)
            self.assertEqual(
                [path.name for path in result],
                [f"{PACKAGE}-{ARM64_CODE}.apk"],
            )
            self.assertTrue(result[0].is_file())


if __name__ == "__main__":
    unittest.main()
