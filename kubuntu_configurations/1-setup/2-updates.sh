#!/bin/bash
echo "🔄 Updating system packages..."
sudo apt update && sudo apt full-upgrade -y && sudo apt autoremove --purge -y && sudo apt clean

echo "📦 Updating Snap packages..."
sudo snap refresh

echo "🎯 Updating Flatpak packages..."
flatpak update -y

echo "✅ All updates completed!"
