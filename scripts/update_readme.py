#!/usr/bin/env python3
"""Generate README from the exact Morphe patch bundles used by the builder."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_app_updates as legacy

API = "https://api.github.com"
TOKEN = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"

APP_NAMES = {
    "youtube": "YouTube",
    "reddit": "Reddit",
    "x": "X",
    "gboard": "Gboard",
    "vpnify": "vpnify",
    "tiktok": "TikTok",
    "tiktok-metra": "TikTok (Metra)",
    "camscanner": "CamScanner",
    "instagram": "Instagram",
}

WORK = ROOT / ".readme-cache"
WORK.mkdir(exist_ok=True)


def api_json(url: str):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def latest_release(owner: str, repo: str) -> dict:
    return api_json(f"{API}/repos/{owner}/{repo}/releases/latest")


def download_asset(release: dict, suffix: str, output: Path) -> str:
    candidates = [
        a for a in release.get("assets", [])
        if a.get("name", "").lower().endswith(suffix.lower())
        and not a.get("name", "").lower().endswith(".asc")
    ]
    if not candidates:
        raise RuntimeError(f"No {suffix} asset in {release.get('tag_name', 'latest')}")
    asset = candidates[0]
    headers = dict(HEADERS)
    headers["Accept"] = "application/octet-stream"
    r = requests.get(asset["url"], headers=headers, timeout=120)
    r.raise_for_status()
    output.write_bytes(r.content)
    return asset["name"]


def source_repo(source: str) -> tuple[str, str]:
    data = json.loads((ROOT / "sources" / f"{source}.json").read_text(encoding="utf-8"))
    repos = [
        x for x in data[1:]
        if x.get("user") and x.get("repo") and "morphe-cli" not in x.get("repo", "").lower()
    ]
    if not repos:
        raise RuntimeError(f"No patch repository found for source {source}")
    x = repos[-1]
    return x["user"], x["repo"]


def app_package(app_name: str) -> str:
    for platform in ("apkmirror", "apkpure", "uptodown", "aptoide", "apkcombo"):
        p = ROOT / "apps" / platform / f"{app_name}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")).get("package", "")
    raise RuntimeError(f"No package config found for {app_name}")


def normalize_patch_name(value: str) -> str:
    """Normalize patch names so local selections match CLI names case-insensitively."""
    value = value.strip().lower()
    value = re.sub(r"\s+", " ", value)
    return value


def local_rules(app_name: str, source: str) -> tuple[set[str], set[str]]:
    p = ROOT / "patches" / f"{app_name}-{source}.txt"
    enabled, disabled = set(), set()
    if not p.exists():
        return enabled, disabled
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("@"):
            continue
        if line.startswith("+"):
            enabled.add(normalize_patch_name(line[1:]))
        elif line.startswith("-"):
            disabled.add(normalize_patch_name(line[1:]))
    return enabled, disabled


def run_cli(cli: Path, patches: Path | list[Path], args: list[str]) -> str:
    patch_list = [patches] if isinstance(patches, Path) else list(patches)
    cmd = ["java", "-jar", str(cli), *args]
    for patch in patch_list:
        cmd += ["--patches", str(patch)]
    result = subprocess.run(cmd, cwd=WORK, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stdout + "\n" + result.stderr)
    return result.stdout




_published_release_versions_cache: dict[tuple[str, str, str], str] | None = None


def _published_release_versions() -> dict[tuple[str, str, str], str]:
    """Return the latest published APK version for each (app, source, arch).

    README generation runs after release publication, so the generated release
    metadata is the most reliable record of the version that was actually built
    and published. This is intentionally independent of store/CLI probing.
    """
    global _published_release_versions_cache
    if _published_release_versions_cache is not None:
        return _published_release_versions_cache

    _published_release_versions_cache = {}
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if "/" not in repo:
        return _published_release_versions_cache

    try:
        releases = []
        for page in range(1, 11):
            batch = api_json(f"{API}/repos/{repo}/releases?per_page=100&page={page}")
            if not isinstance(batch, list) or not batch:
                break
            releases.extend(batch)
            if len(batch) < 100:
                break

        # GitHub returns releases newest-first; keep the first matching release
        # for every app/source/architecture tuple.
        for release in releases:
            if release.get("draft"):
                continue
            body = str(release.get("body") or "")

            def field(name: str) -> str:
                match = re.search(
                    rf"^- \\*\\*{re.escape(name)}:\\*\\* (.+)$",
                    body,
                    re.MULTILINE,
                )
                return match.group(1).strip() if match else ""

            app = field("Application")
            source = field("Patch source")
            arch = field("Architecture")
            if not app or not source or not arch:
                continue

            version = field("Application version")
            if not version:
                for asset in release.get("assets") or []:
                    asset_name = str(asset.get("name") or "")
                    match = re.search(r"-app-v(.+)\\.apk$", asset_name)
                    if match:
                        version = match.group(1).strip()
                        break
            if version:
                _published_release_versions_cache.setdefault((app, source, arch), version)

    except Exception as exc:
        # Publishing metadata is an enhancement/fallback; a GitHub API outage
        # must not prevent the README from being regenerated.
        logging_warning = f"Published-release version lookup failed: {exc}"
        print(f"::warning::{logging_warning}")

    return _published_release_versions_cache


def published_app_version(app: str, source: str, architecture: str) -> str:
    return _published_release_versions().get((app, source, architecture), "")


def parse_patch_list(output: str) -> list[tuple[str, bool]]:
    rows = []
    current_name = None
    current_enabled = None
    for raw in output.splitlines():
        line = raw.strip()
        if line.startswith("Name: "):
            if current_name is not None:
                rows.append((current_name, bool(current_enabled)))
            current_name = line[6:].strip()
            current_enabled = None
        elif line.startswith("Enabled: "):
            current_enabled = line[9:].strip().lower() == "true"
    if current_name is not None:
        rows.append((current_name, bool(current_enabled)))
    unique = []
    seen = set()
    for name, enabled in rows:
        key = normalize_patch_name(name)
        if key not in seen:
            unique.append((name, enabled))
            seen.add(key)
    return unique


def parse_versions(output: str) -> str:
    values = []
    for raw in output.splitlines():
        line = raw.strip()
        if re.fullmatch(r"\d+(?:\.\d+)+(?:[-._A-Za-z0-9]+)?(?:\(\d+\))?", line):
            values.append(line)
    if not values:
        for raw in output.splitlines():
            line = raw.strip()
            if re.fullmatch(r"\d+(?:\.\d+)+\s+build\s+\d+", line, re.I):
                values.append(line)
    if not values:
        return "—"

    def key(value):
        parts = re.findall(r"\d+|[A-Za-z]+", value)
        return tuple(int(x) if x.isdigit() else x.lower() for x in parts)

    return max(values, key=key)


def md_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|")


def resolve_app_version(
    app: str,
    source: str,
    architecture: str,
    cli: Path,
    patches: Path,
) -> str:
    """Resolve the exact application version shown in README.

    Priority:
      1. Latest published build release for this app/source/architecture.
      2. Explicit version pinned in apps/*.json.
      3. Source-published recommended target.
      4. Morphe CLI supported-version list.
    """
    published = published_app_version(app, source, architecture).strip()
    if published:
        return published

    pinned = legacy.load_app_config_version(app).strip()
    if pinned:
        return pinned

    version = legacy.fetch_recommended_version(app, source).strip()
    if version:
        return version

    try:
        package = app_package(app)
        supported = legacy.get_supported_versions(package, str(cli), str(patches))
        if supported:
            return supported[0]
    except Exception:
        pass

    # Do not display a potentially unsupported store version in README.
    return "—"


def main():
    config = json.loads((ROOT / "patch-config.json").read_text(encoding="utf-8"))
    arch = {
        (x["app_name"], x["source"]): ", ".join(x.get("arches", []))
        for x in json.loads((ROOT / "arch-config.json").read_text(encoding="utf-8"))
    }

    cli_release = latest_release("MorpheApp", "morphe-cli")
    cli_name = next(
        a["name"] for a in cli_release.get("assets", [])
        if a["name"].lower().endswith(".jar") and "dev" not in a["name"].lower()
    )
    cli = WORK / cli_name
    if not cli.exists():
        download_asset(cli_release, ".jar", cli)

    # The same Morphe patch repository is also the shared universal source used
    # by the builder for every non-"morphe" app/source. Keep one cached MPP for
    # README generation so the generated patch list reflects the actual build
    # inputs. Only the Disable Play Store updates patch is force-enabled from
    # this universal bundle by the builder.
    universal_release = latest_release("MorpheApp", "morphe-patches")
    universal_mpp = WORK / f"morphe-universal-{universal_release['tag_name'].lstrip('v')}.mpp"
    if not universal_mpp.exists():
        download_asset(universal_release, ".mpp", universal_mpp)

    sections = [
        "# Morphe AutoBuilds",
        "",
        "## 🔄 Update Schedule",
        "- Patch Source Watcher checks every 3 hours and triggers Auto Build when a new patch-source release is detected",
        "- Manual updates available via workflow dispatch",
        "",
    ]
    failures = []

    for item in config.get("patch_list", []):
        app = item["app_name"]
        source = item["source"]
        display = APP_NAMES.get(app, app.title())
        try:
            owner, repo = source_repo(source)
            release = latest_release(owner, repo)
            mpp = WORK / f"{source}-{release['tag_name'].lstrip('v')}.mpp"
            if not mpp.exists():
                download_asset(release, ".mpp", mpp)

            package = app_package(app)
            patch_inputs = [mpp]
            if source != "morphe":
                patch_inputs.append(universal_mpp)

            patch_output = run_cli(
                cli,
                patch_inputs,
                [
                    "list-patches",
                    "--with-descriptions=false",
                    "--filter-package-name",
                    package,
                ],
            )
            rows = parse_patch_list(patch_output)
            if not rows:
                raise RuntimeError("Morphe CLI returned no patches")

            enabled_rules, disabled_rules = local_rules(app, source)
            final_rows = []
            forced_universal = {"disable play store updates"}
            for name, default_enabled in rows:
                key = normalize_patch_name(name)
                if key in forced_universal:
                    # This patch is explicitly enabled by the builder for every
                    # application, regardless of the app-specific bundle's
                    # default state.
                    applied = True
                elif key in disabled_rules:
                    applied = False
                elif key in enabled_rules:
                    applied = True
                else:
                    applied = default_enabled
                final_rows.append((name, applied))

            # Keep README app version in sync with the exact source-aware version
            # resolution used by the build planner, not a fragile CLI text parser.
            app_version = resolve_app_version(\n                app,\n                source,\n                arch.get((app, source), "—"),\n                cli,\n                mpp,\n            )
            source_version = str(release.get("tag_name", "latest")).lstrip("v")
            applied_count = sum(1 for _, value in final_rows if value)

            patch_repo_url = f"https://github.com/{owner}/{repo}"
            patch_version_link = f"[v{md_escape(source_version)}]({patch_repo_url})"

            sections += [
                f"## {display} — App v{md_escape(app_version)} — Patch v{md_escape(source_version)}",
                f"Patch source: {md_escape(source)} — v{md_escape(source_version)} ({patch_version_link})",
                f"Architecture: {md_escape(arch.get((app, source), '—'))}",
                "",
                "<details>",
                f"<summary>🩹 Patches — {applied_count}/{len(final_rows)} applied</summary>",
                "",
            ]
            for name, applied in final_rows:
                sections.append(f"- {'✅' if applied else '❌'} {md_escape(name)}")
            sections += ["", "</details>", ""]

        except Exception as exc:
            failures.append(f"{app}/{source}: {exc}")
            sections += [
                f"## {display} — —",
                f"Patch source: {source}",
                "",
                "<details>",
                "<summary>🩹 Patches — unavailable</summary>",
                "",
                f"> README refresh failed for this app: {exc}",
                "",
                "</details>",
                "",
            ]

    # Always write the README, even when one app/source cannot be inspected.
    # A single incompatible MPP must not prevent the other applications from
    # having their README entries refreshed.
    (ROOT / "README.md").write_text(
        "\n".join(sections).rstrip() + "\n",
        encoding="utf-8",
    )

    if failures:
        print("README refresh completed with warnings:")
        for failure in failures:
            print(" -", failure)


if __name__ == "__main__":
    main()
