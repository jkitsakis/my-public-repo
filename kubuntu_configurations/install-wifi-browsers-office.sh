#!/usr/bin/env bash
set -e

if nmcli -t -f NAME connection show | grep -q "Huawei_pKpM9W"; then
    sudo nmcli connection modify "Huawei_pKpM9W" wifi.band bg
else
    echo "⚠️ Wi-Fi connection 'Huawei_pKpM9W' not found. Skipping..."
fi

sudo systemctl restart NetworkManager

sleep 5

# --- Disable Bluetooth/Wi-Fi coexistence for Intel chipsets ---
echo "🔧 Checking for Intel Wi-Fi chipset..."
if lspci -nnk | grep -qi "Intel.*Wireless"; then
    echo "✅ Intel Wi-Fi chipset detected."
    CONF_FILE="/etc/modprobe.d/iwlwifi-disable-coexistence.conf"

    # Create modprobe config if not exists
    if [ ! -f "$CONF_FILE" ]; then
        echo "🔹 Disabling Bluetooth/Wi-Fi coexistence..."
        echo "options iwlwifi bt_coex_active=0" | sudo tee "$CONF_FILE" >/dev/null
        echo "✅ Created $CONF_FILE"
    else
        # Ensure the option exists (idempotent)
        if ! grep -q "bt_coex_active=0" "$CONF_FILE"; then
            echo "options iwlwifi bt_coex_active=0" | sudo tee -a "$CONF_FILE" >/dev/null
            echo "✅ Updated $CONF_FILE"
        else
            echo "ℹ️ Coexistence already disabled in $CONF_FILE"
        fi
    fi

    # Apply changes
    echo "🔁 Reloading Intel Wi-Fi driver..."
    sudo modprobe -r iwlwifi 2>/dev/null || true
    sudo modprobe iwlwifi 2>/dev/null || true

    # Ensure persistence across boots
    echo "🔄 Updating initramfs..."
    sudo update-initramfs -u

    echo "🎉 Bluetooth/Wi-Fi coexistence disabled for Intel Wi-Fi cards."
else
    echo "ℹ️ No Intel Wi-Fi chipset detected. Skipping coexistence setting."
fi



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

# --- VLC installation (from official VideoLAN PPA) ---
echo "🎥 Installing VLC media player (latest version from VideoLAN PPA)..."

# Remove any existing older VLC
sudo apt remove --purge -y vlc* || true
sudo apt autoremove -y

# Ensure repo tools exist
sudo apt install -y software-properties-common apt-transport-https ca-certificates

# Add the official VLC PPA if not already added
if ! grep -q "videolan" /etc/apt/sources.list.d/* 2>/dev/null; then
    sudo add-apt-repository -y ppa:videolan/master-daily
    echo "✅ VLC PPA added."
else
    echo "ℹ️ VLC PPA already exists, skipping."
fi

# Install VLC and recommended codecs
sudo apt update -qq
sudo apt install -y vlc vlc-plugin-access-extra vlc-plugin-base vlc-plugin-video-output vlc-plugin-qt

echo "🎉 VLC installation complete!"
echo "VLC will now update automatically with apt upgrade."

