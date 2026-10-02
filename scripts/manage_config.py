#!/usr/bin/env python3
"""Apply safe, common configuration changes from GitHub Actions inputs.

This is intentionally a thin editor for config/morphe-config.json. The normal
runtime/build code remains the source of behavior; this script only changes
the centralized registry and then asks sync_config.py to regenerate the files
consumed by the existing workflow.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "morphe-config.json"


def fail(message: str) -> None:
    raise SystemExit(f"Configuration error: {message}")


def load() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing {CONFIG_PATH.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON: {exc}")
    if not isinstance(data, dict) or data.get("version") != 1:
        fail("top-level object must contain version=1")
    return data


def save(data: dict) -> None:
    CONFIG_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def find_build(data: dict, app_name: str, source: str) -> dict | None:
    for build in data.get("builds", []):
        if (
            isinstance(build, dict)
            and build.get("app_name") == app_name
            and build.get("source") == source
        ):
            return build
    return None


def require_app_source(data: dict, app_name: str, source: str) -> None:
    apps = data.get("apps", {})
    sources = data.get("sources", {})
    if app_name not in apps:
        fail(f"unknown app {app_name!r}")
    if source not in sources:
        fail(f"unknown source {source!r}")



def add_app(data: dict, args: argparse.Namespace) -> None:
    app_name = args.app_name.strip()
    display_name = args.display_name.strip()
    package_name = args.package_name.strip()
    provider = args.provider.strip().lower()
    provider_ref = args.provider_ref.strip()
    source = args.source.strip()
    architecture = args.architecture.strip()
    provider_type = args.provider_type.strip()
    dpi = args.dpi.strip()
    if not app_name or not display_name or not package_name:
        fail("add-app requires app_name, display_name, and package_name")
    if app_name in data.get("apps", {}):
        fail(f"app {app_name!r} already exists")
    if provider not in {"apkmirror", "apkpure", "uptodown", "aptoide"}:
        fail(f"unsupported provider {provider!r}")
    if not provider_ref:
        fail("provider-ref must not be empty")
    if source not in data.get("sources", {}):
        fail(f"unknown source {source!r}")
    if architecture not in {"arm64-v8a", "armeabi-v7a", "x86_64", "x86", "universal"}:
        fail(f"unsupported architecture {architecture!r}")
    if provider == "apkmirror":
        parts = provider_ref.split("/", 1)
        if len(parts) != 2 or not all(parts):
            fail("APKMirror provider-ref must be org/name")
        org, name = parts
        cfg = {"org": org, "name": name, "type": provider_type or "APK", "arch": architecture, "dpi": dpi or "nodpi", "package": package_name, "version": ""}
    else:
        cfg = {"name": provider_ref, "package": package_name, "version": ""}
    data.setdefault("apps", {})[app_name] = {"display_name": display_name, "providers": {provider: cfg}}
    data.setdefault("builds", []).append({"app_name": app_name, "source": source, "arches": [architecture], "enabled": True})


def get_or_create_build(data: dict, app_name: str, source: str) -> dict:
    require_app_source(data, app_name, source)
    build = find_build(data, app_name, source)
    if build is not None:
        return build
    build = {"app_name": app_name, "source": source, "enabled": False}
    data.setdefault("builds", []).append(build)
    return build


def normalize_values(value: str) -> list[str]:
    values = [item.strip() for item in value.split(",")]
    values = [item for item in values if item]
    if not values:
        fail("value must contain at least one non-empty item")
    return values


def patch_rules(build: dict) -> dict:
    rules = build.get("patches")
    if rules is None:
        rules = {}
        build["patches"] = rules
    if not isinstance(rules, dict):
        fail("build patches must be an object")
    for key in ("enable", "disable", "options"):
        rules.setdefault(key, [])
        if not isinstance(rules[key], list):
            fail(f"patches.{key} must be an array")
    return rules


def edit(data: dict, args: argparse.Namespace) -> None:
    if args.operation == "add-app":
        add_app(data, args)
        return

    if args.operation in {
        "enable-build",
        "disable-build",
        "set-arches",
        "add-enable-patch",
        "remove-enable-patch",
        "add-disable-patch",
        "remove-disable-patch",
        "clear-patches",
    }:
        build = get_or_create_build(data, args.app_name, args.source)

    if args.operation == "enable-build":
        build["enabled"] = True
        return

    if args.operation == "disable-build":
        build["enabled"] = False
        return

    if args.operation == "set-arches":
        arches = normalize_values(args.value)
        allowed = {"arm64-v8a", "armeabi-v7a", "x86_64", "x86", "universal"}
        unknown = sorted(set(arches) - allowed)
        if unknown:
            fail(f"unsupported architecture(s): {', '.join(unknown)}")
        if len(set(arches)) != len(arches):
            fail("duplicate architecture in value")
        build["arches"] = arches
        return

    if args.operation in {
        "add-enable-patch",
        "remove-enable-patch",
        "add-disable-patch",
        "remove-disable-patch",
    }:
        patch_name = args.value.strip()
        if not patch_name:
            fail("patch name must not be empty")
        rules = patch_rules(build)
        key = (
            "enable"
            if "enable" in args.operation
            else "disable"
        )
        normalized = {str(x).strip().casefold(): x for x in rules[key]}
        needle = patch_name.casefold()

        if args.operation.startswith("add-"):
            if needle not in normalized:
                rules[key].append(patch_name)
        else:
            rules[key] = [
                x for x in rules[key]
                if str(x).strip().casefold() != needle
            ]
        return

    if args.operation == "clear-patches":
        build.pop("patches", None)
        return

    fail(f"unsupported operation {args.operation!r}")


def validate_and_sync() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import sync_config

    data = sync_config.load()
    sync_config.sync(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--operation",
        required=True,
        choices=[
            "add-app",
            "enable-build",
            "disable-build",
            "set-arches",
            "add-enable-patch",
            "remove-enable-patch",
            "add-disable-patch",
            "remove-disable-patch",
            "clear-patches",
        ],
    )
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--display-name", default="")
    parser.add_argument("--package-name", default="")
    parser.add_argument("--provider", default="apkmirror")
    parser.add_argument("--provider-ref", default="")
    parser.add_argument("--architecture", default="arm64-v8a")
    parser.add_argument("--provider-type", default="")
    parser.add_argument("--dpi", default="")
    parser.add_argument("--source", required=True)
    parser.add_argument(
        "--value",
        default="",
        help="Comma-separated architectures or one patch name, depending on operation.",
    )
    args = parser.parse_args()

    data = load()
    edit(data, args)
    save(data)

    # Run the same validator/generator used by the normal sync workflow.
    validate_and_sync()
    print(
        f"✓ Applied {args.operation} to {args.app_name}/{args.source} "
        f"and regenerated runtime configuration."
    )


if __name__ == "__main__":
    main()
