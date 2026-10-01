import logging
import re
from pathlib import Path
from urllib.parse import urljoin

from src import session, utils


def _load_config(app_name: str) -> dict | None:
    path = Path("apps") / "archive" / f"{app_name}.json"
    if not path.exists():
        return None
    try:
        import json
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        logging.warning("Archive config could not be read for %s: %s", app_name, exc)
        return None


def get_download_link(version: str, app_name: str, config: dict | None = None) -> str | None:
    """
    Resolve a single exact-artifact APK from the configured Archive.org mirror.

    This provider is intentionally strict: it only serves the configured
    package/version/ABI artifact and never falls back to another release.
    """
    cfg = config or _load_config(app_name)
    if not cfg:
        return None

    expected_version = str(cfg.get("version") or "").strip()
    expected_arch = str(cfg.get("arch") or "").strip().lower()
    base_url = str(cfg.get("url") or "").rstrip("/")
    package = str(cfg.get("package") or "").strip()

    if not all((expected_version, expected_arch, base_url, package)):
        logging.warning("Archive exact-artifact config is incomplete for %s", app_name)
        return None

    if str(version or "").strip() != expected_version:
        logging.info(
            "Archive exact-artifact skipped for %s: requested %s, configured %s",
            app_name, version, expected_version,
        )
        return None

    try:
        response = session.get(base_url + "/", timeout=30)
        response.raise_for_status()
    except Exception as exc:
        logging.warning("Archive mirror unavailable for %s: %s", app_name, exc)
        return None

    html = response.text
    safe_version = re.escape(expected_version)
    safe_arch = re.escape(expected_arch)

    # Archive.org exposes the item directory as an HTML listing. Require a
    # standalone APK whose filename encodes the exact package/version/ABI.
    pattern = re.compile(
        rf'(?P<name>{re.escape(package)}-{safe_version}-{safe_arch}\.apk)',
        re.IGNORECASE,
    )
    match = pattern.search(html)
    if not match:
        logging.warning(
            "Archive exact-artifact not found for %s: package=%s version=%s arch=%s",
            app_name, package, expected_version, expected_arch,
        )
        return None

    filename = match.group("name")
    link = urljoin(base_url + "/", filename)
    logging.info("Archive exact-artifact selected for %s: %s", app_name, link)
    return link


def validate_exact_artifact(path: Path, app_name: str) -> tuple[bool, list[str]]:
    """Validate the downloaded Messenger mirror artifact before Morphe sees it."""
    cfg = _load_config(app_name)
    if not cfg:
        return False, ["archive exact-artifact config is missing"]

    target = {
        "version": cfg.get("version"),
        "version_codes": [int(cfg["version_code"])],
        "signatures": [str(cfg["signature_sha256"])],
        "apk_file_types": ["APK"],
    }

    return utils.validate_source_artifact(
        path,
        target,
        str(cfg.get("package") or ""),
        str(cfg.get("arch") or "arm64-v8a"),
        verify_signature=True,
        verify_sha256=False,
    )
