import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from src import session, utils


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

    for line in output.splitlines():
        m = re.search(
            r"^\s*(\d+(?:\.\d+)+).*?versionCodes:\s*[^=]+=([0-9]+)",
            line,
        )
        if not m:
            continue
        result.setdefault(m.group(1), []).append(int(m.group(2)))
    return result


def _download_apkeditor(output_dir: Path) -> Path | None:
    """Download the current APKEditor used to merge Google Play split APKs."""
    try:
        release = utils.detect_github_release("REAndroid", "APKEditor", "latest")
        for asset in release.get("assets", []):
            name = str(asset.get("name") or "")
            url = asset.get("browser_download_url")
            if not url or not name.lower().endswith(".jar"):
                continue
            if not name.lower().startswith("apkeditor"):
                continue

            target = output_dir / "APKEditor.jar"
            response = session.get(url, stream=True)
            response.raise_for_status()
            with target.open("wb") as fh:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        fh.write(chunk)
            if target.stat().st_size > 0:
                return target
    except Exception as exc:
        logging.warning("Could not download APKEditor for split merge: %s", exc)
    return None


def _merge_play_splits(apks: list[Path], work_dir: Path, package_name: str) -> Path | None:
    """
    Merge the Google Play base APK + compatible config splits into one
    installable APK.

    gplaydl normally returns a base APK plus the split APKs Google selected for
    the requested device/ABI. A base APK alone is NOT a complete App Bundle
    install: native libraries and density resources may live in config splits.
    APKEditor is specifically designed to merge these splits into one APK.
    """
    if not apks:
        return None

    base = next((p for p in apks if p.name.lower() == "base.apk"), None)
    if base is None:
        # gplaydl names the base APK like <package>-<versionCode>.apk,
        # while split APKs include a "-config.<qualifier>" suffix.
        base = next(
            (
                p for p in apks
                if "-config." not in p.stem.lower()
                and "asset" not in p.stem.lower()
            ),
            None,
        )
    if base is None:
        logging.warning("Google Play download has no base.apk for %s.", package_name)
        return None

    arm64_splits = [
        p for p in apks
        if "config.arm64_v8a" in p.name.lower()
    ]
    incompatible_abis = [
        p for p in apks
        if any(token in p.name.lower() for token in (
            "config.armeabi_v7a",
            "config.x86",
            "config.x86_64",
        ))
    ]

    # Google Play does not always deliver an explicit ABI split. Some apps
    # (including current Meta builds) put the arm64-v8a native libraries
    # directly in the base APK while only density/language resources are
    # delivered as config splits. In that case the base APK is already the
    # arm64 component and must NOT be rejected just because no
    # config.arm64_v8a file was returned.
    base_has_arm64 = False
    try:
        with zipfile.ZipFile(base) as archive:
            base_has_arm64 = any(
                name.startswith("lib/arm64-v8a/") and not name.endswith("/")
                for name in archive.namelist()
            )
    except (OSError, zipfile.BadZipFile) as exc:
        logging.warning("Could not inspect base APK ABI for %s: %s", package_name, exc)

    if not arm64_splits and not base_has_arm64:
        logging.warning(
            "Google Play returned neither an arm64-v8a split nor arm64 native "
            "libraries in the base APK for %s; refusing an incompatible build.",
            package_name,
        )
        return None

    if arm64_splits:
        logging.info(
            "Google Play selected explicit arm64-v8a split(s): %s",
            ", ".join(p.name for p in arm64_splits),
        )
    elif base_has_arm64:
        logging.info(
            "Google Play placed arm64-v8a native libraries in the base APK for %s; "
            "no separate ABI split is required.",
            package_name,
        )

    if incompatible_abis:
        logging.info(
            "Ignoring non-arm64 ABI splits from Google Play: %s",
            ", ".join(p.name for p in incompatible_abis),
        )
        for path in incompatible_abis:
            path.unlink(missing_ok=True)

    # gplaydl's device profile is responsible for the density selection. Keep
    # the density split(s) it returned rather than inventing a DPI value.
    density_splits = [
        p for p in apks
        if re.search(
            r"config\.(?:ldpi|mdpi|hdpi|xhdpi|xxhdpi|xxxhdpi|"
            r"\d{3,4}dpi|nodpi|anydpi)",
            p.name.lower(),
        )
    ]
    logging.info(
        "Google Play selected density/resource splits: %s",
        ", ".join(p.name for p in density_splits) or "(none; base resources only)",
    )
    logging.info(
        "Google Play selected arm64-v8a split(s): %s",
        ", ".join(p.name for p in arm64_splits),
    )

    apkeditor = _download_apkeditor(work_dir)
    if not apkeditor:
        return None

    merged = work_dir / f"{package_name}-merged.apk"
    cmd = [
        "java", "-jar", str(apkeditor),
        "m",
        "-i", str(work_dir),
        "-o", str(merged),
        "-f",
    ]

    try:
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        logging.warning(
            "APKEditor failed to merge Google Play splits: %s",
            (exc.stderr or exc.stdout or "").strip()[-2000:],
        )
        return None

    if not merged.exists() or merged.stat().st_size == 0:
        logging.warning("APKEditor produced no merged APK for %s.", package_name)
        return None

    # Enforce the repository's arm64-v8a-only policy on the merged artifact.
    # This is safe before Morphe patches/signs the APK.
    try:
        utils.strip_zip_entries(
            merged,
            [
                "lib/x86/*",
                "lib/x86_64/*",
                "lib/armeabi-v7a/*",
            ],
        )
        with zipfile.ZipFile(merged) as archive:
            names = archive.namelist()
            arm64_libs = [
                n for n in names
                if n.startswith("lib/arm64-v8a/") and not n.endswith("/")
            ]
            non_arm64_libs = [
                n for n in names
                if n.startswith(("lib/x86/", "lib/x86_64/", "lib/armeabi-v7a/"))
                and not n.endswith("/")
            ]
            if not arm64_libs:
                logging.warning(
                    "Merged Google Play APK has no lib/arm64-v8a native libraries; "
                    "refusing it as an arm64 build."
                )
                return None
            if non_arm64_libs:
                logging.warning(
                    "Merged APK still contains non-arm64 native libraries; refusing it."
                )
                return None
    except (OSError, zipfile.BadZipFile) as exc:
        logging.warning("Merged APK integrity/ABI inspection failed: %s", exc)
        return None

    final = Path(merged.name)
    shutil.copy2(merged, final)
    logging.info(
        "Google Play split set merged successfully into a single arm64-v8a APK: %s",
        final,
    )
    return final


