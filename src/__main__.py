import json
import logging
import re
import os
import shutil
from sys import exit
from pathlib import Path
from os import getenv
import subprocess
from src import (
    r2,
    utils,
    release,
    downloader
)

def _should_retry_with_older_version(output: str | None) -> bool:
    """Detect common patterns that indicate the chosen app version is not
    actually compatible with the selected patches (fingerprint mismatch, etc.)."""
    if not output:
        return False
    t = output.lower()
    return (
        "failed to match the fingerprint" in t
        or "patch.patchexception" in t
        or ("fingerprint" in t and "failed" in t)
        or "patching aborted" in t
    )

def _configured_package(app_name: str) -> str | None:
    """Resolve the Android package from the existing app configuration."""
    for platform in ("aptoide", "apkmirror", "uptodown", "apkpure", "apkcombo"):
        path = Path("apps") / platform / f"{app_name}.json"
        if not path.exists():
            continue
        try:
            with path.open() as cfg:
                package = json.load(cfg).get("package")
            if package:
                return package
        except Exception:
            continue
    return None


def _patch_source_version(source: str) -> str:
    """Return the exact published patch-source version used by this build."""
    source_path = Path("sources") / f"{source}.json"
    if not source_path.exists():
        return ""
    try:
        with source_path.open(encoding="utf-8") as fh:
            entries = json.load(fh)
        patch_entry = next(
            (
                entry for entry in entries[1:]
                if isinstance(entry, dict)
                and entry.get("repo")
                and str(entry.get("repo")).lower() != "morphe-cli"
            ),
            None,
        )
        if not patch_entry:
            return ""
        release = utils.detect_release(patch_entry)
        return str(release.get("tag_name") or "").lstrip("v")
    except Exception as exc:
        logging.warning("Could not resolve patch version for %s: %s", source, exc)
        return ""


def _release_already_has_build(app_name: str, arch: str, patch_version: str, version: str) -> bool:
    """
    Avoid rebuilding an APK that is already published for the exact app,
    architecture, and Morphe-supported version.

    Every build run publishes a NEW release containing only the APKs it
    rebuilt, so an unchanged app lives in an older release. GitHub Actions
    starts from a clean checkout, so local output files cannot be used for
    this decision: check the assets of ALL published releases instead.
    If GitHub metadata is unavailable, fail open and let the normal build run.
    """
    token = getenv("GITHUB_TOKEN") or getenv("GH_TOKEN")
    repo = getenv("GITHUB_REPOSITORY")
    if not token or not repo:
        return False

    import urllib.request

    expected_prefix = f"{app_name}-{arch}-"
    expected_patch_marker = f"-patch-v{patch_version}-"
    expected_suffix = f"-app-v{version}.apk" if patch_version else f"-v{version}.apk"

    max_pages = 10  # 100 releases per page
    for page in range(1, max_pages + 1):
        url = f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Morphe-AutoBuilds-Custom",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                releases = json.load(response)
        except Exception as exc:
            logging.info("Could not inspect GitHub Releases for skip check: %s", exc)
            return False

        if not releases:
            break

        for release in releases:
            if release.get("draft"):
                continue
            for asset in release.get("assets", []):
                name = str(asset.get("name") or "")
                if (
                    name.startswith(expected_prefix)
                    and name.endswith(expected_suffix)
                    and (not patch_version or expected_patch_marker in name)
                ):
                    logging.info(
                        "⏭️ %s %s is already published in release %s as %s; skipping rebuild.",
                        app_name,
                        arch,
                        release.get("tag_name"),
                        name,
                    )
                    Path(".build-skipped").touch()
                    return True

        if len(releases) < 100:
            break

    return False


