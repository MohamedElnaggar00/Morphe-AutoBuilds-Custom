#!/usr/bin/env python3
"""Prepare and verify the build-time Morphe CLI and gplaydl versions.

The build is intentionally not pinned to a specific Morphe CLI release:
sources/* uses tag=latest, and src.downloader resolves that release at build
time. gplaydl is installed from the latest stable PyPI release before every
build.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
PYPI_URL = "https://pypi.org/pypi/gplaydl/json"
GITHUB_API = "https://api.github.com"

HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "Morphe-AutoBuilds-Custom-build-tools",
}


def version_key(value: str) -> tuple:
    text = str(value).strip().lstrip("vV")
    nums = tuple(int(x) for x in re.findall(r"\d+", text))
    pre = 1 if re.search(r"(?:dev|alpha|beta|rc)", text, re.I) else 0
    return nums, -pre


def latest_gplaydl() -> str:
    response = requests.get(PYPI_URL, timeout=30)
    response.raise_for_status()
    data = response.json()
    version = str(data.get("info", {}).get("version") or "").strip()
    if not version:
        raise RuntimeError("PyPI did not return gplaydl latest version")
    return version


def installed_gplaydl() -> str:
    try:
        from importlib.metadata import version

        return version("gplaydl")
    except Exception:
        return ""


def install_gplaydl(version: str) -> None:
    print(f"Installing gplaydl=={version} ...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", f"gplaydl=={version}"],
        check=True,
    )


def morphe_cli_sources() -> list[tuple[str, str, str]]:
    sources_dir = ROOT / "sources"
    found: dict[tuple[str, str], tuple[str, str, str]] = {}

    for path in sorted(sources_dir.glob("*.json")):
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(entries, list):
            continue

        for entry in entries[1:]:
            if not isinstance(entry, dict):
                continue
            repo = str(entry.get("repo") or "").strip()
            if repo.lower() != "morphe-cli":
                continue

            user = str(entry.get("user") or "").strip()
            tag = str(entry.get("tag") or "latest").strip() or "latest"
            if not user:
                raise RuntimeError(f"{path.name}: morphe-cli entry is missing user")

            key = (user.lower(), repo.lower())
            found[key] = (path.stem, f"{user}/{repo}", tag)

    return sorted(found.values())


def latest_morphe_cli(repo: str) -> dict:
    response = requests.get(
        f"{GITHUB_API}/repos/{repo}/releases/latest",
        headers=HEADERS,
        timeout=30,
        allow_redirects=True,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected release response for {repo}")
    return data


def verify_morphe_cli() -> list[tuple[str, str]]:
    results = []
    sources = morphe_cli_sources()
    if not sources:
        raise RuntimeError("No Morphe CLI source entries were found in sources/")

    for source_name, repo, requested_tag in sources:
        if requested_tag != "latest":
            raise RuntimeError(
                f"{source_name}: Morphe CLI must use tag=latest for automatic updates; "
                f"found {requested_tag!r}"
            )

        release = latest_morphe_cli(repo)
        tag = str(release.get("tag_name") or "").strip()
        assets = release.get("assets") or []
        cli_assets = [
            str(asset.get("name") or "")
            for asset in assets
            if str(asset.get("name") or "").lower().endswith(".jar")
            and "morphe" in str(asset.get("name") or "").lower()
        ]

        if not tag:
            raise RuntimeError(f"{repo}: latest release has no tag_name")
        if not cli_assets:
            raise RuntimeError(f"{repo}: latest release has no Morphe CLI .jar asset")

        results.append((repo, tag))
        print(
            f"Morphe CLI: {repo} -> latest stable {tag} "
            f"(asset: {cli_assets[0]})"
        )

    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install the latest stable gplaydl into the current Python environment.",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only verify versions; do not install or change packages.",
    )
    args = parser.parse_args()

    if args.install and args.check_only:
        parser.error("--install and --check-only are mutually exclusive")

    latest = latest_gplaydl()
    current = installed_gplaydl()
    print(f"gplaydl: installed={current or 'not installed'}, latest={latest}")

    if args.install:
        if current != latest:
            install_gplaydl(latest)
        else:
            print("gplaydl is already at the latest stable release.")
        current = installed_gplaydl()

    if not current:
        if args.install:
            raise RuntimeError("gplaydl installation did not complete")
        print(
            "gplaydl is not installed in this check-only environment; "
            "latest stable availability was verified above."
        )
    elif version_key(current) < version_key(latest):
        raise RuntimeError(
            f"gplaydl installation is stale: installed {current}, latest {latest}"
        )

    verify_morphe_cli()

    print("Build tools verified: latest gplaydl + latest Morphe CLI sources are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
