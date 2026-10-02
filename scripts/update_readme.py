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
from src import utils as builder_utils

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
}


def load_registry() -> dict:
    path = ROOT / "config" / "morphe-config.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def app_display_name(app: str, registry: dict) -> str:
    value = registry.get("apps", {}).get(app, {}).get("display_name")
    return str(value).strip() if value else APP_NAMES.get(app, app.title())

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
    url = asset.get("url") or asset.get("browser_download_url") or asset.get("direct_asset_url")
    if not url:
        raise RuntimeError(f"Asset {asset.get('name', suffix)} has no download URL")
    r = requests.get(url, headers=headers, timeout=120)
    r.raise_for_status()
    output.write_bytes(r.content)
    return asset["name"]


def source_repo(source: str) -> dict:
    data = json.loads((ROOT / "sources" / f"{source}.json").read_text(encoding="utf-8"))
    repos = [
        x for x in data[1:]
        if isinstance(x, dict)
        and (
            (x.get("user") and x.get("repo"))
            or x.get("project")
        )
        and str(x.get("repo") or "").lower() != "morphe-cli"
    ]
    if not repos:
        raise RuntimeError(f"No patch repository found for source {source}")
    return repos[-1]


def latest_source_release(entry: dict) -> dict:
    provider = str(entry.get("provider") or "github").strip().lower()
    if provider == "gitlab":
        return builder_utils.detect_release(entry)
    return latest_release(str(entry["user"]), str(entry["repo"]))


def source_repo_url(entry: dict) -> str:
    provider = str(entry.get("provider") or "github").strip().lower()
    if provider == "gitlab":
        return f"https://gitlab.com/{entry['project']}"
    return f"https://github.com/{entry['user']}/{entry['repo']}"


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


def run_cli(cli: Path, patches: Path, args: list[str]) -> str:
    cmd = ["java", "-jar", str(cli), *args, "--patches", str(patches)]
    result = subprocess.run(cmd, cwd=WORK, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stdout + "\n" + result.stderr)
    return result.stdout


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


def resolve_app_version(app: str, source: str, cli: Path, patches: Path) -> str:
    """Resolve the exact stable source target using the same policy as the builder."""
    package = app_package(app)
    try:
        version = builder_utils.get_source_recommended_version(package, source).strip()
    except Exception:
        version = ""

    if version:
        return version

    # Some patch sources do not publish a readable patches-list.json. In
    # that case use the same Morphe CLI compatibility query the legacy
    # planner uses, without inventing a store-latest version.
    try:
        supported = builder_utils.get_supported_versions(package, str(cli), str(patches))
        if supported:
            return supported[0]
    except Exception:
        pass

    version = legacy.fetch_recommended_version(app, source).strip()
    if version:
        return version

    # Do not display a potentially unsupported store version in README.
    return "—"


