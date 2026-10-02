#!/usr/bin/env python3
"""Apply safe configuration changes from the Manage Configuration workflow.

The workflow owns configuration only. It does not build APKs and does not
modify the Auto Build and Release workflow. It:
- adds an app together with a GitHub/GitLab Morphe patch source,
- edits patch overrides using patch names already present in patches/,
- deletes an app and all of its build entries,
- enables/disables all builds belonging to an app.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "morphe-config.json"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "manage-config.yml"

APP_BEGIN = "# BEGIN GENERATED APP OPTIONS"
APP_END = "# END GENERATED APP OPTIONS"
SOURCE_BEGIN = "# BEGIN GENERATED SOURCE OPTIONS"
SOURCE_END = "# END GENERATED SOURCE OPTIONS"
PATCH_BEGIN = "# BEGIN GENERATED PATCH OPTIONS"
PATCH_END = "# END GENERATED PATCH OPTIONS"

ALLOWED_ARCHES = {"arm64-v8a", "armeabi-v7a", "x86_64", "x86", "universal"}
ALLOWED_PROVIDERS = {"apkmirror", "apkpure", "uptodown", "aptoide"}
ALLOWED_PROVIDER_TYPES = {"APK", "BUNDLE"}


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


def normalize_app_name(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        fail("app name must contain only lowercase letters, digits, and single hyphens")
    return value


def normalize_package_name(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+", value):
        fail(f"invalid Android package name: {value!r}")
    return value


def normalize_source_id(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or "patch-source"


def source_identity(info: dict) -> tuple[str, str]:
    provider = info["provider"]
    if provider == "github":
        return provider, f'{info["user"]}/{info["repo"]}'.lower()
    return provider, info["project"].lower()


def parse_patch_source_url(raw_url: str) -> dict:
    raw = raw_url.strip()
    if not raw:
        fail("patch source URL is required for add-app")

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower().strip(".")
    query = parse_qs(parsed.query)

    # Morphe deep-link form.
    if host == "morphe.software" and parsed.path.rstrip("/") == "/add-source":
        candidates = []
        for provider in ("github", "gitlab"):
            values = query.get(provider) or []
            if len(values) > 1:
                fail(f"patch source URL contains multiple {provider}= values")
            if values and values[0].strip():
                candidates.append((provider, unquote(values[0].strip())))
        if len(candidates) != 1:
            fail("Morphe add-source URL must contain exactly one github= or gitlab= parameter")

        provider, identity = candidates[0]
        if provider == "github":
            parts = identity.strip("/").split("/")
            if len(parts) != 2 or not all(parts):
                fail("github= must be owner/repository")
            user, repo = parts
            repo = repo.removesuffix(".git")
            return {
                "provider": "github",
                "user": user,
                "repo": repo,
                "canonical_url": f"https://github.com/{user}/{repo}",
            }

        project = identity.strip("/")
        project = project.removesuffix(".git")
        if len(project.split("/")) < 2 or any(not p for p in project.split("/")):
            fail("gitlab= must be a GitLab project path such as group/repository")
        return {
            "provider": "gitlab",
            "project": project,
            "canonical_url": f"https://gitlab.com/{project}",
        }

    if host == "github.com":
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 2 or not all(parts):
            fail("GitHub patch source URL must be https://github.com/owner/repository")
        user, repo = parts
        repo = repo.removesuffix(".git")
        return {
            "provider": "github",
            "user": user,
            "repo": repo,
            "canonical_url": f"https://github.com/{user}/{repo}",
        }

    if host == "gitlab.com":
        project = parsed.path.strip("/")
        project = project.removesuffix(".git")
        if len(project.split("/")) < 2 or any(not p for p in project.split("/")):
            fail("GitLab patch source URL must point to a project")
        return {
            "provider": "gitlab",
            "project": project,
            "canonical_url": f"https://gitlab.com/{project}",
        }

    fail(
        "unsupported patch source URL; use a GitHub/GitLab repository URL "
        "or a Morphe add-source link with github= or gitlab="
    )


def choose_source_id(data: dict, info: dict) -> tuple[str, bool]:
    identity = source_identity(info)
    sources = data.setdefault("sources", {})

    for source_id, source in sources.items():
        entries = source.get("entries") if isinstance(source, dict) else None
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            provider = (entry.get("provider") or "github").strip().lower()
            if provider == "github" and entry.get("user") and entry.get("repo"):
                current = ("github", f'{entry["user"]}/{entry["repo"]}'.lower())
            elif provider == "gitlab" and entry.get("project"):
                current = ("gitlab", str(entry["project"]).lower())
            else:
                continue
            if current == identity:
                return source_id, True

    if info["provider"] == "github":
        base = normalize_source_id(info["repo"])
    else:
        base = normalize_source_id(info["project"].split("/")[-1])

    if base not in sources:
        return base, False

    digest = hashlib.sha256(
        f'{info["provider"]}:{identity[1]}'.encode("utf-8")
    ).hexdigest()[:8]
    candidate = f"{base}-{digest}"
    while candidate in sources:
        digest = hashlib.sha256((candidate + identity[1]).encode("utf-8")).hexdigest()[:8]
        candidate = f"{base}-{digest}"
    return candidate, False


def build_source_entries(source_id: str, info: dict) -> list[dict]:
    entries: list[dict] = [
        {"name": source_id, "url": info["canonical_url"]},
        {"user": "MorpheApp", "repo": "morphe-cli", "tag": "latest"},
    ]
    if info["provider"] == "github":
        entries.append(
            {
                "provider": "github",
                "user": info["user"],
                "repo": info["repo"],
                "tag": "latest",
            }
        )
    else:
        entries.append(
            {
                "provider": "gitlab",
                "project": info["project"],
                "tag": "latest",
            }
        )
    return entries


def find_build(data: dict, app_name: str, source: str) -> dict | None:
    for build in data.get("builds", []):
        if (
            isinstance(build, dict)
            and build.get("app_name") == app_name
            and build.get("source") == source
        ):
            return build
    return None


def require_app(data: dict, app_name: str) -> dict:
    app = data.get("apps", {}).get(app_name)
    if not isinstance(app, dict):
        fail(f"unknown app {app_name!r}")
    return app


def require_build(data: dict, app_name: str, source: str) -> dict:
    require_app(data, app_name)
    build = find_build(data, app_name, source)
    if build is None:
        fail(f"no build entry exists for {app_name}/{source}")
    return build


def normalize_architecture(value: str) -> str:
    architecture = value.strip()
    if architecture not in ALLOWED_ARCHES:
        fail(f"unsupported architecture {architecture!r}")
    return architecture


def add_app(data: dict, args: argparse.Namespace) -> None:
    if args.app_name.strip() != "__NEW_APP__":
        fail("add-app requires Application = __NEW_APP__; fill New application ID instead")

    app_name = normalize_app_name(args.new_app_name)
    display_name = args.display_name.strip()
    package_name = normalize_package_name(args.package_name)
    provider = args.provider.strip().lower()
    provider_ref = args.provider_ref.strip()
    provider_type = (args.provider_type.strip() or "APK").upper()
    dpi = args.dpi.strip() or "nodpi"
    architecture = normalize_architecture(args.architecture)

    if not display_name:
        fail("display name is required")
    if provider not in ALLOWED_PROVIDERS:
        fail(f"unsupported provider {provider!r}")
    if not provider_ref:
        fail("provider reference must not be empty")
    if provider_type not in ALLOWED_PROVIDER_TYPES:
        fail("provider type must be APK or BUNDLE")

    apps = data.setdefault("apps", {})
    if app_name in apps:
        fail(f"app {app_name!r} already exists")

    source_info = parse_patch_source_url(args.patch_source_url)
    source_id, reused = choose_source_id(data, source_info)

    if reused:
        print(f"✓ Reusing existing patch source: {source_id}")
    else:
        data["sources"][source_id] = {"entries": build_source_entries(source_id, source_info)}
        print(
            f"✓ Added patch source {source_id}: "
            f"{source_info['canonical_url']} (latest stable)"
        )

    if provider == "apkmirror":
        parts = provider_ref.split("/", 1)
        if len(parts) != 2 or not all(parts):
            fail("APKMirror provider reference must be org/name")
        org, name = parts
        cfg = {
            "org": org,
            "name": name,
            "type": provider_type,
            "arch": architecture,
            "dpi": dpi,
            "package": package_name,
            "version": "",
        }
    else:
        cfg = {
            "name": provider_ref,
            "package": package_name,
            "version": "",
        }

    apps[app_name] = {
        "display_name": display_name,
        "providers": {provider: cfg},
    }

    # No explicit patch overrides are created. Morphe therefore uses its
    # curated/default selection, and future source updates can change it.
    data.setdefault("builds", []).append(
        {
            "app_name": app_name,
            "source": source_id,
            "arches": [architecture],
            "enabled": True,
        }
    )
    print(
        f"✓ Added {app_name}/{source_id} with Morphe's default patch selection "
        "(no explicit overrides)"
    )


def normalize_patch_rules(build: dict) -> dict:
    rules = build.get("patches")
    if rules is None:
        rules = {}
        build["patches"] = rules
    if not isinstance(rules, dict):
        fail("build patches must be an object")
    for key in ("enable", "disable", "options"):
        values = rules.setdefault(key, [])
        if not isinstance(values, list):
            fail(f"patches.{key} must be an array")
    return rules


def read_patch_names(app_name: str, source: str) -> list[str]:
    path = ROOT / "patches" / f"{app_name}-{source}.txt"
    if not path.exists():
        fail(
            f"no patch selection file exists for {app_name}/{source}; "
            "this build currently uses Morphe defaults and has no explicit "
            "patch override list to edit"
        )

    names: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("@"):
            continue
        if line[:1] in {"+", "-"}:
            name = line[1:].strip()
            key = name.casefold()
            if name and key not in seen:
                names.append(name)
                seen.add(key)
    if not names:
        fail(f"patch selection file is empty: {path.relative_to(ROOT)}")
    return names


def parse_patch_choice(value: str) -> tuple[str, str, str]:
    if value == "__NONE__":
        return "", "", ""
    if " — " not in value:
        fail("invalid patch dropdown value")
    target, patch = value.split(" — ", 1)
    if " / " not in target:
        fail("invalid patch dropdown target")
    app_name, source = target.split(" / ", 1)
    app_name, source, patch = app_name.strip(), source.strip(), patch.strip()
    if not app_name or not source or not patch:
        fail("invalid patch dropdown value")
    return app_name, source, patch


def edit_patches(data: dict, args: argparse.Namespace) -> None:
    app_name = args.app_name.strip()
    if app_name == "__NEW_APP__":
        fail("edit-patches requires an existing application")
    require_app(data, app_name)

    source = args.source.strip()
    if source == "__AUTO__":
        matches = [
            b for b in data.get("builds", [])
            if isinstance(b, dict) and b.get("app_name") == app_name
        ]
        if len(matches) != 1:
            fail("source must be selected explicitly when an app has multiple build/source entries")
        source = str(matches[0].get("source") or "").strip()
    require_build(data, app_name, source)

    patch_app, patch_source, patch_name = parse_patch_choice(args.patch)
    if patch_app != app_name or patch_source != source:
        fail(
            f"selected patch belongs to {patch_app}/{patch_source}, "
            f"but target is {app_name}/{source}"
        )

    available = read_patch_names(app_name, source)
    if patch_name.casefold() not in {name.casefold() for name in available}:
        fail(f"patch {patch_name!r} is not present in patches/{app_name}-{source}.txt")

    action = args.patch_action.strip().lower()
    if action not in {"add", "exclude", "reset"}:
        fail("patch action must be add, exclude, or reset")

    rules = normalize_patch_rules(require_build(data, app_name, source))
    enabled = list(rules["enable"])
    disabled = list(rules["disable"])

    def remove_case_insensitive(items: list[str], target: str) -> list[str]:
        return [x for x in items if x.strip().casefold() != target.casefold()]

    if action == "add":
        disabled = remove_case_insensitive(disabled, patch_name)
        if patch_name.casefold() not in {x.strip().casefold() for x in enabled}:
            enabled.append(patch_name)
        print(f"✓ Enabled patch override: {app_name}/{source} — {patch_name}")
    elif action == "exclude":
        enabled = remove_case_insensitive(enabled, patch_name)
        if patch_name.casefold() not in {x.strip().casefold() for x in disabled}:
            disabled.append(patch_name)
        print(f"✓ Added patch exception: {app_name}/{source} — {patch_name}")
    else:
        enabled = remove_case_insensitive(enabled, patch_name)
        disabled = remove_case_insensitive(disabled, patch_name)
        print(f"✓ Reset patch to Morphe default: {app_name}/{source} — {patch_name}")

    if enabled or disabled or rules["options"]:
        rules["enable"] = enabled
        rules["disable"] = disabled
        rules["options"] = list(rules["options"])
    else:
        require_build(data, app_name, source).pop("patches", None)


def delete_app(data: dict, args: argparse.Namespace) -> None:
    app_name = args.app_name.strip()
    if app_name == "__NEW_APP__":
        fail("delete-app requires an existing application")
    require_app(data, app_name)
    if not args.confirm_delete:
        fail("delete-app requires confirm_delete=true")

    data["apps"].pop(app_name, None)
    before = len(data.get("builds", []))
    data["builds"] = [
        b for b in data.get("builds", [])
        if not (isinstance(b, dict) and b.get("app_name") == app_name)
    ]
    removed = before - len(data["builds"])
    print(f"✓ Deleted {app_name}; removed {removed} build entr{'y' if removed == 1 else 'ies'}")


def set_app_status(data: dict, args: argparse.Namespace) -> None:
    app_name = args.app_name.strip()
    if app_name == "__NEW_APP__":
        fail("set-app-status requires an existing application")
    require_app(data, app_name)

    status = args.status.strip().lower()
    if status not in {"enabled", "disabled"}:
        fail("status must be enabled or disabled")

    matches = [
        b for b in data.get("builds", [])
        if isinstance(b, dict) and b.get("app_name") == app_name
    ]
    if not matches:
        fail(f"app {app_name!r} has no build entries")

    enabled = status == "enabled"
    for build in matches:
        build["enabled"] = enabled
    print(
        f"✓ Set {app_name} build status to {status} "
        f"for {len(matches)} build entr{'y' if len(matches) == 1 else 'ies'}"
    )


def option_line(value: str) -> str:
    return "- " + json.dumps(value, ensure_ascii=False)


def generated_app_options(data: dict) -> str:
    values = ["__NEW_APP__"] + sorted(
        str(name) for name in data.get("apps", {}) if str(name).strip()
    )
    return "\n".join(option_line(v) for v in values)


def generated_source_options(data: dict) -> str:
    values = ["__AUTO__"] + sorted(
        str(name) for name in data.get("sources", {}) if str(name).strip()
    )
    return "\n".join(option_line(v) for v in values)


def patch_choice_values(data: dict) -> list[str]:
    values = ["__NONE__"]
    seen = {"__NONE__"}

    for build in data.get("builds", []):
        if not isinstance(build, dict):
            continue
        app_name = str(build.get("app_name") or "").strip()
        source = str(build.get("source") or "").strip()
        path = ROOT / "patches" / f"{app_name}-{source}.txt"
        if not app_name or not source or not path.exists():
            continue

        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("@") or line[:1] not in {"+", "-"}:
                continue
            patch = line[1:].strip()
            if not patch:
                continue
            value = f"{app_name} / {source} — {patch}"
            if value not in seen:
                values.append(value)
                seen.add(value)
    return values


def generated_patch_options(data: dict) -> str:
    return "\n".join(option_line(v) for v in patch_choice_values(data))


def replace_marked_block(text: str, begin: str, end: str, body: str) -> str:
    pattern = re.compile(
        rf"(?ms)^(\s*){re.escape(begin)}\n.*?^(\s*){re.escape(end)}$"
    )
    match = pattern.search(text)
    if not match:
        fail(f"workflow marker block not found: {begin}")
    indent = match.group(1)
    indented_body = body.replace("\n", "\n" + indent)
    replacement = f"{indent}{begin}\n{indent}{indented_body}\n{indent}{end}"
    return text[:match.start()] + replacement + text[match.end():]


def refresh_workflow_choices(data: dict) -> None:
    if not WORKFLOW_PATH.exists():
        fail(f"missing {WORKFLOW_PATH.relative_to(ROOT)}")
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    text = replace_marked_block(text, APP_BEGIN, APP_END, generated_app_options(data))
    text = replace_marked_block(text, SOURCE_BEGIN, SOURCE_END, generated_source_options(data))
    text = replace_marked_block(text, PATCH_BEGIN, PATCH_END, generated_patch_options(data))
    WORKFLOW_PATH.write_text(text, encoding="utf-8")
    print("✓ Refreshed Manage Configuration dropdown choices")


def validate_args(args: argparse.Namespace) -> None:
    if args.operation == "add-app":
        if args.patch_source_url.strip() == "":
            fail("add-app requires patch_source_url")
        return

    if args.app_name.strip() == "__NEW_APP__":
        fail(f"{args.operation} requires an existing application selected in the dropdown")

    if args.operation == "edit-patches":
        if args.patch == "__NONE__":
            fail("edit-patches requires a patch selected from patches/")
        if args.patch_action not in {"add", "exclude", "reset"}:
            fail("invalid patch action")
        return

    if args.operation == "delete-app":
        if not args.confirm_delete:
            fail("delete-app requires confirm_delete=true")
        return

    if args.operation == "set-app-status":
        return

    fail(f"unsupported operation {args.operation!r}")


def edit(data: dict, args: argparse.Namespace) -> None:
    if args.operation == "add-app":
        add_app(data, args)
    elif args.operation == "edit-patches":
        edit_patches(data, args)
    elif args.operation == "delete-app":
        delete_app(data, args)
    elif args.operation == "set-app-status":
        set_app_status(data, args)
    else:
        fail(f"unsupported operation {args.operation!r}")


def validate_and_sync() -> None:
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import sync_config
    sync_config.sync(sync_config.load())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--operation",
        required=True,
        choices=["add-app", "edit-patches", "delete-app", "set-app-status"],
    )
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--new-app-name", default="")
    parser.add_argument("--display-name", default="")
    parser.add_argument("--package-name", default="")
    parser.add_argument("--provider", default="apkmirror")
    parser.add_argument("--provider-ref", default="")
    parser.add_argument("--patch-source-url", default="")
    parser.add_argument("--provider-type", default="APK")
    parser.add_argument("--dpi", default="nodpi")
    parser.add_argument("--architecture", default="arm64-v8a")
    parser.add_argument("--source", default="__AUTO__")
    parser.add_argument("--patch-action", default="add")
    parser.add_argument("--patch", default="__NONE__")
    parser.add_argument("--status", default="disabled")
    parser.add_argument("--confirm-delete", action="store_true")
    args = parser.parse_args()

    validate_args(args)
    data = load()
    edit(data, args)
    save(data)
    refresh_workflow_choices(data)
    validate_and_sync()
    print("✓ Manage Configuration change applied; Auto Build workflow logic was not modified.")


if __name__ == "__main__":
    main()
