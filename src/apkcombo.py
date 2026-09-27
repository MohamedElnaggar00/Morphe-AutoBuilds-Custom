"""APKCombo downloader for exact-version APK/APK bundle retrieval.

APKCombo is the deterministic fallback when APKMirror is blocked by Cloudflare.
The resolver follows APKCombo's public package URL -> exact-version download page
-> /r2 signed-object flow, while selecting the requested ABI/DPI from the
variant row instead of relying on whichever link happens to appear first.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests as plain_requests
from bs4 import BeautifulSoup

from src import session, utils

BASE_URL = "https://apkcombo.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _slug_candidates(config: dict) -> list[str]:
    """Return deterministic APKCombo app slugs, most specific first."""
    package = str(config.get("package") or "").strip().lower()
    name = str(config.get("name") or "").strip().lower()
    explicit = str(
        config.get("apkcombo_slug")
        or config.get("apkcombo_name")
        or config.get("slug")
        or config.get("app_slug")
        or ""
    ).strip().lower()

    out: list[str] = []

    def add(value: str) -> None:
        value = (value or "").strip().strip("/")
        if value and value not in out:
            out.append(value)

    add(explicit)
    if package:
        # APKCombo's public slugs for the three problematic packages.
        known = {
            "com.facebook.katana": "facebook",
            "com.facebook.orca": "facebook-messenger",
            "com.adobe.reader": "adobe-reader",
        }
        add(known.get(package, ""))

    add(name)
    if name:
        add(name.replace("-plus", ""))
        add(name.replace("-", ""))
        add(name.replace("_", "-"))

    return out


def _app_urls(config: dict) -> list[str]:
    package = str(config.get("package") or "").strip()
    if not package:
        return []
    return [
        f"{BASE_URL}/{slug}/{package}"
        for slug in _slug_candidates(config)
    ]


def _get(url: str):
    """Fetch a public APKCombo page with the shared curl-cffi session."""
    kwargs = {"headers": HEADERS, "timeout": 30}
    try:
        response = session.get(url, **kwargs)
        if response.status_code == 200:
            return response
        logging.debug("APKCombo curl-cffi %s -> HTTP %s", url, response.status_code)
    except Exception as exc:
        logging.debug("APKCombo curl-cffi failed for %s: %s", url, exc)

    try:
        response = plain_requests.get(url, **kwargs)
        logging.debug("APKCombo requests %s -> HTTP %s", url, response.status_code)
        return response
    except Exception as exc:
        logging.debug("APKCombo requests failed for %s: %s", url, exc)
        return None


def _same_version(left: str, right: str) -> bool:
    left = str(left or "").strip()
    right = str(right or "").strip()
    return (
        left == right
        or utils.normalize_version(left) == utils.normalize_version(right)
    )


def _version_links(soup: BeautifulSoup):
    for anchor in soup.select('a[href*="/download/phone-"]'):
        href = anchor.get("href") or ""
        match = re.search(r"/download/phone-([0-9A-Za-z][0-9A-Za-z.\-_]*)-(?:apk|xapk|apks)(?:[/?#]|$)", href)
        if match:
            yield match.group(1), urljoin(BASE_URL + "/", href)


def get_latest_version(app_name: str, config: dict) -> str | None:
    for base_url in _app_urls(config):
        response = _get(base_url.rstrip("/") + "/old-versions/")
        if not response or response.status_code != 200:
            continue
        soup = BeautifulSoup(response.content, "html.parser")
        for version, _ in _version_links(soup):
            if version and not any(x in version.lower() for x in ("alpha", "beta", "canary", "nightly")):
                logging.info("APKCombo latest version for %s: %s", app_name, version)
                return version

        text = soup.get_text(" ", strip=True)
        versions = re.findall(r"\b\d+(?:\.\d+){1,5}\b", text)
        if versions:
            return utils.get_highest_version(versions)
    return None


def _r2_target(href: str) -> str | None:
    """Decode APKCombo's /r2?u=<signed-object-url> wrapper."""
    try:
        parsed = urlparse(urljoin(BASE_URL, href))
        target = parse_qs(parsed.query).get("u", [""])[0]
        if not target:
            return None
        # The nested URL is normally one layer encoded; tolerate a second
        # encoding layer without modifying the signed query parameters.
        for _ in range(2):
            decoded = unquote(target)
            if decoded == target:
                break
            target = decoded
        if target.startswith(("http://", "https://")):
            return target
    except Exception:
        pass
    return None