def main():
    config = json.loads((ROOT / "patch-config.json").read_text(encoding="utf-8"))
    registry = load_registry()
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

    sections = [
        "# Morphe AutoBuilds",
        "",
        "## 🔄 Update Schedule",
        "- Automatic updates daily at 9:17 AM Africa/Cairo time",
        "- Manual updates available via workflow dispatch",
        "",
        "## ⚙️ Configuration",
        "Edit [config/morphe-config.json](config/morphe-config.json) to add apps, sources, build entries, or patch selections.",
        "Generated runtime files are synchronized by the **Sync Configuration** workflow.",
        "",
        "## 🛠️ Manage Configuration",
        "",
        "Use **Actions → Manage Configuration → Run workflow** to change the repository configuration without editing JSON files manually. The workflow has four operations:",
        "",
        "### 1. Add Application",
        "",
        "Use this when adding a new app for the first time. Select **add-app**, then fill these fields:",
        "",
        "| Field | What to enter | Example |",
        "|---|---|---|",
        "| **Application** | Keep **__NEW_APP__** | <code>__NEW_APP__</code> |",
        "| **New application ID** | Lowercase app ID; letters, numbers, and hyphens only | <code>truecaller</code> |",
        "| **Display name** | Human-readable app name | <code>Truecaller</code> |",
        "| **Package name** | Android package name | <code>com.truecaller</code> |",
        "| **Provider** | Store/source used to obtain the original app | <code>apkmirror</code> |",
        "| **Provider reference** | For APKMirror use <code>org/name</code>; for other providers use the provider's app name | <code>truecaller/truecaller</code> |",
        "| **Provider type** | <code>APK</code> for a standalone APK, <code>BUNDLE</code> for a native split bundle such as APKM | <code>BUNDLE</code> |",
        "| **DPI** | APKMirror DPI when required; normally <code>nodpi</code> | <code>nodpi</code> |",
        "| **Architecture** | Target build architecture | <code>arm64-v8a</code> |",
        "| **Patch Source URL** | GitHub/GitLab patch repository URL, or a Morphe add-source link | <code>https://morphe.software/add-source?gitlab=Paresh-Maheshwari/paresh-patches</code> |",
        "",
        "The patch source is read during registration and the source's package-specific Morphe default patch selection is materialized into <code>patches/&lt;app&gt;-&lt;source&gt;.txt</code>. You do not need to manually enter individual patches when adding the app.",
        "",
        "**Example — Truecaller:** set Application to <code>__NEW_APP__</code>, New application ID to <code>truecaller</code>, Display name to <code>Truecaller</code>, Package name to <code>com.truecaller</code>, choose the correct original-app provider, set the correct provider reference and artifact type, choose <code>arm64-v8a</code>, and enter the Truecaller patch-source URL.",
        "",
        "### 2. Edit Patch Selection",
        "",
        "Use this to change the patches used for an app that is already configured. The patch choices are read from the committed <code>patches/</code> directory.",
        "",
        "| Field | What to enter |",
        "|---|---|",
        "| **Application** | Select the existing app you want to change |",
        "| **Source** | Select the patch source used by that app; use <code>__AUTO__</code> when the app has only one build/source entry |",
        "| **Patch Action** | <code>add</code> = force-enable, <code>exclude</code> = disable/exception, <code>reset</code> = remove the override and return to the source default |",
        "| **Patch** | Select the exact patch from the dropdown. Each item is shown as <code>app / source — patch</code> |",
        "",
        "The workflow verifies that the selected patch belongs to the selected application/source before changing the configuration.",
        "",
        "### 3. Delete Application",
        "",
        "Use this to completely remove an application from the build configuration.",
        "",
        "| Field | What to enter |",
        "|---|---|",
        "| **Application** | Select the app to delete |",
        "| **Confirm delete** | Must be enabled (<code>true</code>) |",
        "",
        "Deletion removes the app and all of its build entries. A shared patch source is not deleted automatically because another app may still use it. Generated runtime files are synchronized after the change.",
        "",
        "### 4. Enable / Disable Application",
        "",
        "Use **set-app-status** to control whether an application's configured build entries participate in automatic builds.",
        "",
        "| Field | What to enter |",
        "|---|---|",
        "| **Application** | Select the existing app |",
        "| **Status** | <code>enabled</code> or <code>disabled</code> |",
        "",
        "For an app with multiple patch sources/build entries, the status is applied to all of that app's build entries.",
        "",
        "### Important Notes",
        "",
        "- **Manage Configuration changes configuration only. It does not build APKs.** After a configuration change is committed, the normal **Auto Build and Release Morphe** workflow handles building and publishing according to the existing build logic.",
        "- Patch selections are maintained in <code>patches/</code>. The GUI dropdown is generated from those committed files, so only patches already represented there can be selected in **Edit Patch Selection**.",
        "- When no patch override is needed, keep the app/source at the Morphe default selection. Use **reset** to remove an explicit override.",
        "",
    ]
    failures = []

    for item in config.get("patch_list", []):
        app = item["app_name"]
        source = item["source"]
        display = app_display_name(app, registry)
        try:
            source_entry = source_repo(source)
            release = latest_source_release(source_entry)
            mpp = WORK / f"{source}-{release['tag_name'].lstrip('v')}.mpp"
            if not mpp.exists():
                download_asset(release, ".mpp", mpp)

            package = app_package(app)
            patch_output = run_cli(
                cli,
                mpp,
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
            for name, default_enabled in rows:
                key = normalize_patch_name(name)
                if key in disabled_rules:
                    applied = False
                elif key in enabled_rules:
                    applied = True
                else:
                    applied = default_enabled
                final_rows.append((name, applied))

            # Keep README app version in sync with the exact source-aware version
            # resolution used by the build planner, not a fragile CLI text parser.
            app_version = resolve_app_version(app, source, cli, mpp)
            source_version = str(release.get("tag_name", "latest")).lstrip("v")
            applied_count = sum(1 for _, value in final_rows if value)

            patch_repo_url = source_repo_url(source_entry)
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

    if failures:
        print("README refresh failed:")
        for failure in failures:
            print(" -", failure)
        raise SystemExit(1)

    (ROOT / "README.md").write_text(
        "\n".join(sections).rstrip() + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
