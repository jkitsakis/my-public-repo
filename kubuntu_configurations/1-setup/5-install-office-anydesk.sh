#!/usr/bin/env bash
set -e

echo "🚀 Installing AnyDesk + LibreOffice"

sudo apt update
sudo apt install -y wget gnupg software-properties-common

# AnyDesk
wget -qO- https://keys.anydesk.com/repos/DEB-GPG-KEY | \
sudo gpg --dearmor -o /usr/share/keyrings/anydesk.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/anydesk.gpg] http://deb.anydesk.com/ all main" | \
sudo tee /etc/apt/sources.list.d/anydesk.list

sudo apt update
sudo apt install -y anydesk

# LibreOffice (no purge madness)
sudo add-apt-repository -y ppa:libreoffice/ppa
sudo apt update
sudo apt install -y libreoffice libreoffice-gtk3 libreoffice-style-breeze

echo "✅ Done"