def run_build(app_name: str, source: str, arch: str = "universal") -> str:
    """Build APK for specific architecture"""
    download_files, name = downloader.download_required(source)

    # Log downloaded files for debugging
    logging.info(f"📦 Downloaded {len(download_files)} files for {source}:")
    for file in download_files:
        logging.info(f"  - {file.name} ({file.stat().st_size} bytes)")

    # DETECT SOURCE TYPE BASED ON DOWNLOADED FILES
    is_morphe = False
    is_revanced = False

    # Check file contents to determine source type
    for file in download_files:
        if "morphe-cli" in file.name.lower():
            is_morphe = True
            break
        elif "revanced-cli" in file.name.lower():
            is_revanced = True
            break

    # If not detected by CLI name, check patch file extension
    if not is_morphe and not is_revanced:
        for file in download_files:
            if file.suffix == ".mpp":
                is_morphe = True
                break
            elif file.suffix in [".rvp", ".jar"] and "patches" in file.name.lower():
                is_revanced = True
                break

    # If still not detected, fallback to source name
    if not is_morphe and not is_revanced:
        is_morphe = "morphe" in source.lower() or "custom" in source.lower()
        is_revanced = not is_morphe  # Default to ReVanced if not Morphe

    logging.info(f"🔍 Detected: {'Morphe' if is_morphe else 'ReVanced'} source type")

    # FIND FILES BASED ON DETECTED TYPE
    if is_morphe:
        # Find Morphe files - prefer non-dev version
        cli = utils.find_file(download_files, contains="morphe-cli", suffix=".jar", exclude=["dev"])
        if not cli:
            # Fallback to any Morphe CLI
            cli = utils.find_file(download_files, contains="morphe", suffix=".jar")
        
        if not cli:
            cli = utils.find_file(download_files, suffix=".jar")
        patches = utils.find_file(download_files, contains="patches", suffix=".mpp")
        if not patches:
            # Fallback to any .mpp file
            patches = utils.find_file(download_files, suffix=".mpp")
    else:
        # Find ReVanced files
        cli = utils.find_file(download_files, contains="revanced-cli", suffix=".jar")
        patches = utils.find_file(download_files, contains="patches", suffix=".rvp")
        
        if not patches:
            # Try .jar extension for patches
            patches = utils.find_file(download_files, contains="patches", suffix=".jar")

    # Validate tools
    if not cli:
        logging.error(f"❌ CLI not found for source: {source}")
        logging.error(f"Available files: {[f.name for f in download_files]}")
        return None
    if not patches:
        logging.error(f"❌ Patches not found for source: {source}")
        logging.error(f"Available files: {[f.name for f in download_files]}")
        return None

    logging.info(f"✅ Using CLI: {cli.name}")
    logging.info(f"✅ Using patches: {patches.name}")

    # Resolve the exact automatic target policy before the APK download.
    # Stable targets are preferred over experimental ones by the shared
    # source-target resolver, so the skip check and downloader use the same
    # app-version decision.
    package_name = _configured_package(app_name) or app_name
    source_targets = utils.get_source_supported_targets(package_name, source)
    supported_versions = (
        [target["version"] for target in source_targets]
        if source_targets
        else utils.get_supported_versions(package_name, str(cli), str(patches))
    )

    patch_version = _patch_source_version(source)

    if supported_versions:
        latest_supported = supported_versions[0]
        if _release_already_has_build(app_name, arch, patch_version, latest_supported):
            print(f"⏭️ Skipping {app_name}: {latest_supported} is already built and published.")
            return None

    # Bundle patch sets are tied to the exact split bundle they were
    # checked against. For these apps the authoritative source is APKMirror's
    # native APKM bundle; do not silently substitute an XAPK/APK from another
    # provider with a different build code.
    is_bundle_app = False
    try:
        cfg_path = Path("apps") / "apkmirror" / f"{app_name}.json"
        if cfg_path.exists():
            with cfg_path.open() as cfg_file:
                is_bundle_app = str(json.load(cfg_file).get("type", "APK")).upper() == "BUNDLE"
    except Exception:
        pass

    if is_bundle_app:
        # Native APKM/APKS bundle sources remain authoritative for bundle apps.
        # gplaydl is intentionally not inserted here because --no-splits would
        # turn a bundle download into a base APK that is not equivalent to the
        # native Morphe bundle.
        download_methods = [
            downloader.download_apkmirror,
            downloader.download_apkcombo,
        ]
    else:
        # Keep the original public-source order for TikTok. TikTok was already
        # building successfully with these sources before gplaydl was added,
        # so Google Play must remain a fallback rather than becoming the
        # preferred downloader for this app.
        public_download_methods = [
            downloader.download_apkmirror,
            downloader.download_aptoide,
            downloader.download_github,
            downloader.download_uptodown,
            downloader.download_apkpure,
            downloader.download_apkcombo,
        ]

        # Facebook and Messenger are now downloaded from Google Play first.
        # Their current Play artifacts may contain arm64 native libraries in the
        # base APK rather than a separate config.arm64_v8a split, and public
        # providers are also frequently blocked by Cloudflare. gplaydl therefore
        # gets first chance for these two apps, with the public providers kept as
        # a fallback if Google Play cannot serve the requested version.
        if app_name in {"facebook", "messenger"}:
            download_methods = [downloader.download_gplaydl] + public_download_methods
        else:
            # Keep the original, proven public download path first for every
            # other non-bundle app. gplaydl remains a last-resort fallback.
            download_methods = public_download_methods + [downloader.download_gplaydl]

    input_apk = None
    version = None
    candidates: list[str] = []
    used_method = None
    for method in download_methods:
        input_apk, version, candidates = method(app_name, str(cli), str(patches), arch)
        if input_apk:
            # Bundle apps can be APKM/APKS/XAPK depending on the source.
            # download_platform has already enforced any available Morphe
            # build-code validation before returning the file.
            if is_bundle_app and input_apk.suffix.lower() not in {".apkm", ".apks", ".xapk"}:
                logging.warning(
                    f"Rejected non-native bundle input {input_apk.name} for bundle-configured "
                    f"app {app_name}."
                )
                input_apk.unlink(missing_ok=True)
                input_apk = None
                continue
            used_method = method
            break

    if input_apk is None or not used_method or not version:
        logging.error(f"❌ Failed to download APK for {app_name}")
        logging.error("All download sources failed. Skipping this app.")
        return None

    # The downloader already handles version fallback when a release
    # cannot be found/downloaded. Once a supported release has actually been
    # downloaded, do NOT retry older patch-set versions after a patch failure.
    # The highest supported version is authoritative for the build.
    versions_to_try: list[str] = [version]

    exclude_patches = []
    include_patches = []
    patch_options = []

    patches_path = Path("patches") / f"{app_name}-{source}.txt"
    if patches_path.exists():
        with patches_path.open('r') as patches_file:
            for line in patches_file:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('-'):
                    exclude_patches.extend(["-d", line[1:].strip()])
                elif line.startswith('+'):
                    include_patches.extend(["-e", line[1:].strip()])
                elif line.startswith('@'):
                    # Patch option syntax:
                    # @optionName=value  ->  -OoptionName=value
                    option = line[1:].strip()
                    if option:
                        patch_options.extend(["-O" + option])

    for attempt_idx, ver in enumerate(versions_to_try):
        if attempt_idx > 0:
            logging.warning(
                f"Retrying {app_name}/{source}/{arch} with older version {ver} due to patch failure..."
            )
            # Cleanup any previous attempt artifacts.
            try:
                input_apk.unlink(missing_ok=True)
            except Exception:
                pass

            input_apk, version, _ = used_method(app_name, str(cli), str(patches), arch, override_version=ver)
            if input_apk is None:
                continue
            version = ver

        # --- Normalize input only when the patcher cannot consume it ---
        # Morphe accepts APKMirror's native APKM/APKS bundles directly.
        # Do not extract base.apk or merge split bundles with APKEditor: doing
        # so can change the package/signing/layout that the patch set expects.
        # Keep the downloaded bundle untouched and only normalize standalone
        # APK filenames.
        if input_apk.suffix.lower() == ".apk":
            target_apk = input_apk
        elif input_apk.suffix.lower() in [".apkm", ".apks", ".xapk", ".zip"]:
            logging.info(f"Keeping native bundle for Morphe: {input_apk.name}")
        else:
            target_apk = input_apk.with_name(f"{input_apk.stem}.apk")
            logging.info(f"Normalizing standalone download to {target_apk.name}")
            if input_apk != target_apk:
                target_apk.unlink(missing_ok=True)
                os.replace(input_apk, target_apk)
                input_apk = target_apk

        if not input_apk.exists():
            logging.error("Processed input file not found")
            raise RuntimeError("Processed input file not found")

        # --- ARCHITECTURE / INTEGRITY PROCESSING ---
        # APKM/APKS bundles must remain untouched. Morphe handles the selected
        # architecture and bundle structure itself. Rewriting the ZIP or
        # running zip -FF here can invalidate the bundle.
        is_native_bundle = input_apk.suffix.lower() in [".apkm", ".apks", ".xapk"]
        if not is_native_bundle:
            if arch != "universal":
                logging.info(f"Processing APK for {arch} architecture...")
                if arch == "arm64-v8a":
                    utils.strip_zip_entries(input_apk, ["lib/x86/*", "lib/x86_64/*", "lib/armeabi-v7a/*"])
                elif arch == "armeabi-v7a":
                    utils.strip_zip_entries(input_apk, ["lib/x86/*", "lib/x86_64/*", "lib/arm64-v8a/*"])
            else:
                utils.strip_zip_entries(input_apk, ["lib/x86/*", "lib/x86_64/*"])

            logging.info("Checking APK integrity...")
            if not utils.check_apk_integrity(input_apk):
                logging.warning("APK integrity check failed; keeping original input for Morphe")
            else:
                logging.info("APK integrity OK; no repair needed")
        else:
            logging.info(f"Preserving native Morphe bundle without modification: {input_apk.name}")

        # Include architecture in output filename
        output_apk = Path(f"{app_name}-{arch}-patch-v{patch_version}-app-v{version}.apk") if patch_version else Path(f"{app_name}-{arch}-patch-v{version}.apk")

        try:
            # USE DIFFERENT COMMANDS BASED ON SOURCE TYPE
            if is_morphe:
                logging.info("🔧 Using Morphe patching system...")
                morphe_cmd = [
                    "java", "-jar", str(cli),
                    "patch", "--patches", str(patches),
                    *exclude_patches, *include_patches, *patch_options,
                    *([ "--force" ] if (
                        version not in (
                            [target["version"] for target in source_targets]
                            or utils.get_supported_versions(
                                _configured_package(app_name) or app_name,
                                str(cli), str(patches)
                            )
                        )
                    ) else []),
                    "--out", str(output_apk), str(input_apk)
                ]
                utils.run_process(morphe_cmd, capture=True, stream=True)
            else:
                logging.info("🔧 Using ReVanced patching system...")
                cli_name = Path(cli).name.lower()
                is_revanced_v6_or_newer = (
                    'revanced-cli-6' in cli_name or 'revanced-cli-7' in cli_name or 'revanced-cli-8' in cli_name
                )

                if is_revanced_v6_or_newer:
                    utils.run_process([
                        "java", "-jar", str(cli),
                        "patch", "-p", str(patches), "-b",
                        *exclude_patches, *include_patches, *patch_options,
                        "--out", str(output_apk), str(input_apk)
                    ], capture=True, stream=True)
                else:
                    utils.run_process([
                        "java", "-jar", str(cli),
                        "patch", "--patches", str(patches),
                        *exclude_patches, *include_patches, *patch_options,
                        "--out", str(output_apk), str(input_apk)
                    ], capture=True, stream=True)

        except subprocess.CalledProcessError as e:
            # Remove temp input apk; we'll re-download if retrying.
            input_apk.unlink(missing_ok=True)
            output_apk.unlink(missing_ok=True)

            if attempt_idx < len(versions_to_try) - 1 and _should_retry_with_older_version(getattr(e, "output", None)):
                continue
            raise

        # Patch succeeded -> cleanup input and sign.
        input_apk.unlink(missing_ok=True)

        signed_apk = (
            Path(f"{app_name}-{arch}-{name}-patch-v{patch_version}-app-v{version}.apk")
            if patch_version
            else Path(f"{app_name}-{arch}-{name}-v{version}.apk")
        )

        apksigner = utils.find_apksigner()
        if not apksigner:
            raise RuntimeError("apksigner not found")

        # --- SIGN APK ---
        # Follow the original Morphe-AutoBuilds signing approach: apksigner
        # receives a JKS keystore, alias, and passwords. In CI the JKS is
        # materialized from the encrypted Actions secret at runtime.
        signing_keystore = getenv("SIGNING_KEYSTORE_PATH")
        signing_alias = getenv("SIGNING_KEY_ALIAS", "public")
        signing_keystore_password = getenv("SIGNING_KEYSTORE_PASSWORD", "public")
        signing_key_password = getenv("SIGNING_KEY_PASSWORD", signing_keystore_password)

        if not signing_keystore:
            raise RuntimeError(
                "SIGNING_KEYSTORE_PATH is not set. Configure the signing keystore "
                "through the CI secret and environment variables."
            )

        if not Path(signing_keystore).is_file():
            raise RuntimeError(f"Signing keystore not found: {signing_keystore}")

        signing_common_args = [
            str(apksigner), "sign", "--verbose",
            "--ks", signing_keystore,
            "--ks-pass", "env:SIGNING_KEYSTORE_PASSWORD",
            "--key-pass", "env:SIGNING_KEY_PASSWORD",
            "--ks-key-alias", signing_alias,
            "--in", str(output_apk), "--out", str(signed_apk)
        ]
        try:
            utils.run_process(
                signing_common_args,
                capture=True, stream=True
            )
        except Exception as e:
            logging.warning(f"Standard signing failed: {e}")
            logging.info("Trying alternative signing method...")

            utils.run_process([
                str(apksigner), "sign", "--verbose",
                "--min-sdk-version", "21",
                *signing_common_args[3:]
            ], capture=True, stream=True)

        output_apk.unlink(missing_ok=True)
        print(f"✅ APK built: {signed_apk.name}")
        return str(signed_apk)

    # If we got here, every candidate version failed.
    return None

