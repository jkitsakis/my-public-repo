#!/usr/bin/env bash
set -euo pipefail

# --- JetBrains Toolbox installation (official) ---
echo "🧰 Installing JetBrains Toolbox App..."

# Ensure required tools are available
for cmd in curl wget tar; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "❌ Error: $cmd is not installed. Please install it first."
    exit 1
  fi
done

# Create JetBrains apps folder
INSTALL_DIR="$HOME/.local/share/JetBrains"
mkdir -p "$INSTALL_DIR"

# Create a temporary directory
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# Get the latest Toolbox release URL
TOOLBOX_URL="$(curl -s 'https://data.services.jetbrains.com/products/releases?code=TBA&latest=true&type=release' \
  | grep -oP '(?<="linux":\{"link":")[^"]*' || true)"

if [[ -z "$TOOLBOX_URL" ]]; then
  echo "❌ Failed to retrieve JetBrains Toolbox download URL."
  exit 1
fi

# Download Toolbox tarball
echo "⬇️  Downloading Toolbox from:"
echo "   $TOOLBOX_URL"
wget -qO "$TMP_DIR/jetbrains-toolbox.tar.gz" "$TOOLBOX_URL"

# Extract tarball
tar -xzf "$TMP_DIR/jetbrains-toolbox.tar.gz" -C "$INSTALL_DIR"

# Directly specify the toolbox binary location
TOOLBOX_BINARY="$INSTALL_DIR/jetbrains-toolbox-3.0.1.59888/bin/jetbrains-toolbox"

# Check if the binary exists and is executable
if [[ ! -x "$TOOLBOX_BINARY" ]]; then
  echo "❌ Toolbox binary not found or not executable. Found these files instead:"
  find "$INSTALL_DIR" -type f -name "jetbrains-toolbox*"
  exit 1
fi

# Launch Toolbox (will handle adding itself to autostart/menu)
echo "🚀 Launching JetBrains Toolbox..."
nohup "$TOOLBOX_BINARY" >/dev/null 2>&1 & disown

echo "🎉 JetBrains Toolbox installed successfully!"
echo "📦 Use it to install and auto-update IntelliJ IDEA, PyCharm, WebStorm, CLion, and more."
