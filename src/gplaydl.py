import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from src import utils


def _find_package(app_name: str) -> str | None:
    """Find the configured Android package without adding a new app config."""
    for platform in ("aptoide", "apkmirror", "uptodown", "apkpure", "apkcombo"):
        path = Path("apps") / platform / f"{app_name}.json"
        if not path.exists():
            continue
        try:
            with path.open() as fh:
                package = json.load(fh).get("package")
            if package:
                return package
        except Exception:
            continue
    return None


def _arch_name(arch: str) -> str:
    return {
        "arm64-v8a": "arm64",
        "armeabi-v7a": "armv7",
        "x86_64": "x86_64",
        "x86": "x86",
    }.get(arch, "arm64")


def _version_codes(package_name: str, cli: str, patches: str) -> dict[str, list[int]]:
    """Read Morphe's supported version/versionCode pairs from the patch set."""
    cmd = [
        "java", "-jar", cli,
        "list-versions",
        "-f", package_name,
        "--patches", patches,
    ]
    output = utils.run_process(cmd, capture=True, silent=True, check=False) or ""
    result: dict[str, list[int]] = {}

    import re
    for line in output.splitlines():
        m = re.search(
            r"^\s*(\d+(?:\.\d+)+).*?versionCodes:\s*[^=]+=([0-9]+)",
            line,
        )
        if not m:
            continue
        result.setdefault(m.group(1), []).append(int(m.group(2)))
    return result


def download_app(
    app_name: str,
    cli: str,
    patches: str,
    arch: str = "arm64-v8a",
    override_version: str | None = None,
) -> tuple[Path | None, str | None]:
    """
    Download a Morphe-compatible APK directly from Google Play through gplaydl.

    gplaydl is intentionally optional: if GPLAYDL_API_KEY is not configured,
    or Google Play cannot serve the requested build, the caller should fall
    back to the existing public-store downloaders.
    """
    if not os.getenv("GPLAYDL_API_KEY"):
        logging.info("gplaydl skipped: GPLAYDL_API_KEY is not configured.")
        return None, None

    package_name = _find_package(app_name)
    if not package_name:
        logging.warning("gplaydl skipped: no package configured for %s.", app_name)
        return None, None

    supported = _version_codes(package_name, cli, patches)
    if not supported:
        logging.info(
            "gplaydl skipped for %s: Morphe did not expose usable versionCodes.",
            app_name,
        )
        return None, None

    if override_version:
        versions = [override_version]
    else:
        versions = utils.get_supported_versions(package_name, cli, patches)

    # Try Morphe-supported versions from highest to lowest. Google Play is
    # asked for the exact versionCode, avoiding Aptoide's nearest-version
    # behavior.
    candidates: list[tuple[str, int]] = []
    for version in versions:
        for code in supported.get(version, []):
            candidates.append((version, code))

    if not candidates:
        logging.info(
            "gplaydl skipped for %s: no versionCode matched Morphe's supported versions.",
            app_name,
        )
        return None, None

    play_arch = _arch_name(arch)

    for version, version_code in candidates:
        with tempfile.TemporaryDirectory(prefix=f"gplaydl-{app_name}-") as tmp:
            output_dir = Path(tmp)
            cmd = [
                "gplaydl",
                "download",
                package_name,
                "-a",
                play_arch,
                "-v",
                str(version_code),
                "--no-splits",
                "--no-extras",
                "-o",
                str(output_dir),
            ]
            logging.info(
                "Trying Google Play via gplaydl: %s %s (versionCode %s, arch %s)",
                package_name,
                version,
                version_code,
                play_arch,
            )

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except FileNotFoundError:
                logging.warning("gplaydl command is not installed.")
                return None, None

            if proc.returncode != 0:
                logging.warning(
                    "gplaydl failed for %s versionCode %s: %s",
                    app_name,
                    version_code,
                    (proc.stderr or proc.stdout).strip()[-1200:],
                )
                continue

            apks = sorted(output_dir.glob("*.apk"))
            if not apks:
                logging.warning(
                    "gplaydl returned success but no APK for %s versionCode %s.",
                    app_name,
                    version_code,
                )
                continue

            # With --no-splits gplaydl should produce one base APK. Copy it
            # outside the temporary directory because the patcher consumes it
            # after this function returns.
            source = apks[0]
            target = Path(source.name)
            target.write_bytes(source.read_bytes())

            logging.info(
                "Google Play download succeeded: %s -> %s",
                source.name,
                target,
            )
            return target, version

    logging.warning("gplaydl could not download a usable build for %s.", app_name)
    return None, None
