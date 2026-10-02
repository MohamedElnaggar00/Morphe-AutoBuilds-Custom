import os
import unittest
from unittest.mock import patch

from src.__main__ import _force_rebuild_requested


class ForceRebuildTests(unittest.TestCase):
    def test_manual_force_flag_is_enabled(self):
        with patch.dict(os.environ, {"MORPHE_FORCE_REBUILD": "true"}, clear=False):
            self.assertTrue(_force_rebuild_requested())

    def test_legacy_test_flag_is_still_supported(self):
        with patch.dict(os.environ, {"MORPHE_TEST_FORCE_REBUILD": "1"}, clear=False):
            with patch.dict(os.environ, {"MORPHE_FORCE_REBUILD": ""}, clear=False):
                self.assertTrue(_force_rebuild_requested())

    def test_force_flag_is_disabled_by_default(self):
        with patch.dict(
            os.environ,
            {"MORPHE_FORCE_REBUILD": "", "MORPHE_TEST_FORCE_REBUILD": ""},
            clear=False,
        ):
            self.assertFalse(_force_rebuild_requested())


if __name__ == "__main__":
    unittest.main()
