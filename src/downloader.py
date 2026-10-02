import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from src import (
    utils,
    apkpure,
    session,
    uptodown,
    aptoide,
    apkmirror,
    github,
    apkcombo,
    gplaydl,
    archive,
)

def download_resource(url: str, name: str = None) -> Path:
    res = session.get(url, stream=True)
    res.raise_for_status()
    final_url = res.url

    if not name:
        name = utils.extract_filename(res, fallback_url=final_url)

    filepath = Path(name)
    total_size = int(res.headers.get('content-length', 0))
    downloaded_size = 0

    with filepath.open("wb") as file:
        for chunk in res.iter_content(chunk_size=8192):
            if chunk:
                file.write(chunk)
                downloaded_size += len(chunk)

    logging.info(
        f"URL: {final_url} [{downloaded_size}/{total_size}] -> \"{filepath}\" [1]"
    )

    return filepath

def download_required(source: str) -> tuple[list[Path], str]:
    source_path = Path("sources") / f"{source}.json"
    with source_path.open() as json_file:
        repos_info = json.load(json_file)

    # Handle bundle format
    if isinstance(repos_info, dict) and "bundle_url" in repos_info:
        return download_from_bundle(repos_info)
    
    # Handle old list format
    name = repos_info[0]["name"]
    downloaded_files = []

    for repo_info in repos_info[1:]:
        release = utils.detect_release(repo_info)
        entry_name = (
            repo_info.get("repo")
            or repo_info.get("project")
            or repo_info.get("name")
            or ""
        ).lower()

        for asset in release["assets"]:
            asset_name = asset["name"]
            asset_url = asset["browser_download_url"]
            if asset_name.endswith(".asc"):
                continue

            # Patch sources publish a .mpp bundle alongside optional checksums/SBOMs.
            # Only download the patch bundle; the Morphe CLI is the separate .jar.
            if asset_name.lower().endswith(".mpp"):
                downloaded_files.append(download_resource(asset_url))
            elif "morphe-cli" in entry_name and asset_name.lower().endswith(".jar"):
                downloaded_files.append(download_resource(asset_url))

    # Every non-"morphe" source gets one additional, centrally maintained
    # universal patch bundle from MorpheApp/morphe-patches. The build step
    # enables ONLY the requested universal patch ("Disable Play Store updates")
    # from this bundle; app-specific patches remain owned by the configured
    # source. The native "morphe" source already uses this exact bundle, so do
    # not download it twice there.
    if name.lower() != "morphe":
        try:
            universal_release = utils.detect_github_release("MorpheApp", "morphe-patches", "latest")
            for asset in universal_release.get("assets", []):
                asset_name = str(asset.get("name") or "")
                if asset_name.lower().endswith(".mpp") and not asset_name.lower().endswith(".asc"):
                    universal_name = f"morphe-universal-{asset_name}"
                    downloaded_files.append(
                        download_resource(asset["browser_download_url"], name=universal_name)
                    )
                    logging.info(
                        "Downloaded Morphe universal patch bundle: %s", universal_name
                    )
                    break
        except Exception as exc:
            logging.warning(
                "Could not download Morphe universal patch bundle for %s: %s",
                name,
                exc,
            )

    return downloaded_files, name

