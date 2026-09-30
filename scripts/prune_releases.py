#!/usr/bin/env python3
"""Prune superseded APKs across ALL releases.

Every workflow run publishes a NEW release that contains only the APKs it
rebuilt (plus the full manifest.json). Over time older releases end up holding
APKs that have since been superseded by a newer build of the same
app/source/arch. This script removes those and then deletes releases that
became empty because of it.

Rules
-----
* Identity of an APK = ``identity_prefix`` from cleanup_old_apks.py
  (``{app}-{arch}-{source}``). Only the newest version of each identity is kept
  (newest = highest patch version, then highest app version).
* If the same APK name exists in several releases, the copy in the protected
  (just-published) release wins, otherwise the most recent release.
* Only ``.apk`` assets are ever deleted; manifest.json and any other asset
  are never touched directly.
* A release is deleted (together with its tag) ONLY if it had APKs and every
  one of them was pruned by this run. Releases that never contained APKs, and
  the protected release, are never deleted.
* Drafts are ignored.

Usage
-----
    python scripts/prune_releases.py --protect-tag v2026.09.30-42 [--dry-run]
Needs ``gh`` and GITHUB_REPOSITORY / GH_TOKEN in the environment.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cleanup_old_apks import identity_prefix, version_key  # noqa: E402

# tag -> (release_id, [(asset_id, asset_name), ...])
Releases = Dict[str, Tuple[int, List[Tuple[str, str]]]]

JQ = (
    '.[] | select(.draft | not) | '
    '("R\\t\\(.id)\\t\\(.tag_name)"), '
    '(.tag_name as $t | .assets[] | "A\\t\\($t)\\t\\(.id)\\t\\(.name)")'
)


def gh(args: List[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)


def fetch_releases(repo: str) -> Releases:
    p = gh(["api", "--paginate", f"repos/{repo}/releases?per_page=100", "--jq", JQ])
    if p.returncode != 0:
        raise RuntimeError(f"could not list releases: {(p.stderr or p.stdout).strip()[:300]}")
    releases: Releases = {}
    for line in p.stdout.splitlines():
        parts = line.split("\t")
        if parts[0] == "R" and len(parts) == 3:
            releases[parts[2]] = (int(parts[1]), [])
        elif parts[0] == "A" and len(parts) == 4 and parts[1] in releases:
            releases[parts[1]][1].append((parts[2], parts[3]))
    return releases


def plan_prune(releases: Releases, protect: Set[str]):
    """Return (assets_to_delete, releases_to_delete).

    assets_to_delete: list of (tag, asset_id, asset_name)
    releases_to_delete: list of tags
    """
    # identity -> best (rank tuple, tag, asset_id, name)
    best: Dict[str, tuple] = {}
    for tag, (rel_id, assets) in releases.items():
        for asset_id, name in assets:
            if not name.lower().endswith(".apk"):
                continue
            ident = identity_prefix(name)
            rank = (version_key(name), 1 if tag in protect else 0, rel_id)
            cur = best.get(ident)
            if cur is None or rank > cur[0]:
                best[ident] = (rank, tag, asset_id, name)

    assets_to_delete: List[Tuple[str, str, str]] = []
    releases_to_delete: List[str] = []

    for tag, (rel_id, assets) in releases.items():
        apks = [(aid, n) for aid, n in assets if n.lower().endswith(".apk")]
        if not apks:
            continue
        removed = 0
        for asset_id, name in apks:
            winner = best[identity_prefix(name)]
            if winner[1] == tag and winner[2] == asset_id:
                continue
            assets_to_delete.append((tag, asset_id, name))
            removed += 1
        if removed == len(apks) and tag not in protect:
            releases_to_delete.append(tag)

    return assets_to_delete, releases_to_delete


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--protect-tag", action="append", default=[],
                    help="release tag that must never be deleted (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if not repo:
        print("GITHUB_REPOSITORY is not set", file=sys.stderr)
        return 1

    releases = fetch_releases(repo)
    protect = {t for t in args.protect_tag if t}
    assets_to_delete, releases_to_delete = plan_prune(releases, protect)

    print(f"Scanned {len(releases)} release(s).")
    if not assets_to_delete:
        print("Nothing to prune.")
        return 0

    whole = set(releases_to_delete)
    # Releases that go away entirely are deleted in one call; only delete
    # individual assets for releases that stay.
    for tag, asset_id, name in assets_to_delete:
        if tag in whole:
            continue
        if args.dry_run:
            print(f"[dry-run] delete asset {name} from {tag}")
            continue
        p = gh(["api", "-X", "DELETE", f"repos/{repo}/releases/assets/{asset_id}"])
        print(("🗑️  deleted asset" if p.returncode == 0 else "⚠️  failed to delete asset"),
              f"{name} ({tag})")

    for tag in releases_to_delete:
        if args.dry_run:
            print(f"[dry-run] delete release {tag} (+ tag)")
            continue
        p = gh(["release", "delete", tag, "--yes", "--cleanup-tag"])
        print(("🗑️  deleted release" if p.returncode == 0 else "⚠️  failed to delete release"), tag)

    return 0


if __name__ == "__main__":
    sys.exit(main())
