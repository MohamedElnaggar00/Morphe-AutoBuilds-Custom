#!/usr/bin/env python3
"""Generate the repository README from patch-config.json and live patch-source metadata."""

from __future__ import annotations

import json
import os
import re
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
    "facebook": "Facebook",
}

def api_json(url: str):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()

def release_data(owner: str, repo: str) -> dict:
    return api_json(f"{API}/repos/{owner}/{repo}/releases/latest")

def download_release_asset(release: dict, asset_name: str):
    for asset in release.get("assets", []):
        if asset.get("name") == asset_name:
            headers = dict(HEADERS)
            headers["Accept"] = "application/octet-stream"
            r = requests.get(asset["url"], headers=headers, timeout=60)
            r.raise_for_status()
            return r.json() if "json" in asset.get("content_type", "").lower() else json.loads(r.content)
    return None

def patch_metadata(owner: str, repo: str):
    try:
        release = release_data(owner, repo)
        data = download_release_asset(release, "patches-list.json")
        if data is not None:
            return data
    except Exception:
        pass

    url = f"{API}/repos/{owner}/{repo}/contents/patches-list.json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        obj = r.json()
        raw_headers = dict(HEADERS)
        raw_headers["Accept"] = "application/vnd.github.raw+json"
        rr = requests.get(obj["download_url"], headers=raw_headers, timeout=60)
        rr.raise_for_status()
        return rr.json()
    except Exception:
        return None

def source_repo(source: str) -> tuple[str, str]:
    data = json.loads((ROOT / "sources" / f"{source}.json").read_text(encoding="utf-8"))
    repos = [
        x for x in data[1:]
        if x.get("user") and x.get("repo") and "morphe-cli" not in x.get("repo", "").lower()
    ]
    if not repos:
        raise RuntimeError(f"No patch repository found for source {source}")
    return repos[-1]["user"], repos[-1]["repo"]

def app_config(app_name: str) -> dict:
    for platform in ("apkmirror", "apkpure", "uptodown", "aptoide", "apkcombo"):
        p = ROOT / "apps" / platform / f"{app_name}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return {}

def selected_rules(app_name: str, source: str) -> tuple[set[str], set[str]]:
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

def compatible_with(patch: dict, package: str) -> bool:
    cps = patch.get("compatiblePackages")
    if cps is None:
        return True
    if isinstance(cps, dict):
        return package in cps
    if isinstance(cps, list):
        return any(isinstance(x, dict) and x.get("packageName") == package for x in cps)
    return False

def patch_targets(patch: dict, package: str) -> list[str]:
    versions = []
    cps = patch.get("compatiblePackages")
    if isinstance(cps, dict):
        targets = cps.get(package, [])
        for item in targets:
            if isinstance(item, str):
                versions.append(item)
            elif isinstance(item, dict) and item.get("version"):
                versions.append(str(item["version"]))
        return versions
    if not isinstance(cps, list):
        return versions
    for cp in cps:
        if not isinstance(cp, dict) or cp.get("packageName") != package:
            continue
        for target in cp.get("targets") or []:
            if isinstance(target, dict) and target.get("version"):
                versions.append(str(target["version"]))
    return versions

def version_key(value: str):
    return tuple(int(x) if x.isdigit() else x.lower()
                 for x in re.findall(r"\d+|[A-Za-z]+", value))

def highest_version(values: list[str]) -> str:
    if not values:
        return "—"
    try:
        return max(values, key=version_key)
    except Exception:
        return values[0]

def md_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|")

def generate():
    config = json.loads((ROOT / "patch-config.json").read_text(encoding="utf-8"))
    arch_config = json.loads((ROOT / "arch-config.json").read_text(encoding="utf-8"))
    arch_map = {(x["app_name"], x["source"]): ", ".join(x.get("arches", [])) for x in arch_config}

    sections = ["# Morphe AutoBuilds", ""]
    errors = []

    for item in config.get("patch_list", []):
        app = item["app_name"]
        source = item["source"]
        package = app_config(app).get("package", "")
        display = APP_NAMES.get(app, app.title())
        try:
            owner, source_repo_name = source_repo(source)
            meta = patch_metadata(owner, source_repo_name)
            if not meta:
                raise RuntimeError("patches-list.json unavailable")
            patches = [p for p in meta.get("patches", []) if compatible_with(p, package)]
            enabled, disabled = selected_rules(app, source)

            rows = []
            versions = []
            for p in patches:
                name = p.get("name", "").strip()
                if not name:
                    continue
                versions.extend(patch_targets(p, package))
                if name in disabled:
                    applied = False
                elif name in enabled:
                    applied = True
                else:
                    applied = bool(p.get("default", p.get("use", False)))
                rows.append((name, applied))

            unique = []
            seen = set()
            for name, applied in rows:
                if name not in seen:
                    unique.append((name, applied))
                    seen.add(name)

            app_version = highest_version(versions)
            source_version = str(meta.get("version", "latest")).lstrip("v")
            arch = arch_map.get((app, source), "—")
            applied_count = sum(1 for _, applied in unique if applied)

            sections += [
                f"## {display} — {md_escape(app_version)}",
                f"**Patch source:** source={md_escape(source)} — v{md_escape(source_version)}",
                f"**Architecture:** {md_escape(arch)}",
                "",
                "<details>",
                f"<summary>🩹 Patches — {applied_count}/{len(unique)} applied</summary>",
                "",
            ]
            for name, applied in unique:
                sections.append(f"- {'✅' if applied else '❌'} {md_escape(name)}")
            sections += ["", "</details>", ""]

        except Exception as exc:
            errors.append(f"{app}/{source}: {exc}")
            sections += [
                f"## {display} — —",
                f"**Patch source:** source={source}",
                "",
                "<details>",
                "<summary>🩹 Patches — unavailable</summary>",
                "",
                f"> Could not refresh patch metadata: {exc}",
                "",
                "</details>",
                "",
            ]

    if errors:
        print("README refresh completed with warnings:")
        for error in errors:
            print(" -", error)

    (ROOT / "README.md").write_text("\n".join(sections).rstrip() + "\n", encoding="utf-8")

if __name__ == "__main__":
    generate()