def download_from_bundle(bundle_info: dict) -> tuple[list[Path], str]:
    """Download resources from a bundle URL"""
    bundle_url = bundle_info["bundle_url"]
    name = bundle_info.get("name", "bundle-patches")
    
    logging.info(f"Downloading bundle from {bundle_url}")
    
    # Download the bundle JSON
    with session.get(bundle_url) as res:
        res.raise_for_status()
        bundle_data = res.json()
    
    downloaded_files = []
    
    # Check API version and structure
    if "patches" in bundle_data:
        # API v4 format
        patches = bundle_data.get("patches", [])
        integrations = bundle_data.get("integrations", [])
        
        # Download patches (JAR files)
        for patch in patches:
            if "url" in patch:
                filepath = download_resource(patch["url"])
                downloaded_files.append(filepath)
                logging.info(f"Downloaded patch: {patch.get('name', 'unknown')}")
        
        # Download integrations (APK files)
        for integration in integrations:
            if "url" in integration:
                filepath = download_resource(integration["url"])
                downloaded_files.append(filepath)
                logging.info(f"Downloaded integration: {integration.get('name', 'unknown')}")
    
    # Also download CLI (still needed) - try ReVanced CLI first
    try:
        cli_release = utils.detect_github_release("revanced", "revanced-cli", "latest")
        for asset in cli_release["assets"]:
            if asset["name"].endswith(".asc"):
                continue
            if asset["name"].endswith(".jar") and "cli" in asset["name"].lower():
                filepath = download_resource(asset["browser_download_url"])
                downloaded_files.append(filepath)
                logging.info("Downloaded ReVanced CLI")
                break
    except Exception as e:
        logging.warning(f"Could not download ReVanced CLI: {e}")
    
    return downloaded_files, name

def get_supported_version_codes(package_name: str, cli: str, patches: str) -> dict[str, list[int]]:
    """Return Morphe's declared version codes for each supported version."""
    cmd = [
        "java", "-jar", cli,
        "list-versions",
        "-f", package_name,
        "--patches", patches,
    ]
    output = utils.run_process(cmd, capture=True, silent=True, check=False) or ""
    result: dict[str, list[int]] = {}
    for line in output.splitlines():
        m = re.search(r"^\s*(\d+(?:\.\d+)+).*?versionCodes:\s*[^=]+=([0-9]+)", line)
        if not m:
            continue
        result.setdefault(m.group(1), []).append(int(m.group(2)))
    return result

_STORE_LATEST_VERSION_CACHE = {}


def _get_store_latest_versions(app_name: str, config: dict, platform: str) -> list[str]:
    """Discover latest store versions independently of the patch-compatible list.

    APKMirror can be unavailable to GitHub-hosted runners because of Cloudflare.
    Do not let that single provider decide the version candidates for every
    other provider. Cache the cross-store result so one build job does not
    repeatedly scrape the same catalogs while moving through the provider
    fallback chain.
    """
    key = (
        app_name,
        str(config.get("package") or ""),
        str(config.get("arch") or "universal"),
    )
    if key in _STORE_LATEST_VERSION_CACHE:
        return list(_STORE_LATEST_VERSION_CACHE[key])

    provider_modules = [
        ("uptodown", uptodown),
        ("aptoide", aptoide),
        ("apkcombo", apkcombo),
        ("apkpure", apkpure),
    ]

    ordered = []
    current = next((item for item in provider_modules if item[0] == platform), None)
    if current:
        ordered.append(current)
    ordered.extend(item for item in provider_modules if item[0] != platform)

    versions = []
    for provider_name, module in ordered:
        try:
            latest = module.get_latest_version(app_name, config)
            if latest and latest not in versions:
                versions.append(latest)
                logging.info(
                    f"Latest store version for {app_name}: {latest} (source: {provider_name})"
                )
        except Exception as exc:
            logging.debug(
                f"Could not get latest version for {app_name} from {provider_name}: {exc}"
            )

    _STORE_LATEST_VERSION_CACHE[key] = list(versions)
    return versions

