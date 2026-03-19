#!/usr/bin/env bash
set -e

echo "🚀 SYSTEM UPDATE (SAFE)"

if ! ping -c 2 8.8.8.8 >/dev/null 2>&1; then
    echo "❌ No internet. Connect first."
    exit 1
fi

sudo dpkg --configure -a || true
sudo apt --fix-broken install -y || true

sudo apt update
sudo apt upgrade -y
sudo apt full-upgrade -y

sudo apt autoremove --purge -y
sudo apt clean

sudo apt install -y snapd flatpak plasma-discover-backend-flatpak

if ! flatpak remote-list | grep -q flathub; then
    sudo flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
fi

echo "✅ System updated"
