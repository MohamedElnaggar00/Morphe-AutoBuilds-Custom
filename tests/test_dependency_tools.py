import unittest

from scripts.prepare_build_tools import morphe_cli_sources, version_key


class DependencyToolTests(unittest.TestCase):
    def test_version_key_orders_releases(self):
        self.assertGreater(version_key("4.2.1"), version_key("4.2.0"))
        self.assertGreater(version_key("4.2.0"), version_key("4.1.0"))

    def test_all_morphe_cli_sources_use_latest(self):
        sources = morphe_cli_sources()
        self.assertTrue(sources)
        for source_name, _repo, tag in sources:
            self.assertEqual(
                tag,
                "latest",
                f"{source_name} must keep Morphe CLI on tag=latest",
            )


if __name__ == "__main__":
    unittest.main()