def main():
    app_name = getenv("APP_NAME")
    source = getenv("SOURCE")

    if not app_name or not source:
        logging.error("APP_NAME and SOURCE environment variables must be set")
        exit(1)

    # Read arch-config.json
    arch_config_path = Path("arch-config.json")
    if arch_config_path.exists():
        with open(arch_config_path) as f:
            arch_config = json.load(f)
        
        # Find arches for this app
        arches = [(getenv("ARCH") or "universal").strip()]
        for config in arch_config:
            if not getenv("ARCH") and config["app_name"] == app_name and config["source"] == source:
                arches = config["arches"]
                break
        
        # Build for each architecture
        built_apks = []
        for arch in arches:
            logging.info(f"🔨 Building {app_name} for {arch} architecture...")
            apk_path = run_build(app_name, source, arch)
            if apk_path:
                built_apks.append(apk_path)
                print(f"✅ Built {arch} version: {Path(apk_path).name}")
        
        # Summary
        print(f"\n🎯 Built {len(built_apks)} APK(s) for {app_name}:")
        for apk in built_apks:
            print(f"  📱 {Path(apk).name}")
        
    else:
        # Fallback to single universal build
        logging.warning("arch-config.json not found, building universal only")
        apk_path = run_build(app_name, source, "universal")
        if apk_path:
            print(f"🎯 Final APK path: {apk_path}")

if __name__ == "__main__":
    main()
