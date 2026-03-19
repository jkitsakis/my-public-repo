#!/bin/bash

set -e

echo "=============================="
echo "🚀 FORCING KUBUNTU TO X11 ONLY"
echo "=============================="

# -----------------------------
# 1. Fix APT IPv6 issue (your case)
# -----------------------------
echo "👉 Forcing APT to use IPv4..."
echo 'Acquire::ForceIPv4 "true";' | sudo tee /etc/apt/apt.conf.d/99force-ipv4 >/dev/null

# -----------------------------
# 2. Update system
# -----------------------------
echo "👉 Updating package lists..."
sudo apt update

# -----------------------------
# 3. Install required packages
# -----------------------------
echo "👉 Installing X11 components..."
sudo apt install -y plasma-session-x11 xorg xserver-xorg

# -----------------------------
# 4. Configure SDDM (clean way)
# -----------------------------
echo "👉 Configuring SDDM..."

sudo mkdir -p /etc/sddm.conf.d

echo -e "[General]\nDisplayServer=x11" | sudo tee /etc/sddm.conf.d/x11.conf >/dev/null

echo -e "[Wayland]\nEnable=false" | sudo tee /etc/sddm.conf.d/wayland.conf >/dev/null

# -----------------------------
# 5. Force session to X11
# -----------------------------
echo "👉 Setting default session to X11..."

cat > ~/.dmrc <<EOF
[Desktop]
Session=plasmax11
EOF

# -----------------------------
# 6. Remove Wayland sessions (prevent fallback)
# -----------------------------
echo "👉 Removing Wayland sessions..."
sudo rm -rf /usr/share/wayland-sessions/*

# -----------------------------
# 7. Optional: Improve X11 compatibility (AnyDesk etc.)
# -----------------------------
echo "👉 Installing X11 video drivers..."
sudo apt install -y xserver-xorg-video-all || true

# -----------------------------
# 8. Final message
# -----------------------------
echo "=============================="
echo "✅ DONE!"
echo "Reboot NOW:"
echo "   sudo reboot"
echo "=============================="