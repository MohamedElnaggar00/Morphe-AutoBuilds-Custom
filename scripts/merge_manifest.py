#!/usr/bin/env python3
"""Merge per-build records into the final manifest.json before uploading.

Inputs:
  - new_manifest.json      (planning-time manifest, has all entries with old apk
                            filenames as fallback for carry-overs)
  - build_records/*.json   (one record per built APK, written by record_build.py)

Output:
  - manifest.json          (final manifest to attach to the release)
"""
import json
import sys
from pathlib import Path


def main() -> int:
    new_manifest_path = Path("new_manifest.json")
    if not new_manifest_path.exists():
        print("No new_manifest.json found; nothing to merge")
        return 0

    with new_manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    entries = manifest.setdefault("entries", {})

    rec_dir = Path("build_records")
    if rec_dir.exists():
        for rec_file in sorted(rec_dir.rglob("*.json")):
            try:
                with rec_file.open("r", encoding="utf-8") as f:
                    rec = json.load(f)
            except Exception as e:
                print(f"  skip bad record {rec_file}: {e}")
                continue
            key = rec.get("key")
            apk = rec.get("apk", "")
            # resolved_version is the version actually embedded in the built APK
            # filename (extracted by record_build.py). Propagate it as
            # 'built_version' so check_app_updates.py can detect on the next run
            # when a newer app version becomes available, even for apps whose
            # config 'version' is empty (meaning "latest at build time").
            resolved_version = (rec.get("resolved_version") or "").strip()
            if not key:
                continue
            entry = entries.get(key)
            if not entry:
                # Record exists but planning didn't list this combo; create it.
                entry = {
                    "app_name": rec.get("app_name", ""),
                    "source": rec.get("source", ""),
                    "arch": rec.get("arch", "universal"),
                    "config_version": "",
                    "config_sig": "",
                    "source_sig": "",
                    "apk": "",
                    "built_version": "",
                }
                entries[key] = entry
            if apk:
                entry["apk"] = apk
            if resolved_version:
                entry["built_version"] = resolved_version
            # Promote pending_config_sig/config_sig and pending_source_sig
            # only after the build succeeded. This prevents a failed build from
            # consuming a configuration or patch-source change.
            pending_config_sig = entry.get("pending_config_sig", "")
            if pending_config_sig:
                entry["config_sig"] = pending_config_sig
                del entry["pending_config_sig"]

            # Promote pending_source_sig -> source_sig now that the build
            # succeeded.  The planner deliberately keeps the OLD source_sig
            # for rebuild entries so that a failed build doesn't "consume"
            # the signature change.  Only a successful build (= this code
            # path) finalises the new signature.
            pending_sig = entry.get("pending_source_sig", "")
            if pending_sig:
                entry["source_sig"] = pending_sig
                del entry["pending_source_sig"]
            print(f"  merged {key} -> apk={apk!r} built_version={resolved_version!r}")
    # Do not promote pending signatures for entries whose build did not
    # produce a record. They remain in new_manifest only transiently; removing
    # them keeps the release manifest clean while preserving the old signature
    # so the next planner run will retry.
    for entry in entries.values():
        entry.pop("pending_config_sig", None)
        entry.pop("pending_source_sig", None)

    with open("manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"Wrote manifest.json with {len(entries)} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
