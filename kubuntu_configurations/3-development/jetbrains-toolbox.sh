#!/usr/bin/env bash
set -e

# --- JetBrains Toolbox installation (official) ---
echo "🧰 Installing JetBrains Toolbox App..."

# Create apps folder
mkdir -p ~/.local/share/JetBrains

# Download the latest Toolbox tarball
TOOLBOX_URL=$(curl -s https://data.services.jetbrains.com/products/releases?code=TBA&latest=true&type=release \
  | grep -oP '(?<="linux":\{"link":")[^"]*')
wget -qO /tmp/jetbrains-toolbox.tar.gz "$TOOLBOX_URL"

# Extract and install
tar -xzf /tmp/jetbrains-toolbox.tar.gz -C ~/.local/share/JetBrains
TOOLBOX_DIR=$(find ~/.local/share/JetBrains -type d -name "jetbrains-toolbox*" | head -n 1)

# Run the Toolbox installer (will add menu entry automatically)
"$TOOLBOX_DIR/jetbrains-toolbox" & disown

echo "🎉 JetBrains Toolbox installed successfully!"
echo "📦 Use it to install and auto-update IntelliJ IDEA, PyCharm, WebStorm, CLion, and more."
