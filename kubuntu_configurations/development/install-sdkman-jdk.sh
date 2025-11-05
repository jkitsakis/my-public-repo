#!/usr/bin/env bash
set -e

echo "☕ Installing SDKMAN! for managing Java versions..."

# Ensure curl and unzip exist
sudo apt update -qq
sudo apt install -y curl zip unzip ca-certificates

# Install SDKMAN if not already installed
if [ ! -d "$HOME/.sdkman" ]; then
    echo "📥 Installing SDKMAN..."
    curl -s "https://get.sdkman.io" | bash
else
    echo "ℹ️ SDKMAN already installed, updating..."
    source "$HOME/.sdkman/bin/sdkman-init.sh"
    sdk selfupdate || true
fi

# Load SDKMAN into current shell
export SDKMAN_DIR="$HOME/.sdkman"
source "$SDKMAN_DIR/bin/sdkman-init.sh"

echo "✅ SDKMAN installed successfully."
echo "🔁 Restart your terminal or run: source ~/.sdkman/bin/sdkman-init.sh"
