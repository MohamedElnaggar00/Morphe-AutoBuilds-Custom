#!/usr/bin/env python3
"""
Release-per-app incremental planner.

Each (app, patch source, architecture) owns its own GitHub Release history.
A build is required when:
  - no previous APK exists for that exact app/source/arch;
  - the patch-source release version changed;
  - the app version supported by the current patch set changed.

The planner intentionally does not use manifest.json. This makes the release
history itself the source of truth and allows the repository to start with zero
releases.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_app_updates as legacy


def gh(args: List[str], timeout: int = 300) -> Tuple[int, str, str]:
    env = os.environ.copy()
    if env.get("GITHUB_TOKEN") and "GH_TOKEN" not in env:
        env["GH_TOKEN"] = env["GITHUB_TOKEN"]
    try:
        p = subprocess.run(["gh", *args], capture_output=True, text=True,
                           env=env, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, "", str(e)


def repo_name() -> str:
    value = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if "/" not in value:
        raise RuntimeError("GITHUB_REPOSITORY is missing")
    return value


def load_releases() -> List[dict]:
    owner, name = repo_name().split("/", 1)
    rc, out, err = gh([
        "api", "--paginate", "--slurp",
        f"repos/{owner}/{name}/releases?per_page=100",
    ])
    if rc != 0:
        raise RuntimeError(f"Could not read GitHub releases: {err.strip()}")
    try:
        data = json.loads(out)
    except Exception as e:
        raise RuntimeError(f"Invalid GitHub releases response: {e}")
    # --slurp returns one array containing the page arrays.
    if data and isinstance(data[0], list):
        flattened = []
        for page in data:
            flattened.extend(page if isinstance(page, list) else [])
        data = flattened
    return [r for r in data if isinstance(r, dict) and not r.get("draft")]


def extract_asset_identity(name: str, app: str, source: str, arch: str) -> Optional[dict]:
    """
    Match the exact filename identity produced by Morphe:
      app-arch-source-patch-vPATCH-app-vAPP.apk

    Source names may contain hyphens, so matching is anchored by the known
    app/source/arch values rather than splitting on '-'.
    """
    prefix = f"{app}-{arch}-{source}-"
    if not name.startswith(prefix) or not name.endswith(".apk"):
        return None

    patch_match = re.search(r"-patch-v(.+)-app-v(.+)\.apk$", name)
    if patch_match:
        return {
            "apk": name,
            "patch_version": patch_match.group(1).strip(),
            "app_version": patch_match.group(2).strip(),
        }

    # Backward compatibility for old filenames without the explicit
    # patch-v/app-v markers. These are only useful as historical state.
    old_match = re.search(r"-v(\d[^-]*)\.apk$", name)
    return {
        "apk": name,
        "patch_version": "",
        "app_version": old_match.group(1).strip() if old_match else "",
    }


def _source_asset_names(source: str) -> List[str]:
    """Return configured source IDs/names that may appear in published APK filenames."""
    names = {str(source).strip()}
    path = ROOT / "sources" / f"{source}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list) and data and isinstance(data[0], dict):
            display_name = str(data[0].get("name") or "").strip()
            if display_name:
                names.add(display_name)
    except Exception:
        pass
    return [name for name in names if name]


def _release_metadata(release: dict) -> dict:
    """Read the exact identity written into generated release notes."""
    body = release.get("body") or ""
    fields = {
        "app_name": r"^- \*\*Application:\*\* (.+)$",
        "source": r"^- \*\*Patch source:\*\* (.+)$",
        "arch": r"^- \*\*Architecture:\*\* (.+)$",
    }
    values = {}
    for key, pattern in fields.items():
        match = re.search(pattern, body, re.MULTILINE)
        if match:
            values[key] = match.group(1).strip()
    return values


def _apk_versions(name: str) -> Optional[dict]:
    """Extract patch/app versions from a generated APK filename."""
    match = re.search(r"-patch-v(.+)-app-v(.+)\.apk$", name or "", re.IGNORECASE)
    if not match:
        return None
    return {
        "patch_version": match.group(1).strip().lstrip("vV"),
        "app_version": match.group(2).strip().lstrip("vV"),
    }


def latest_build_for(app: str, source: str, arch: str,
                     releases: List[dict]) -> Optional[dict]:
    """
    Find the newest published build for the exact (app, source, arch).

    Generated releases now carry authoritative identity in their release-body
    metadata. That metadata is preferred over release titles because titles are
    intentionally human-friendly (e.g. "X - piko-newx"), while APK filenames
    may use the source's configured display name (e.g. "piko-patches").
    """
    candidates = []
    source_names = _source_asset_names(source)

    for release in releases:
        if release.get("draft"):
            continue

        release_name = (release.get("name") or "").strip()
        release_tag = release.get("tag_name") or ""
        assets = [
            a for a in (release.get("assets") or [])
            if (a.get("name") or "").strip().lower().endswith(".apk")
        ]

        metadata = _release_metadata(release)
        metadata_complete = all(
            key in metadata for key in ("app_name", "source", "arch")
        )

        # Preferred path: generated release metadata is exact and source-aware.
        if metadata_complete:
            if (
                metadata["app_name"] != app
                or metadata["source"] != source
                or metadata["arch"] != arch
            ):
                continue

            for asset in assets:
                name = (asset.get("name") or "").strip()
                versions = _apk_versions(name)
                if not versions:
                    continue
                candidates.append({
                    "apk": name,
                    "patch_version": versions["patch_version"],
                    "app_version": versions["app_version"],
                    "release_tag": release_tag,
                    "release_name": release_name,
                    "published_at": release.get("published_at")
                    or release.get("created_at")
                    or "",
                })
                break
            continue

        # Backward-compatible path for releases created before exact metadata.
        # Still require the configured source identity in the APK filename and,
        # when available, in the generated tag. Never fall back to app/version
        # alone because that could confuse two patch sources for the same app.
        safe_source = re.sub(r"[^A-Za-z0-9._-]+", "-", str(source)).strip("-")
        tag_source_match = (
            not release_tag
            or f"-{safe_source}-" in release_tag
        )

        if not tag_source_match:
            continue

        for asset in assets:
            name = (asset.get("name") or "").strip()
            for source_name in source_names:
                identity = extract_asset_identity(name, app, source_name, arch)
                if not identity:
                    continue
                identity["release_tag"] = release_tag
                identity["release_name"] = release_name
                identity["published_at"] = (
                    release.get("published_at")
                    or release.get("created_at")
                    or ""
                )
                candidates.append(identity)
                break

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (x.get("published_at", ""), x.get("release_tag", ""))
    )
    return candidates[-1]
def source_patch_version(source: str) -> str:
    sig = legacy.get_source_signature(source)
    effective = legacy._effective_patch_version_signature(sig)
    versions = []
    for segment in effective.split(";"):
        if "@" not in segment:
            continue
        version = segment.rsplit("@", 1)[-1].strip().lstrip("vV")
        if version:
            versions.append(version)
    if not versions:
        return ""

    # Multiple repositories can make one source. Prefer the highest semantic
    # version as the patch identity.
    try:
        return max(versions, key=legacy.provider_utils.normalize_version)
    except Exception:
        return versions[-1]


def expected_app_version(app: str, source: str) -> str:
    # This is the same source-aware recommendation used by the existing
    # planner and mirrors what the build process supports.
    recommended = legacy.fetch_recommended_version(app, source).strip()
    if recommended:
        return recommended

    # NEVER use the store's latest version as a rebuild trigger when the patch
    # source does not expose a machine-readable supported-version list.
    #
    # Morphe/custom sources normally publish an opaque .mpp bundle. In that
    # case the store may already have a newer app version which the current
    # patch bundle does not support. Comparing against the store version would
    # therefore create false rebuilds (often for many apps at once).
    #
    # For opaque patch bundles, the patch-source release signature is already
    # compared separately and is the safe signal for a new supported app
    # version. Returning an empty value here deliberately prevents a
    # store-version-only rebuild.
    return ""


def needs_build(item: dict, previous: Optional[dict]) -> Tuple[bool, str, str, str]:
    app = item["app_name"]
    source = item["source"]

    patch_version = source_patch_version(source)
    app_version = expected_app_version(app, source)

    if previous is None:
        return True, "no previous release for this app/source", patch_version, app_version

    old_patch = previous.get("patch_version", "").strip()
    old_app = previous.get("app_version", "").strip()

    # If we can read a current patch version, it must match the published APK.
    if patch_version and old_patch and patch_version != old_patch:
        return True, f"patch update {old_patch} -> {patch_version}", patch_version, app_version

    # If current patch version could not be resolved, do not rebuild solely on
    # a missing value. A successful build will still publish a concrete filename.
    if app_version and old_app and app_version != old_app:
        return True, f"app update {old_app} -> {app_version}", patch_version, app_version

    # Historical APKs may not contain version markers. Treat that as stale so
    # the first run after migration establishes the new naming convention.
    if not old_patch or not old_app:
        return True, "previous APK has no complete version identity", patch_version, app_version

    return False, "up to date", patch_version, app_version


def write_output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        print(f"{key}={value}")
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


def main() -> int:
    force = os.environ.get("FORCE_FULL_REBUILD", "false").lower() in {"1", "true", "yes"}
    releases = load_releases()
    full = legacy.build_full_matrix()

    build_matrix = []
    reasons = []

    for item in full:
        previous = latest_build_for(
            item["app_name"], item["source"], item["arch"], releases
        )

        if force:
            build = True
            reason = "forced full rebuild"
            patch_version = source_patch_version(item["source"])
            app_version = expected_app_version(item["app_name"], item["source"])
        else:
            build, reason, patch_version, app_version = needs_build(item, previous)

        label = f"{item['app_name']}/{item['source']}/{item['arch']}"
        if build:
            build_matrix.append(item)
            reasons.append(f"{label}: {reason}")
            print(f"BUILD  {label}: {reason}")
        else:
            print(f"SKIP   {label}: {reason}")

    Path("build_matrix.json").write_text(json.dumps(build_matrix, indent=2), encoding="utf-8")
    Path("release_plan.json").write_text(json.dumps({
        "builds": build_matrix,
        "reasons": reasons,
    }, indent=2), encoding="utf-8")

    matrix_json = json.dumps(build_matrix, separators=(",", ":"))
    write_output("build_matrix", matrix_json)
    write_output("has_updates", "true" if build_matrix else "false")
    write_output("update_count", str(len(build_matrix)))
    write_output("total_count", str(len(full)))
    write_output("carry_count", "0")
    write_output("incremental", "false" if force else "true")

    print("=" * 70)
    print(f"Total app/source/arch entries: {len(full)}")
    print(f"Need build: {len(build_matrix)}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
