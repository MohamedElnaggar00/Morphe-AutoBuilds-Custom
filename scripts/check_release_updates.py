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


def latest_build_for(app: str, source: str, arch: str,
                     releases: List[dict]) -> Optional[dict]:
    candidates = []
    for release in releases:
        release_name = (release.get("name") or "").strip()

        # Release title is authoritative. APK filenames can contain the patch
        # package name rather than the configured source key.
        title_match = re.match(
            rf"^{re.escape(app)} v(.+) — Patch v(.+) — "
            rf"{re.escape(source)} — {re.escape(arch)}$",
            release_name,
        )
        if title_match:
            candidates.append({
                "apk": next(
                    (
                        (a.get("name") or "").strip()
                        for a in release.get("assets") or []
                        if (a.get("name") or "").endswith(".apk")
                    ),
                    "",
                ),
                "patch_version": title_match.group(2).strip().lstrip("vV"),
                "app_version": title_match.group(1).strip().lstrip("vV"),
                "release_tag": release.get("tag_name") or "",
                "release_name": release_name,
                "published_at": release.get("published_at") or release.get("created_at") or "",
            })
            continue

        # Backward-compatible fallback for older release titles.
        for asset in release.get("assets") or []:
            name = (asset.get("name") or "").strip()
            identity = extract_asset_identity(name, app, source, arch)
            if not identity:
                continue
            identity["release_tag"] = release.get("tag_name") or ""
            identity["release_name"] = release_name
            identity["published_at"] = release.get("published_at") or release.get("created_at") or ""
            candidates.append(identity)

    if not candidates:
        return None

    candidates.sort(key=lambda x: x["published_at"])
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

    # Fallback to the current app-store version when the patch list cannot be
    # inspected. This preserves update detection for sources without a readable
    # patches-list asset.
    return legacy.fetch_latest_app_version(app).strip()


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
