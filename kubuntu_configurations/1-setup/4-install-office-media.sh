#!/usr/bin/env bash
set -e

echo "🚀 Installing AnyDesk (official repository version)..."

# Ensure dependencies
sudo apt update -qq
sudo apt install -y wget apt-transport-https ca-certificates gnupg

# --- AnyDesk setup ---
ANYDESK_KEYRING="/usr/share/keyrings/anydesk-archive-keyring.gpg"
ANYDESK_LIST="/etc/apt/sources.list.d/anydesk-stable.list"

if [ ! -f "$ANYDESK_KEYRING" ]; then
    echo "🔹 Adding AnyDesk GPG key..."
    wget -qO- https://keys.anydesk.com/repos/DEB-GPG-KEY | \
        sudo gpg --dearmor -o "$ANYDESK_KEYRING"
    echo "✅ Key added."
else
    echo "ℹ️ AnyDesk key already exists, skipping."
fi

if [ ! -f "$ANYDESK_LIST" ]; then
    echo "🔹 Adding AnyDesk repository..."
    echo "deb [arch=$(dpkg --print-architecture) signed-by=$ANYDESK_KEYRING] http://deb.anydesk.com/ all main" | \
        sudo tee "$ANYDESK_LIST" >/dev/null
    echo "✅ Repo added."
else
    echo "ℹ️ AnyDesk repository already exists, skipping."
fi

# --- Install AnyDesk ---
echo "📦 Installing AnyDesk..."
sudo apt update -qq
sudo apt install -y anydesk

echo "🎉 AnyDesk installation complete!"
echo "It will now auto-update through 'apt update && apt upgrade'."



# --- LibreOffice (remove Ubuntu version, install official PPA version) ---
echo "🧹 Removing preinstalled LibreOffice packages..."
sudo apt remove --purge -y libreoffice* libreoffice-core* libreoffice-common* || true
sudo apt autoremove -y
sudo apt clean

echo "🔹 Setting up official LibreOffice repository (PPA)..."
PPA_FILE="/etc/apt/sources.list.d/libreoffice-ppa.list"

# Install dependencies for add-apt-repository if missing
sudo apt install -y software-properties-common apt-transport-https ca-certificates

if [ ! -f "$PPA_FILE" ]; then
    sudo add-apt-repository -y ppa:libreoffice/ppa
    echo "✅ LibreOffice PPA added."
else
    echo "ℹ️ LibreOffice PPA already exists, skipping."
fi

echo "📦 Installing latest LibreOffice (Fresh branch)..."
sudo apt update -qq
sudo apt install -y libreoffice libreoffice-gtk3 libreoffice-style-breeze

echo "🎉 LibreOffice installation complete!"
echo "It will now auto-update via 'apt update && apt upgrade'."


# --- Kodi with flatpak ---
sudo flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak remotes

sudo apt install flatpak -y
flatpak install flathub tv.kodi.Kodi -y

# --- VLC ---
sudo apt install -y vlc

echo "🎉 VLC installation complete!"
echo "VLC will now update automatically with apt upgrade."

