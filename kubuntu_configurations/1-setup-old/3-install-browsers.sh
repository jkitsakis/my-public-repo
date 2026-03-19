#!/usr/bin/env bash
set -e


echo "🚀 Installing Google Chrome and Opera browsers (with apt auto-updates)..."

# Ensure required packages are present
sudo apt update -qq
sudo apt install -y wget apt-transport-https ca-certificates gnupg

# --- Google Chrome setup ---
echo "🔹 Setting up Google Chrome repository..."
GOOGLE_KEYRING="/usr/share/keyrings/google-linux-keyring.gpg"
GOOGLE_LIST="/etc/apt/sources.list.d/google-chrome.list"

if [ ! -f "$GOOGLE_KEYRING" ]; then
    wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | \
        sudo gpg --dearmor -o "$GOOGLE_KEYRING"
    echo "✅ Google key added."
else
    echo "ℹ️ Google key already exists, skipping."
fi

if [ ! -f "$GOOGLE_LIST" ]; then
    echo "deb [arch=$(dpkg --print-architecture) signed-by=$GOOGLE_KEYRING] http://dl.google.com/linux/chrome/deb/ stable main" | \
        sudo tee "$GOOGLE_LIST" >/dev/null
    echo "✅ Google repo added."
else
    echo "ℹ️ Google repo already exists, skipping."
fi

# --- Opera setup ---
echo "🔹 Setting up Opera repository..."
OPERA_KEYRING="/usr/share/keyrings/opera-archive-keyring.gpg"
OPERA_LIST="/etc/apt/sources.list.d/opera-stable.list"

if [ ! -f "$OPERA_KEYRING" ]; then
    wget -qO- https://deb.opera.com/archive.key | \
        sudo gpg --dearmor -o "$OPERA_KEYRING"
    echo "✅ Opera key added."
else
    echo "ℹ️ Opera key already exists, skipping."
fi

if [ ! -f "$OPERA_LIST" ]; then
    echo "deb [arch=$(dpkg --print-architecture) signed-by=$OPERA_KEYRING] https://deb.opera.com/opera-stable/ stable non-free" | \
        sudo tee "$OPERA_LIST" >/dev/null
    echo "✅ Opera repo added."
else
    echo "ℹ️ Opera repo already exists, skipping."
fi

# --- Install browsers ---
echo "📦 Installing browsers..."
sudo apt update -qq
sudo apt install -y google-chrome-stable opera-stable

echo "🎉 Installation complete!"
echo "Both Google Chrome and Opera will now update automatically with apt upgrade."


