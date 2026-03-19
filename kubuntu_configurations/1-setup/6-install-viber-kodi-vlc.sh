#!/usr/bin/env bash
set -e

echo "🚀 Installing media apps"

sudo apt update
sudo apt install -y vlc flatpak

# Flatpak setup
sudo flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo

# Kodi
flatpak install -y flathub tv.kodi.Kodi

# Viber
flatpak install -y flathub com.viber.Viber

echo "✅ Media apps installed"
