#!/usr/bin/env bash

set -e

echo "🚀 FULL SYSTEM SETUP + REPAIR + UPDATE STARTED..."

# --------------------------------------------------
# 1. FIX NETWORK (CRITICAL)
# --------------------------------------------------
echo "🔧 Unblocking WiFi..."
sudo rfkill unblock all || true

echo "🔧 Restarting NetworkManager..."
sudo systemctl restart NetworkManager
sleep 5

echo "🔧 Fixing DNS..."
sudo bash -c 'cat > /etc/resolv.conf <<EOF
nameserver 8.8.8.8
nameserver 1.1.1.1
EOF'

# --------------------------------------------------
# 2. ENSURE INTERNET
# --------------------------------------------------
echo "🌐 Checking internet..."

if ! ping -c 2 8.8.8.8 > /dev/null 2>&1; then
    echo "❌ No internet → trying to reconnect WiFi"

    nmcli device wifi rescan || true
    nmcli device wifi list || true

    WIFI_NAME="Huawei_pKpM9W"
    WIFI_PASS="PU4nq9D6"

    nmcli device wifi connect "$WIFI_NAME" password "$WIFI_PASS" || true

    sleep 5

    if ! ping -c 2 8.8.8.8 > /dev/null 2>&1; then
        echo "❌ Still no internet → STOP"
        exit 1
    fi
fi

echo "✅ Internet OK"

# --------------------------------------------------
# 3. FIX APT
# --------------------------------------------------
echo "🧹 Cleaning APT..."
sudo rm -rf /var/lib/apt/lists/*
sudo apt clean

echo "🔧 Fixing broken packages..."
sudo dpkg --configure -a || true
sudo apt --fix-broken install -y || true

echo "⬇️ Updating repositories..."
sudo apt update --fix-missing

# --------------------------------------------------
# 4. INSTALL SNAP (snapd)
# --------------------------------------------------
echo "📦 Installing Snap..."

if ! command -v snap >/dev/null 2>&1; then
    sudo apt install -y snapd
    sudo systemctl enable --now snapd
    sudo ln -s /var/lib/snapd/snap /snap || true
    echo "✅ Snap installed"
else
    echo "✅ Snap already installed"
fi

# --------------------------------------------------
# 5. INSTALL FLATPAK
# --------------------------------------------------
echo "🎯 Installing Flatpak..."

if ! command -v flatpak >/dev/null 2>&1; then
    sudo apt install -y flatpak
    echo "✅ Flatpak installed"
else
    echo "✅ Flatpak already installed"
fi

# Add Flathub repo if missing
if ! flatpak remote-list | grep -q flathub; then
    echo "🔧 Adding Flathub repo..."
    sudo flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
fi

# KDE integration (important for Kubuntu)
sudo apt install -y plasma-discover-backend-flatpak || true

# --------------------------------------------------
# 6. UPDATE EVERYTHING
# --------------------------------------------------
echo "🔄 Updating APT packages..."
sudo apt full-upgrade -y
sudo apt autoremove --purge -y
sudo apt clean

echo "📦 Updating Snap packages..."
sudo snap refresh || true

echo "🎯 Updating Flatpak packages..."
flatpak update -y || true

# --------------------------------------------------
# 7. FINAL CHECK
# --------------------------------------------------
echo "🔍 Final check..."
ping -c 2 google.com && echo "✅ SYSTEM FULLY WORKING"

echo ""
echo "🎉 DONE! EVERYTHING INSTALLED + FIXED + UPDATED"
