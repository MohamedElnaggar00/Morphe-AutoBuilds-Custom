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
            r"^\s*(\d+(?:\.\d+)+).*?versionCodes:\s*(.*?)(?:\s*\([^)]*\))?\s*$",
            line,
        )
        if not m:
            continue

        version = m.group(1)
        code_text = m.group(2)

        # Morphe prints architecture-specific version codes, e.g.
        # ARMEABI_V7A=345212666, ARM64_V8A=345212670.
        # Keep the mapping by ABI. The old implementation flattened all
        # architecture codes into one list, which made an arm64 build try
        # the ARMv7 versionCode first.
        arch_codes: dict[str, int] = {}
        for match in re.finditer(
            r"(ARMEABI_V7A|ARM64_V8A|X86_64|X86)\s*=\s*(\d+)",
            code_text,
        ):
            arch_codes[match.group(1)] = int(match.group(2))

        if arch_codes:
            # Store the architecture mapping as an ordered list with ARM64
            # first. download_app() filters this list to the requested ABI.
            ordered = [
                arch_codes[name]
                for name in ("ARM64_V8A", "ARMEABI_V7A", "X86_64", "X86")
                if name in arch_codes
            ]
            result.setdefault(version, []).extend(ordered)
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



def _apk_version_name(apk: Path) -> str | None:
    """Read versionName from a merged APK without relying on patch metadata."""
    try:
        android_home = os.environ.get("ANDROID_HOME", "")
        aapt2_candidates = (
            sorted(Path(android_home).glob("build-tools/*/aapt2"), reverse=True)
            if android_home else []
        )
        if not aapt2_candidates:
            return None
        out = subprocess.run(
            [str(aapt2_candidates[0]), "dump", "badging", str(apk)],
            capture_output=True, text=True, check=False,
        ).stdout
        match = re.search(r"versionName='([^']+)'", out)
        return match.group(1) if match else None
    except Exception as exc:
        logging.debug("Could not read APK versionName from %s: %s", apk, exc)
        return None


