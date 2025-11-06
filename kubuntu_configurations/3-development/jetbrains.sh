#!/usr/bin/env bash
set -e

# --- JetBrains IntelliJ IDEA & PyCharm setup ---
echo "🧠 Setting up JetBrains APT repository for IntelliJ IDEA and PyCharm..."

JETBRAINS_KEYRING="/usr/share/keyrings/jetbrains-archive-keyring.gpg"
JETBRAINS_LIST="/etc/apt/sources.list.d/jetbrains.list"

# 1️⃣  Fetch official JetBrains GPG key
echo "🔑 Fetching JetBrains signing key..."
wget -qO- https://download.jetbrains.com/keys/jetbrains.key | \
  sudo gpg --dearmor -o "$JETBRAINS_KEYRING"

# 2️⃣  Add the JetBrains repository (stable branch)
echo "deb [arch=$(dpkg --print-architecture) signed-by=$JETBRAINS_KEYRING] https://packages.jetbrains.team/apt/jetbrains-toolbox stable main" | \
  sudo tee "$JETBRAINS_LIST" >/dev/null

# 3️⃣  Update and install IDEs
sudo apt update -qq
echo "📦 Installing IntelliJ IDEA Community and PyCharm Community..."
sudo apt install -y intellij-idea-community pycharm-community

echo "🎉 JetBrains IDEs installed successfully!"
echo "✅ They will now update automatically with 'sudo apt update && sudo apt upgrade'."
