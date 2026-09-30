import unittest

from src import utils


class VersionSelectionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
