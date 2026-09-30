#!/bin/bash
# Download ReVanced tools without using GitHub API

# Get latest version from GitHub releases page (no API)
get_latest_version() {
    curl -s "https://github.com/$1/releases" | grep -o 'tag/[^"]*' | head -1 | cut -d'/' -f2
}

# Download revanced-cli
CLI_VERSION=$(get_latest_version "revanced/revanced-cli")
curl -L "https://github.com/revanced/revanced-cli/releases/download/$CLI_VERSION/revanced-cli-all.jar" -o revanced-cli.jar

# Download patches
PATCHES_VERSION=$(get_latest_version "revanced/revanced-patches")
curl -L "https://github.com/revanced/revanced-patches/releases/download/$PATCHES_VERSION/patches.jar" -o patches.jar

echo "Downloaded: revanced-cli $CLI_VERSION, patches $PATCHES_VERSION"
