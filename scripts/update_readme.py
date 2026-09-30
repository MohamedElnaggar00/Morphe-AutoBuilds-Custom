#!/usr/bin/env python3
"""Generate README from the exact Morphe patch bundles used by the builder."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
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

def local_rules(app_name: str, source: str) -> tuple[set[str], set[str]]:
    p = ROOT / "patches" / f"{app_name}-{source}.txt"
    enabled, disabled = set(), set()
    if not p.exists():
        return enabled, disabled
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("+"):
            enabled.add(line[1:].strip())
        elif line.startswith("-"):
            disabled.add(line[1:].strip())
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
        if name not in seen:
            unique.append((name, enabled))
            seen.add(name)
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

    sections = [
        "# Morphe AutoBuilds",
        "",
        "## 🔄 Update Schedule",
        "- Automatic updates daily at 6:17 AM UTC",
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
                if name in disabled_rules:
                    applied = False
                elif name in enabled_rules:
                    applied = True
                else:
                    applied = default_enabled
                final_rows.append((name, applied))

            version_output = run_cli(
                cli,
                mpp,
                ["list-versions", "--filter-package-names", package],
            )
            app_version = parse_versions(version_output)
            source_version = str(release.get("tag_name", "latest")).lstrip("v")
            applied_count = sum(1 for _, value in final_rows if value)

            patch_repo_url = f"https://github.com/{owner}/{repo}"
            patch_version_link = f"[v{md_escape(source_version)}]({patch_repo_url})"

            sections += [
                f"## {display} — {md_escape(app_version)} ({patch_version_link})",
                f"Patch source: {md_escape(source)} — v{md_escape(source_version)}",
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
