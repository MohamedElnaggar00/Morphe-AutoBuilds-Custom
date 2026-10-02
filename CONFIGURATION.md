# Repository Configuration

All user-maintained configuration is centralized in `config/morphe-config.json`.

The following files are generated from it and should normally **not** be edited manually:

- `patch-config.json`
- `arch-config.json`
- `apps/<provider>/*.json`
- `sources/*.json`
- `patches/*.txt`

## 1. Add an application

Add the application under `apps` and put the provider-specific store configuration inside `providers`.

Example:

```json
"example-app": {
  "display_name": "Example App",
  "providers": {
    "apkmirror": {
      "org": "example-org",
      "name": "example-app",
      "type": "APK",
      "arch": "arm64-v8a",
      "dpi": "nodpi",
      "package": "com.example.app",
      "version": ""
    }
  }
}
```

You can define more than one download provider for the same app. The existing downloader logic remains responsible for provider fallback.

## 2. Add a patch source

Add a new key under `sources`.

```json
"my-patches": {
  "entries": [
    { "name": "my-patches" },
    { "user": "MorpheApp", "repo": "morphe-cli", "tag": "latest" },
    { "user": "example-user", "repo": "example-patches", "tag": "latest" }
  ]
}
```

The `entries` order is preserved exactly when `sources/my-patches.json` is generated.

Keep the CLI and patch repository declarations consistent with the actual patch source. Version/support declarations from the patch source remain authoritative at build time.

## 3. Add, disable, or remove a build

A build connects an application to a patch source:

```json
{
  "app_name": "example-app",
  "source": "my-patches",
  "arches": ["arm64-v8a"],
  "enabled": true
}
```

Use `"enabled": false` to temporarily disable a build without deleting its configuration.

When `arches` is omitted, the existing runtime default behavior is preserved.

The same application may appear more than once with different patch sources.

## 4. Customize individual patches

A build can override the patch source defaults:

```json
"patches": {
  "enable": [
    "Patch A",
    "Patch B"
  ],
  "disable": [
    "Patch C"
  ],
  "options": [
    "darkThemeColor=#181818"
  ]
}
```

The existing runtime syntax is preserved:

- `+ Patch name` → include the patch.
- `- Patch name` → exclude the patch.
- `@option=value` → pass a patch option.

Do not put the same patch in both `enable` and `disable`.

## 5. Synchronization

The **Sync Configuration** workflow runs automatically when `config/morphe-config.json` or `scripts/sync_config.py` changes. It can also be started manually.

It validates the registry and regenerates the existing runtime files used by the current build system.

The normal build/download/fallback logic is not replaced by this configuration layer.

## 6. Adding a completely new downloader

Adding a new website/provider module is different from adding an application. That requires a code change in `src/` and is not represented by the configuration registry.