def download_platform(
    app_name: str,
    platform: str,
    cli: str,
    patches: str,
    arch: str = None,
    override_version: str = None,
) -> tuple[Path | None, str | None, list[str]]:
    try:
        config_path = Path("apps") / platform / f"{app_name}.json"
        config = None
        if config_path.exists():
            with config_path.open() as json_file:
                config = json.load(json_file)
        else:
            # Fallback: search other platform config directories for this app
            for other_platform in ["apkmirror", "uptodown", "apkpure", "aptoide", "github", "apkcombo"]:
                if other_platform == platform:
                    continue
                other_path = Path("apps") / other_platform / f"{app_name}.json"
                if other_path.exists():
                    try:
                        with other_path.open() as json_file:
                            other_cfg = json.load(json_file)
                        if other_cfg.get("package"):
                            # A config synthesized from another provider is only
                            # a package/variant mapping. Never inherit that
                            # provider's pinned version or release-specific
                            # validation, because those values may be tied to
                            # APKMirror and can prevent a fallback source from
                            # discovering its own compatible version.
                            config = {
                                "name": other_cfg.get("name", app_name),
                                "package": other_cfg["package"],
                                "version": "",
                                "arch": other_cfg.get("arch", "universal"),
                                "type": other_cfg.get("type", "APK"),
                                "dpi": other_cfg.get("dpi", "nodpi"),
                            }
                            logging.info(
                                f"Synthesized {platform} config for {app_name} "
                                f"from {other_platform} without provider-specific version pin"
                            )
                            break
                    except Exception:
                        continue

        if not config or not config.get("package"):
            raise FileNotFoundError(f"Config file not found for {app_name} on {platform}")
        
        # Override arch only if explicitly specified non-universal, or if config has no arch set
        if arch and arch != "universal":
            config['arch'] = arch
        elif 'arch' not in config or not config['arch']:
            config['arch'] = arch or "universal"

        # When the caller is trying a specific patch-source target, providers
        # must resolve that exact version only. This prevents a provider from
        # silently substituting its own latest/nearest version before the
        # caller has given the other providers a chance to serve the same target.
        config['_strict_version'] = bool(override_version)

        platform_module = globals()[platform]

        # Candidate versions (highest -> lowest):
        # - An explicit retry override remains authoritative for that retry.
        # - A pinned config version remains authoritative.
        # - Otherwise use the patch source's declared targets. When the source
        #   declares stable targets, experimental/unknown targets are excluded.
        #   When it declares only experimental targets, they remain as a
        #   compatibility fallback.
        # - Only when the source exposes no machine-readable target metadata do
        #   we fall back to CLI/store discovery.
        pinned = (config.get("version") or "").strip()
        source_targets = utils.get_source_supported_targets(
            config["package"], os.getenv("SOURCE", "")
        )

        if override_version:
            candidates = [override_version]
        elif pinned:
            candidates = [pinned]
        elif source_targets:
            candidates = [target["version"] for target in source_targets]
        else:
            candidates = utils.get_supported_versions(config["package"], cli, patches)

            try:
                latest = platform_module.get_latest_version(app_name, config)
                if latest and latest not in candidates:
                    candidates.append(latest)
            except Exception as e:
                logging.debug(
                    f"Could not get latest version for {app_name} on {platform}: {e}"
                )

        logging.info(f"Version candidates for {app_name} on {platform}: {candidates}")

        last_error: Exception | None = None
        for version in candidates:
            if not version:
                continue
            download_link = platform_module.get_download_link(version, app_name, config)
            if not download_link:
                last_error = ValueError(
                    f"No download link found for {app_name} version {version}"
                )
                continue

            try:
                filepath = download_resource(download_link)

                min_size_mb = config.get("min_size_mb")
                if min_size_mb is not None:
                    actual_size_mb = filepath.stat().st_size / (1024 * 1024)
                    if actual_size_mb <= float(min_size_mb):
                        logging.warning(
                            f"Rejected {filepath.name}: file size {actual_size_mb:.2f} MB "
                            f"is not greater than {float(min_size_mb):.2f} MB"
                        )
                        filepath.unlink(missing_ok=True)
                        last_error = ValueError(
                            f"Artifact for {app_name} is too small: {actual_size_mb:.2f} MB"
                        )
                        continue

                return filepath, version, candidates
            except Exception as e:
                last_error = e
                continue

        raise last_error or ValueError(f"No downloadable versions found for {app_name} on {platform}")

    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        return None, None, []