def _download_with_compat_profiles(
    package_name: str,
    target_version: str,
    version_code: int,
    arch: str,
    output_dir: Path,
) -> list[Path]:
    """
    Use gplaydl's own Google Play API/profile machinery, but try every
    compatible device profile instead of stopping after its limited retry set.

    This specifically handles old/pinned versionCodes that Google refuses for
    a modern default profile such as Pixel 9a, while keeping the requested ABI.
    """
    try:
        from gplaydl.api import (
            AppNotAvailableError,
            AppNotSupportedError,
            AuthExpiredError,
            PlayAPIError,
            get_delivery,
            get_details,
            purchase,
        )
        from gplaydl.auth import fetch_token_for_profile
        from gplaydl.download import DownloadSpec, download_batch
        from gplaydl.profiles import get_compat_profiles
    except Exception as exc:
        logging.warning("Could not import gplaydl compatibility API: %s", exc)
        return []

    play_arch = _arch_name(arch)
    profiles = get_compat_profiles(play_arch)

    if not profiles:
        logging.warning(
            "gplaydl exposed no compatibility profiles for %s.",
            play_arch,
        )
        return []

    logging.info(
        "gplaydl compatibility fallback: trying %d profiles for %s versionCode %s.",
        len(profiles),
        play_arch,
        version_code,
    )

    for profile_name, profile in profiles:
        device = profile.get("UserReadableName", profile_name)
        try:
            logging.info(
                "gplaydl compatibility fallback: requesting token with profile %s.",
                device,
            )
            auth = fetch_token_for_profile(profile)
            if not auth:
                logging.info(
                    "gplaydl compatibility fallback: profile %s did not yield a token.",
                    device,
                )
                continue

            # Google Play can expose the same version string under a
            # profile-specific versionCode. Morphe's patch metadata gives us
            # the ABI-specific code, but that exact code is not necessarily
            # the code accepted by every Play profile.
            details = get_details(package_name, auth)
            profile_version = details.version_string
            profile_version_code = details.version_code
            if profile_version != target_version or not profile_version_code:
                logging.info(
                    "gplaydl compatibility fallback: profile %s serves %s "
                    "(versionCode %s), not requested %s; skipping profile.",
                    device,
                    profile_version or "(unknown)",
                    profile_version_code or "(unknown)",
                    target_version,
                )
                continue

            if profile_version_code != version_code:
                logging.info(
                    "gplaydl compatibility fallback: profile %s exposes the "
                    "requested %s as versionCode %s (instead of %s); using the "
                    "profile-specific code.",
                    device,
                    target_version,
                    profile_version_code,
                    version_code,
                )

            effective_version_code = profile_version_code
            delivery_token = purchase(package_name, effective_version_code, auth)
            delivery = get_delivery(
                package_name,
                effective_version_code,
                auth,
                delivery_token,
            )

            specs: list[DownloadSpec] = []
            base_name = f"{package_name}-{effective_version_code}.apk"
            use_gzip = bool(delivery.gzipped_url and delivery.gzipped_size)
            specs.append(
                DownloadSpec(
                    url=delivery.gzipped_url if use_gzip else delivery.download_url,
                    dest=output_dir / base_name,
                    cookies=delivery.cookies,
                    label=base_name,
                    gzipped=use_gzip,
                    sha256=delivery.sha256,
                    sha1=delivery.sha1,
                )
            )

            for split in delivery.splits:
                split_name = f"{package_name}-{effective_version_code}-{split.name}.apk"
                use_gzip = bool(split.gzipped_url and split.gzipped_size)
                specs.append(
                    DownloadSpec(
                        url=split.gzipped_url if use_gzip else split.url,
                        dest=output_dir / split_name,
                        label=split_name,
                        gzipped=use_gzip,
                        sha256=split.sha256,
                    )
                )

            download_batch(specs)

            apks = sorted(output_dir.glob("*.apk"))
            if apks:
                logging.info(
                    "gplaydl compatibility fallback succeeded with profile %s: %s",
                    device,
                    ", ".join(p.name for p in apks),
                )
                return apks

        except AuthExpiredError:
            logging.info(
                "gplaydl compatibility fallback: token expired for profile %s.",
                device,
            )
        except (AppNotSupportedError, AppNotAvailableError) as exc:
            logging.info(
                "gplaydl compatibility fallback: profile %s cannot receive "
                "versionCode %s: %s",
                device,
                version_code,
                exc,
            )
        except PlayAPIError as exc:
            logging.info(
                "gplaydl compatibility fallback: Google Play rejected profile "
                "%s for versionCode %s: %s",
                device,
                version_code,
                exc,
            )
        except Exception as exc:
            logging.warning(
                "gplaydl compatibility fallback failed with profile %s: %s",
                device,
                exc,
            )

        # Do not let a partially downloaded/failed attempt poison the next profile.
        for path in output_dir.glob("*"):
            if path.is_file():
                path.unlink(missing_ok=True)

    return []

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
    source_codes = utils.get_source_supported_version_codes(
        package_name,
        os.getenv("SOURCE", ""),
    )

    if override_version:
        versions = [override_version]
    elif source_codes:
        # The patch source is authoritative: only builds explicitly listed by
        # the patch developer are eligible. Do not substitute a newer/nearest
        # Google Play version when those builds are unavailable.
        versions = list(source_codes)
        logging.info(
            "gplaydl: patch source declares supported builds: %s",
            ", ".join(
                f"{version} (versionCodes: {', '.join(map(str, codes))})"
                for version, codes in source_codes.items()
            ),
        )
    else:
        if not supported:
            logging.info(
                "gplaydl skipped for %s: no supported version/build metadata found.",
                app_name,
            )
            return None, None
        versions = list(supported)

    # Try only the exact versionCode/build combinations declared by the patch
    # source. Never infer a nearby or latest build when the declared builds
    # cannot be downloaded.
    candidates: list[tuple[str, int]] = []
    # Morphe can expose multiple ABI-specific versionCodes for the same
    # version. Never try another ABI's code for an arm64 build.
    requested_code_index = {
        "arm64-v8a": 0,
        "armeabi-v7a": 1,
        "x86_64": 2,
        "x86": 3,
    }.get(arch, 0)

    for version in versions:
        codes = source_codes.get(version, []) if source_codes else supported.get(version, [])
        if source_codes and not override_version:
            if codes and arch == "arm64-v8a":
                # Every published arm64 build is a valid candidate. We try the
                # exact codes from the patch developer, not a guessed/nearest
                # Google Play release.
                for code in codes:
                    candidates.append((version, code))
            elif not codes:
                # Some sources publish the version but omit build codes.
                # Preserve the existing Morphe CLI code mapping in that case.
                fallback_codes = supported.get(version, [])
                if len(fallback_codes) > requested_code_index:
                    candidates.append((version, fallback_codes[requested_code_index]))
            continue

        if len(codes) > requested_code_index:
            candidates.append((version, codes[requested_code_index]))

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

            apks: list[Path] = []
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
                error_text = (proc.stderr or proc.stdout).strip()[-1200:]
                logging.warning(
                    "gplaydl failed for %s versionCode %s: %s",
                    app_name,
                    version_code,
                    error_text,
                )

                # gplaydl's CLI has a finite compatibility-profile retry budget.
                # For pinned Morphe versions, Google may reject the modern
                # default profile while still serving the exact same versionCode
                # to an older compatible profile. Re-enter gplaydl through its
                # Python API and try the complete compatibility-profile pool.
                if "does not serve version" in error_text.lower():
                    compat_apks = _download_with_compat_profiles(
                        package_name,
                        version,
                        version_code,
                        arch,
                        output_dir,
                    )
                    if compat_apks:
                        apks = compat_apks
                    else:
                        continue
                else:
                    continue

            if not apks:
                apks = sorted(output_dir.glob("*.apk"))
            if not apks:
                logging.warning(
                    "gplaydl returned success but no APK for %s versionCode %s.",
                    app_name,
                    version_code,
                )

                compat_apks = _download_with_compat_profiles(
                    package_name,
                    version,
                    version_code,
                    arch,
                    output_dir,
                )
                if not compat_apks:
                    continue
                apks = compat_apks

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

            # The merged APK is only an intermediate input for Morphe.
            # Keep the named gplaydl input and remove the intermediate so it
            # cannot be mistaken for a second build artifact/release asset.
            merged.unlink(missing_ok=True)

            logging.info(
                "Google Play download succeeded as a single installable APK: %s -> %s",
                merged.name,
                target,
            )
            return target, version

    logging.warning("gplaydl could not download a usable build for %s.", app_name)
    return None, None
