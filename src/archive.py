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


def _normalize_arch(arch: str | None) -> str:
    """Normalize common Android ABI labels used by manifests and callers."""
    value = str(arch or "").strip().lower()
    aliases = {
        "arm64": "arm64-v8a",
        "aarch64": "arm64-v8a",
        "arm64_v8a": "arm64-v8a",
        "arm64-v8a": "arm64-v8a",
        "arm-v7a": "armeabi-v7a",
        "armv7": "armeabi-v7a",
        "armeabi": "armeabi-v7a",
        "armeabi-v7a": "armeabi-v7a",
        "all": "universal",
        "universal": "universal",
        "noarch": "universal",
    }
    return aliases.get(value, value)


def _artifact_config(cfg: dict, version: str, arch: str | None = None) -> dict | None:
    """Select one exact artifact declaration using safe architecture priorities.

    A universal build target means "must run on the repository's target
    device ABI", not "accept any ABI". Prefer an explicit arm64-v8a artifact
    for universal requests, then a true universal/noarch/all artifact. Never
    use a pure armeabi-v7a artifact as a universal fallback.

    For a concrete ABI request, prefer that exact ABI and then allow a true
    universal/noarch/all artifact.
    """
    requested_version = str(version or "").strip()
    requested_arch = _normalize_arch(arch)

    def pick_artifact(artifacts: list[dict]) -> dict | None:
        exact = None
        universal = None
        requested = None

        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            if str(artifact.get("version") or "").strip() != requested_version:
                continue

            artifact_arch_raw = str(artifact.get("arch") or "").strip()
            artifact_arch = _normalize_arch(artifact_arch_raw)

            if not requested_arch:
                return artifact
            if artifact_arch == requested_arch:
                requested = requested or artifact
            elif artifact_arch == "universal":
                universal = universal or artifact

            if requested_arch == "universal" and artifact_arch == "arm64-v8a":
                exact = exact or artifact

        if requested_arch == "universal":
            # Universal target: arm64 first, then a genuinely universal
            # artifact. ARMv7-only artifacts are deliberately excluded.
            return exact or universal
        return requested or universal

    artifacts = cfg.get("artifacts")
    if isinstance(artifacts, list):
        return pick_artifact(artifacts)

    # Backward-compatible single-artifact manifest.
    if str(cfg.get("version") or "").strip() != requested_version:
        return None

    cfg_arch = _normalize_arch(cfg.get("arch"))
    if not requested_arch or not cfg_arch:
        return cfg
    if requested_arch == "universal":
        return cfg if cfg_arch in {"arm64-v8a", "universal"} else None
    return cfg if cfg_arch in {requested_arch, "universal"} else None


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

    if _normalize_arch(arch) == "universal" and _normalize_arch(expected_arch) == "arm64-v8a":
        logging.info(
            "Archive: universal target resolved to exact arm64-v8a artifact for %s %s",
            app_name, expected_version,
        )
    direct_url = str(artifact.get("file_url") or "").strip()
    if not direct_url and str(artifact.get("url") or "").lower().split("?")[0].endswith(".apk"):
        direct_url = str(artifact.get("url")).strip()
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
    version: str | None = None,
) -> tuple[bool, list[str]]:
    """Validate an Archive artifact against its exact manifest contract."""
    cfg = config or _load_config(app_name)
    if not cfg:
        return False, ["archive exact-artifact config is missing"]

    artifact = _artifact_config(cfg, str(version or cfg.get("version") or ""), arch)
    if not artifact:
        return False, ["archive exact-artifact manifest entry is missing"]

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