def download_archive(
    app_name: str,
    cli: str,
    patches: str,
    arch: str = None,
    override_version: str = None,
) -> tuple[Path | None, str | None, list[str]]:
    """Download an exact Archive.org artifact declared in the app manifest."""
    if not override_version:
        logging.info("Archive provider skipped for %s: no exact target version.", app_name)
        return None, None, []

    cfg_path = Path("apps") / "archive" / f"{app_name}.json"
    if not cfg_path.exists():
        return None, None, []

    try:
        with cfg_path.open(encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception as exc:
        logging.warning("Could not read Archive config for %s: %s", app_name, exc)
        return None, None, []

    link = archive.get_download_link(override_version, app_name, cfg, arch=arch)
    if not link:
        return None, None, []

    try:
        filepath = download_resource(link)
        valid, reasons = archive.validate_exact_artifact(
            filepath, app_name, arch=arch, config=cfg, version=override_version
        )
        if not valid:
            logging.warning(
                "Archive exact-artifact rejected for %s: %s",
                app_name, "; ".join(reasons),
            )
            filepath.unlink(missing_ok=True)
            return None, None, []

        logging.info(
            "Archive exact-artifact passed its manifest validation: %s",
            filepath.name,
        )
        return filepath, str(override_version), [str(override_version)]
    except Exception as exc:
        logging.warning(
            "Archive exact-artifact download failed for %s: %s",
            app_name, exc,
        )
        return None, None, []


def download_gplaydl(
    app_name: str,
    cli: str,
    patches: str,
    arch: str = None,
    override_version: str = None,
) -> tuple[Path | None, str | None, list[str]]:
    """Prefer Google Play through gplaydl when its CI API key is configured."""
    filepath, version = gplaydl.download_app(
        app_name, cli, patches, arch or "arm64-v8a", override_version
    )
    if filepath and version:
        return filepath, version, [version]
    return None, None, []

def download_apkmirror(
    app_name: str,
    cli: str,
    patches: str,
    arch: str = None,
    override_version: str = None,
) -> tuple[Path | None, str | None, list[str]]:
    return download_platform(app_name, "apkmirror", cli, patches, arch, override_version)

def download_github(
    app_name: str,
    cli: str,
    patches: str,
    arch: str = None,
    override_version: str = None,
) -> tuple[Path | None, str | None, list[str]]:
    return download_platform(app_name, "github", cli, patches, arch, override_version)

def download_apkpure(
    app_name: str,
    cli: str,
    patches: str,
    arch: str = None,
    override_version: str = None,
) -> tuple[Path | None, str | None, list[str]]:
    return download_platform(app_name, "apkpure", cli, patches, arch, override_version)

def download_aptoide(
    app_name: str,
    cli: str,
    patches: str,
    arch: str = None,
    override_version: str = None,
) -> tuple[Path | None, str | None, list[str]]:
    return download_platform(app_name, "aptoide", cli, patches, arch, override_version)

def download_uptodown(
    app_name: str,
    cli: str,
    patches: str,
    arch: str = None,
    override_version: str = None,
) -> tuple[Path | None, str | None, list[str]]:
    return download_platform(app_name, "uptodown", cli, patches, arch, override_version)

def download_apkcombo(
    app_name: str,
    cli: str,
    patches: str,
    arch: str = None,
    override_version: str = None,
) -> tuple[Path | None, str | None, list[str]]:
    return download_platform(app_name, "apkcombo", cli, patches, arch, override_version)

def download_apkeditor() -> Path:
    max_retries = 3
    for attempt in range(max_retries):
        try:
            release = utils.detect_github_release("REAndroid", "APKEditor", "latest")

            for asset in release["assets"]:
                if asset["name"].startswith("APKEditor") and asset["name"].endswith(".jar"):
                    return download_resource(asset["browser_download_url"])

            raise RuntimeError("APKEditor .jar file not found in the latest release")
        except Exception as e:
            if attempt == max_retries - 1:
                raise RuntimeError(f"Failed to download APKEditor after {max_retries} attempts: {e}")
            logging.warning(f"APKEditor download attempt {attempt + 1} failed: {e}. Retrying...")
            time.sleep(2)  # Wait 2 seconds before retry