import re
import unittest

from scripts.update_readme import _published_release_versions


class ReadmeVersionRegexTests(unittest.TestCase):
    def test_application_version_metadata_is_read(self):
        body = "- **Application:** x\n- **Application version:** 12.29.1-prod.01\n- **Patch source:** piko-newx\n- **Architecture:** arm64-v8a\n"
        pattern = r"^- \*\*Application version:\*\* (.+)$"
        self.assertEqual(re.search(pattern, body, re.MULTILINE).group(1), "12.29.1-prod.01")

    def test_apk_version_fallback_is_read(self):
        name = "x-arm64-v8a-piko-patches-patch-v3.49.0-app-v12.29.1-prod.01.apk"
        match = re.search(r"-patch-v.+-app-v(.+)\.apk$", name, re.IGNORECASE)
        self.assertEqual(match.group(1), "12.29.1-prod.01")

    def test_release_tag_version_fallback_is_read(self):
        tag = "build-gboard-v18.0.3.954559732-release-arm64-v8a-patch-v3.11.0-jasonwu1994-138"
        match = re.search(
            r"^build-gboard-v(.+?)-patch-v", tag, re.IGNORECASE
        )
        self.assertEqual(
            match.group(1),
            "18.0.3.954559732-release-arm64-v8a",
        )


if __name__ == "__main__":
    unittest.main()
