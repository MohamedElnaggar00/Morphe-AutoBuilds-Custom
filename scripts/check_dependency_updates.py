#!/usr/bin/env python3
"""Report dependency updates without changing the repository automatically."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
PYPI = "https://pypi.org/pypi/gplaydl/json"

HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "Morphe-AutoBuilds-Custom-dependency-check",
}
token = os.getenv("GITHUB_TOKEN")
if token:
    HEADERS["Authorization"] = f"Bearer {token}"


def version_key(value: str) -> tuple:
    """Comparable key for common semantic-version strings."""
    text = str(value).strip().lstrip("v")
    nums = tuple(int(x) for x in re.findall(r"\d+", text))
    prerelease = 1 if re.search(r"[-.]?(?:dev|alpha|beta|rc)", text, re.I) else 0
    return nums, -prerelease


def github_json(url: str) -> tuple[dict, str]:
    response = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected GitHub response from {url}")
    return data, response.url


def main() -> int:
    print("# Morphe dependency status")
    print("")

    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    match = re.search(r"^gplaydl\s*==\s*([^\s#]+)", req, re.M | re.I)
    pinned = match.group(1) if match else "unpinned"

    try:
        pypi = requests.get(PYPI, timeout=30).json()
        latest = str(pypi["info"]["version"])
    except Exception as exc:
        print(f"- gplaydl: unable to query PyPI: {exc}")
        latest = None

    if latest:
        status = "CURRENT" if pinned != "unpinned" and version_key(pinned) >= version_key(latest) else "UPDATE AVAILABLE"
        print(f"- gplaydl: pinned={pinned}, latest={latest} -> **{status}**")

    sources_dir = ROOT / "sources"
    seen = set()
    cli_entries = []
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
            user = str(entry.get("user") or "").strip()
            repo = str(entry.get("repo") or "").strip()
            if not user or not repo or "morphe-cli" not in repo.lower():
                continue
            key = (user.lower(), repo.lower())
            if key in seen:
                continue
            seen.add(key)
            cli_entries.append((path.stem, user, repo, str(entry.get("tag") or "latest")))

    if not cli_entries:
        print("- Morphe CLI: no Morphe CLI repository entries found in sources/.")
    else:
        for source, user, repo, requested_tag in cli_entries:
            url = f"{API}/repos/{user}/{repo}/releases/latest"
            try:
                release, final_url = github_json(url)
                resolved = str(release.get("tag_name") or "?")
                print(
                    f"- Morphe CLI ({source}): source={user}/{repo}, requested={requested_tag}, "
                    f"latest stable={resolved}, endpoint={final_url}"
                )
                if "morphe-desktop" in final_url.lower() and repo.lower() != "morphe-desktop":
                    print("  - Canonical repository endpoint now resolves through morphe-desktop; review before changing the build source.")
            except Exception as exc:
                print(f"- Morphe CLI ({source}): check failed: {exc}")

    print("")
    print("No dependency is upgraded automatically by this check; changes require compatibility review first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
