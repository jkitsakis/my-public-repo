#!/usr/bin/env bash
set -e

echo "🔄 Updating APT..."
sudo apt update

echo "⬆️ Upgrading system..."
sudo apt full-upgrade -y

echo "🧹 Cleaning..."
sudo apt autoremove --purge -y
sudo apt clean

echo "📦 Snap update..."
sudo snap refresh

echo "🎯 Flatpak update..."
flatpak update -y

echo "🔌 Firmware update..."
if command -v fwupdmgr &> /dev/null; then
    sudo fwupdmgr refresh --force
    sudo fwupdmgr update -y
fi

echo "📊 Upgradable packages:"
apt -qq list --upgradable || true

echo "✅ DONE"
