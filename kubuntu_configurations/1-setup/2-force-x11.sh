#!/usr/bin/env bash
set -e

echo "🚀 KDE → X11 setup (SAFE)"

sudo apt update
sudo apt install -y plasma-session-x11 xorg xserver-xorg

sudo mkdir -p /etc/sddm.conf.d

echo -e "[General]\nDisplayServer=x11" | sudo tee /etc/sddm.conf.d/x11.conf >/dev/null
echo -e "[Wayland]\nEnable=false" | sudo tee /etc/sddm.conf.d/wayland.conf >/dev/null

cat > ~/.dmrc <<EOF
[Desktop]
Session=plasmax11
EOF

echo "✅ X11 configured. Reboot recommended."
