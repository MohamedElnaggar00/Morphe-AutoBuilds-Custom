import unittest
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup

from src import apkmirror


def _response(html: str):
    response = MagicMock()
    response.status_code = 200
    response.content = html.encode()
    response.text = html
    response.url = "https://www.apkmirror.com/test"
    response.raise_for_status.return_value = None
    return response


class ApkMirrorVariantSelectionTests(unittest.TestCase):
    def _release_soup(self, rows: str):
        return BeautifulSoup(
            f"""
            <html><body>
              <div class="variants-table">{rows}</div>
            </body></html>
            """,
            "html.parser",
        )

    def _config(self):
        return {
            "org": "redditinc",
            "name": "reddit",
            "package": "com.reddit.frontpage",
            "type": "",
            "arch": "arm64-v8a",
            "dpi": "",
        }

    @patch("src.apkmirror._get_api_variant_urls", return_value=[])
    @patch("src.apkmirror._get_direct_release_page")
    @patch("src.apkmirror._cf_get")
    def test_arm64_can_select_universal_apkm_bundle(
        self, mock_cf_get, mock_release
    ):
        rows = """
        <div class="table-row">
          <a href="/apk/redditinc/reddit/reddit-2026-14-0-2-android-apk-download/">
            2026.14.0 BUNDLE 3 S 2614121 universal Android 10+ 120-640dpi
          </a>
        </div>
        """
        mock_release.return_value = (
            self._release_soup(rows),
            "https://www.apkmirror.com/apk/redditinc/reddit/reddit-2026-14-0-release/",
        )
        mock_cf_get.side_effect = [
            _response('<a class="downloadButton" href="/download-page/">Download</a>'),
            _response('<a id="download-link" href="https://cdn.example/reddit.apkm">Download</a>'),
        ]

        result = apkmirror.get_download_link(
            "2026.14.0", "reddit", self._config(), "arm64-v8a"
        )

        self.assertEqual(result, "https://cdn.example/reddit.apkm")

    @patch("src.apkmirror._get_api_variant_urls", return_value=[])
    @patch("src.apkmirror._get_direct_release_page")
    @patch("src.apkmirror._cf_get")
    def test_arm64_does_not_accept_universal_standalone_apk(
        self, mock_cf_get, mock_release
    ):
        rows = """
        <div class="table-row">
          <a href="/apk/redditinc/reddit/reddit-2026-14-0-2-android-apk-download/">
            2026.14.0 APK 2614121 universal Android 10+ 120-640dpi
          </a>
        </div>
        """
        mock_release.return_value = (
            self._release_soup(rows),
            "https://www.apkmirror.com/apk/redditinc/reddit/reddit-2026-14-0-release/",
        )

        result = apkmirror.get_download_link(
            "2026.14.0", "reddit", self._config(), "arm64-v8a"
        )

        self.assertIsNone(result)
        mock_cf_get.assert_not_called()

    @patch("src.apkmirror._get_api_variant_urls", return_value=[])
    @patch("src.apkmirror._get_direct_release_page")
    @patch("src.apkmirror._cf_get")
    def test_exact_arm64_variant_still_wins_over_universal_bundle(
        self, mock_cf_get, mock_release
    ):
        rows = """
        <div class="table-row">
          <a href="/universal-download/">2026.14.0 BUNDLE 3 S 2614121 universal Android 10+</a>
        </div>
        <div class="table-row">
          <a href="/arm64-download/">2026.14.0 APK 2614121 arm64-v8a Android 10+</a>
        </div>
        """
        mock_release.return_value = (
            self._release_soup(rows),
            "https://www.apkmirror.com/release/",
        )
        mock_cf_get.side_effect = [
            _response('<a class="downloadButton" href="/download-page/">Download</a>'),
            _response('<a id="download-link" href="https://cdn.example/exact.apk">Download</a>'),
        ]

        result = apkmirror.get_download_link(
            "2026.14.0", "reddit", self._config(), "arm64-v8a"
        )

        self.assertEqual(result, "https://cdn.example/exact.apk")
        self.assertEqual(mock_cf_get.call_args_list[0].args[0], "https://www.apkmirror.com/arm64-download/")




if __name__ == "__main__":
    unittest.main()