def download_app(
    app_name: str,
    cli: str,
    patches: str,
    arch: str = "arm64-v8a",
    override_version: str | None = None,
) -> tuple[Path | None, str | None]:
    """
    Download a Morphe-compatible APK directly from Google Play through gplaydl.

    For App Bundle apps, gplaydl is asked for the complete split set, not just
    base.apk. The selected arm64-v8a + device-density splits are then merged
    into one installable APK before Morphe sees it.

    gplaydl is intentionally optional: if GPLAYDL_API_KEY is not configured,
    or Google Play cannot serve the requested build, the caller falls back to
    the existing public-store downloaders.
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
    # asked for the exact versionCode, avoiding nearest-version behavior.
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
    if arch != "arm64-v8a":
        logging.warning(
            "gplaydl currently expects the repository's arm64-v8a build policy; "
            "requested arch=%s will be passed through as %s.",
            arch,
            play_arch,
        )

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
                "--no-extras",
                "-o",
                str(output_dir),
            ]

            logging.info(
                "Trying Google Play via gplaydl: %s %s (versionCode %s, arch %s). "
                "Requesting base + splits for complete installability.",
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

            logging.info(
                "Google Play returned %d APK components for %s: %s",
                len(apks),
                app_name,
                ", ".join(p.name for p in apks),
            )

            # Always merge the Play split set. This avoids the previous
            # base-only bug that produced an APK which could fail installation
            # because its ABI/density resources were still in config splits.
            merged = _merge_play_splits(apks, output_dir, package_name)
            if merged is None:
                logging.warning(
                    "Refusing Google Play artifact for %s because the complete "
                    "arm64/density-compatible split set could not be produced.",
                    app_name,
                )
                continue

            target = Path(f"{app_name}-{version_code}-gplaydl.apk")
            shutil.copy2(merged, target)

            logging.info(
                "Google Play download succeeded as a single installable APK: %s -> %s",
                merged.name,
                target,
            )
            return target, version

    logging.warning("gplaydl could not download a usable build for %s.", app_name)
    return None, None
