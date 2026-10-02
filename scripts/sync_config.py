#!/usr/bin/env python3
"""Generate the existing runtime configuration from config/morphe-config.json."""

# Keep this path covered by the temporary pre-final integration trigger.
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "morphe-config.json"
PROVIDERS = {"apkmirror", "apkpure", "uptodown", "aptoide"}


def error(message: str) -> None:
    raise ValueError(f"Configuration error: {message}")


def load() -> dict:
    if not CONFIG_PATH.exists():
        error(f"missing {CONFIG_PATH.relative_to(ROOT)}")
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        error(f"invalid JSON: {exc}")
    if not isinstance(data, dict) or data.get("version") != 1:
        error("top-level object must contain version=1")
    return data


def validate(data: dict) -> None:
    apps = data.get("apps")
    sources = data.get("sources")
    builds = data.get("builds")
    if not isinstance(apps, dict) or not apps:
        error("apps must be a non-empty object")
    if not isinstance(sources, dict) or not sources:
        error("sources must be a non-empty object")
    if not isinstance(builds, list):
        error("builds must be an array")

    for app_name, app in apps.items():
        if not isinstance(app, dict):
            error(f"app {app_name!r} must be an object")
        providers = app.get("providers")
        if not isinstance(providers, dict) or not providers:
            error(f"app {app_name!r}: providers must be a non-empty object")
        for provider, cfg in providers.items():
            if provider not in PROVIDERS:
                error(f"app {app_name!r}: unsupported provider {provider!r}")
            if not isinstance(cfg, dict):
                error(f"app {app_name!r}/{provider}: config must be an object")

    for source_name, source in sources.items():
        if not isinstance(source, dict):
            error(f"source {source_name!r} must be an object")
        entries = source.get("entries")
        if not isinstance(entries, list) or len(entries) < 2:
            error(f"source {source_name!r}: entries must contain name + repository")
        if not isinstance(entries[0], dict) or not str(entries[0].get("name") or "").strip():
            error(f"source {source_name!r}: first entry must contain name")
        if not any(isinstance(e, dict) and str(e.get("repo") or "").strip() for e in entries[1:]):
            error(f"source {source_name!r}: no repository entry")

    seen = set()
    for i, build in enumerate(builds, 1):
        if not isinstance(build, dict):
            error(f"build #{i} must be an object")
        app_name = str(build.get("app_name") or "").strip()
        source = str(build.get("source") or "").strip()
        if not app_name or app_name not in apps:
            error(f"build #{i}: unknown/missing app_name")
        if not source or source not in sources:
            error(f"build #{i}: unknown/missing source")
        enabled = build.get("enabled", True)
        if not isinstance(enabled, bool):
            error(f"build {app_name}/{source}: enabled must be boolean")
        if enabled:
            key = (app_name, source)
            if key in seen:
                error(f"duplicate enabled build {app_name}/{source}")
            seen.add(key)
        arches = build.get("arches")
        if arches is not None:
            if not isinstance(arches, list) or not all(isinstance(a, str) and a.strip() for a in arches):
                error(f"build {app_name}/{source}: arches must be an array of strings")
            if len(set(arches)) != len(arches):
                error(f"build {app_name}/{source}: duplicate architecture")
        rules = build.get("patches") or {}
        if not isinstance(rules, dict):
            error(f"build {app_name}/{source}: patches must be an object")
        for field in ("enable", "disable", "options"):
            values = rules.get(field, [])
            if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
                error(f"build {app_name}/{source}: patches.{field} must be an array of strings")
        on = {x.strip().casefold() for x in rules.get("enable", [])}
        off = {x.strip().casefold() for x in rules.get("disable", [])}
        if on & off:
            error(f"build {app_name}/{source}: patch appears in both enable and disable")


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sync(data: dict) -> None:
    validate(data)
    apps = data["apps"]
    sources = data["sources"]
    builds = [b for b in data["builds"] if b.get("enabled", True)]

    write_json(ROOT / "patch-config.json", {
        "patch_list": [{"app_name": b["app_name"], "source": b["source"]} for b in builds]
    })
    write_json(ROOT / "arch-config.json", [
        {"app_name": b["app_name"], "source": b["source"], "arches": b["arches"]}
        for b in builds if b.get("arches")
    ])

    desired_apps = set()
    for app_name, app in apps.items():
        for provider, cfg in app["providers"].items():
            path = ROOT / "apps" / provider / f"{app_name}.json"
            desired_apps.add(path.resolve())
            write_json(path, cfg)

    for provider in PROVIDERS:
        directory = ROOT / "apps" / provider
        directory.mkdir(parents=True, exist_ok=True)
        for path in directory.glob("*.json"):
            if path.resolve() not in desired_apps:
                path.unlink()

    desired_sources = set()
    source_dir = ROOT / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    for source_name, source in sources.items():
        path = source_dir / f"{source_name}.json"
        desired_sources.add(path.resolve())
        write_json(path, source["entries"])
    for path in source_dir.glob("*.json"):
        if path.resolve() not in desired_sources:
            path.unlink()

    patch_dir = ROOT / "patches"
    patch_dir.mkdir(parents=True, exist_ok=True)
    desired_patches = set()
    for build in builds:
        rules = build.get("patches") or {}
        if not any(rules.get(k) for k in ("enable", "disable", "options")):
            continue
        path = patch_dir / f'{build["app_name"]}-{build["source"]}.txt'
        desired_patches.add(path.resolve())
        lines = [f"+ {x}" for x in rules.get("enable", [])]
        lines += [f"- {x}" for x in rules.get("disable", [])]
        lines += [f"@{x}" for x in rules.get("options", [])]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    for path in patch_dir.glob("*.txt"):
        if path.resolve() not in desired_patches:
            path.unlink()

    print(f"✓ Configuration valid: {len(apps)} apps, {len(sources)} sources, {len(builds)} active builds")
    print("✓ Runtime configuration synchronized")


if __name__ == "__main__":
    sync(load())