def _artifact_kind(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    disposition = " ".join(query.get("response-content-disposition", [])).lower()
    content_type = " ".join(query.get("response-content-type", [])).lower()
    path = parsed.path.lower()
    probe = " ".join((path, disposition, content_type))
    if any(ext in probe for ext in (".xapk", "xapk-package", ".apkm", ".apks")):
        return "bundle"
    return "apk"


def _variant_context(anchor) -> str:
    parts = []
    node = anchor
    for _ in range(4):
        if not node:
            break
        try:
            parts.append(node.get_text(" ", strip=True).lower())
        except Exception:
            pass
        node = node.parent
    return " ".join(parts)


def _arch_matches(context: str, target_arch: str) -> bool:
    target = str(target_arch or "universal").lower()
    if target in ("", "universal", "noarch"):
        return True
    if target == "arm64-v8a":
        return "arm64-v8a" in context or "arm64" in context
    if target == "armeabi-v7a":
        return "armeabi-v7a" in context or "armv7" in context or "arm v7" in context
    return target in context


def _dpi_matches(context: str, wanted_dpi: str) -> bool:
    wanted = str(wanted_dpi or "nodpi").lower().strip()
    if wanted in ("", "nodpi", "all", "auto", "120-640dpi"):
        return True

    range_match = re.fullmatch(r"(\d+)\s*-\s*(\d+)dpi", wanted)
    if range_match:
        low, high = map(int, range_match.groups())
        for lo, hi in re.findall(r"(\d+)\s*-\s*(\d+)dpi", context):
            if int(lo) >= low and int(hi) <= high:
                return True
        for value in re.findall(r"(?<!\d)(\d+)dpi", context):
            if low <= int(value) <= high:
                return True
        return False

    if wanted in context:
        return True
    return wanted.endswith("dpi") and wanted[:-3] in context


def _pick_signed_download(soup: BeautifulSoup, config: dict, target_arch: str) -> str | None:
    wanted_type = str(config.get("type") or "APK").lower()
    wanted_dpi = str(config.get("dpi") or "nodpi").lower()

    candidates = []
    for anchor in soup.select('a[href^="/r2?u="]'):
        target = _r2_target(anchor.get("href") or "")
        if not target:
            continue
        context = _variant_context(anchor)
        if not _arch_matches(context, target_arch):
            continue
        if not _dpi_matches(context, wanted_dpi):
            continue

        kind = _artifact_kind(target)
        type_score = 0
        if wanted_type == "bundle":
            if kind != "bundle":
                continue
            type_score = 20
        else:
            # Prefer a real APK for APK-configured apps, but accept an XAPK/APKS
            # when APKCombo has no standalone artifact for this exact version.
            type_score = 20 if kind == "apk" else 5

        arch_score = 0
        context_lower = context
        if target_arch == "arm64-v8a" and "arm64-v8a" in context_lower:
            arch_score = 10
        elif target_arch == "armeabi-v7a" and "armeabi-v7a" in context_lower:
            arch_score = 10

        dpi_score = 0
        if wanted_dpi == "nodpi" and "nodpi" in context_lower:
            dpi_score = 5
        elif wanted_dpi not in ("", "all", "auto") and wanted_dpi in context_lower:
            dpi_score = 5

        candidates.append((type_score + arch_score + dpi_score, target, context))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    score, target, context = candidates[0]
    logging.info("✓ APKCombo selected signed artifact (score=%s): %s | %s", score, target, context[:180])
    return target


def _exact_download_page(base_url: str, version: str) -> str:
    return f"{base_url.rstrip('/')}/download/phone-{version}-apk"


def get_download_link(version: str, app_name: str, config: dict, arch: str = None) -> str | None:
    version = str(version or "").strip()
    if not version:
        return None

    target_arch = arch if (arch and arch != "universal") else config.get("arch", "universal")
    for base_url in _app_urls(config):
        # APKCombo intentionally exposes the exact-version page with the
        # "-apk" suffix even when the returned artifact is XAPK.
        urls = [
            _exact_download_page(base_url, version),
            f"{base_url.rstrip('/')}/download/phone-{version}-xapk",
            f"{base_url.rstrip('/')}/download/phone-{version}-apks",
        ]
        tried = set()

        for page_url in urls:
            if page_url in tried:
                continue
            tried.add(page_url)

            response = _get(page_url)
            if not response or response.status_code != 200:
                continue

            soup = BeautifulSoup(response.content, "html.parser")
            page_text = soup.get_text(" ", strip=True)
            if version not in page_text and version.replace("-", ".") not in page_text:
                logging.debug("APKCombo page did not validate version %s: %s", version, response.url)
                continue

            link = _pick_signed_download(soup, config, target_arch)
            if link:
                return link

        # Do not guess an alternate version. If the exact version is absent
        # from APKCombo, the caller's other source/version logic remains in control.
        logging.info("APKCombo has no exact downloadable artifact for %s %s", app_name, version)

    return None
