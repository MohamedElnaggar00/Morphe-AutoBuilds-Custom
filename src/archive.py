import json
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
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        logging.warning("Archive config could not be read for %s: %s", app_name, exc)
        return None


def _artifact_config(cfg: dict, version: str, arch: str | None = None) -> dict | None:
    """Select one exact artifact declaration from an app's Archive manifest."""
    requested_version = str(version or "").strip()
    requested_arch = str(arch or "").strip().lower()

    artifacts = cfg.get("artifacts")
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            if str(artifact.get("version") or "").strip() != requested_version:
                continue
            artifact_arch = str(artifact.get("arch") or "").strip().lower()
            if requested_arch and artifact_arch and artifact_arch != requested_arch:
                continue
            return artifact

    # Backward-compatible single-artifact manifest.
    if str(cfg.get("version") or "").strip() != requested_version:
        return None
    cfg_arch = str(cfg.get("arch") or "").strip().lower()
    if requested_arch and cfg_arch and cfg_arch != requested_arch:
        return None
    return cfg


def get_download_link(
    version: str,
    app_name: str,
    config: dict | None = None,
    arch: str | None = None,
) -> str | None:
    """
    Resolve one exact artifact from the configured Archive.org mirror.

    Archive.org is treated as a transport host, not proof of trust. The
    manifest identifies the exact artifact and the downloaded file is
    validated separately before Morphe receives it.
    """
    cfg = config or _load_config(app_name)
    if not cfg:
        return None

    artifact = _artifact_config(cfg, version, arch)
    if not artifact:
        logging.info(
            "Archive exact-artifact skipped for %s: no manifest entry for version=%s arch=%s",
            app_name, version, arch or "any",
        )
        return None

    expected_version = str(artifact.get("version") or "").strip()
    expected_arch = str(artifact.get("arch") or "").strip().lower()
    package = str(artifact.get("package") or cfg.get("package") or "").strip()
    direct_url = str(artifact.get("url") or "").strip()
    base_url = str(artifact.get("base_url") or cfg.get("url") or "").rstrip("/")
    filename = str(artifact.get("filename") or "").strip()

    if not expected_version or not package:
        logging.warning("Archive exact-artifact config is incomplete for %s", app_name)
        return None

    if direct_url:
        return direct_url

    if not base_url:
        logging.warning("Archive exact-artifact config has no URL for %s", app_name)
        return None

    if filename:
        return urljoin(base_url + "/", filename)

    try:
        response = session.get(base_url + "/", timeout=30)
        response.raise_for_status()
    except Exception as exc:
        logging.warning("Archive mirror unavailable for %s: %s", app_name, exc)
        return None

    arch_part = rf"-{re.escape(expected_arch)}" if expected_arch else ""
    pattern = re.compile(
        rf'(?P<name>{re.escape(package)}-{re.escape(expected_version)}{arch_part}\.apk)',
        re.IGNORECASE,
    )
    match = pattern.search(response.text)
    if not match:
        logging.warning(
            "Archive exact-artifact not found for %s: package=%s version=%s arch=%s",
            app_name, package, expected_version, expected_arch or "any",
        )
        return None

    return urljoin(base_url + "/", match.group("name"))


def validate_exact_artifact(
    path: Path,
    app_name: str,
    arch: str | None = None,
    config: dict | None = None,
) -> tuple[bool, list[str]]:
    """Validate an Archive artifact against its exact manifest contract."""
    cfg = config or _load_config(app_name)
    if not cfg:
        return False, ["archive exact-artifact config is missing"]

    artifacts = cfg.get("artifacts")
    if isinstance(artifacts, list):
        if len(artifacts) != 1:
            return False, [
                "archive manifest has multiple artifacts; downloader must pass an exact artifact entry"
            ]
        artifact = artifacts[0]
    else:
        artifact = cfg

    package = str(artifact.get("package") or cfg.get("package") or "").strip()
    version = str(artifact.get("version") or "").strip()
    version_code = artifact.get("version_code")
    if not package or not version or version_code is None:
        return False, ["archive exact-artifact manifest lacks package/version/versionCode"]

    target = {
        "version": version,
        "version_codes": [int(version_code)],
        "apk_file_types": [
            str(artifact.get("artifact_type") or cfg.get("artifact_type") or "APK")
        ],
    }

    signature = artifact.get("signature_sha256") or artifact.get("signature")
    if signature:
        target["signatures"] = [str(signature)]

    sha256 = artifact.get("sha256")
    if sha256:
        target["sha256"] = [str(sha256)]

    return utils.validate_source_artifact(
        path,
        target,
        package,
        str(artifact.get("arch") or cfg.get("arch") or arch or "universal"),
        verify_signature=bool(target.get("signatures")),
        verify_sha256=bool(target.get("sha256")),
    )
