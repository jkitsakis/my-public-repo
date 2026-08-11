#!/usr/bin/env bash
set -Eeuo pipefail

echo "======================================================"
echo " SYSTEM UPDATE + OPERA + ANYDESK CLEANUP"
echo "======================================================"

ARCH="$(dpkg --print-architecture)"

########################################
# 1. REMOVE ANYDESK COMPLETELY
########################################
echo "🧹 Removing AnyDesk..."

sudo apt purge -y anydesk 2>/dev/null || true
sudo rm -f \
    /etc/apt/sources.list.d/anydesk.list \
    /etc/apt/sources.list.d/anydesk-stable.list \
    /usr/share/keyrings/anydesk.gpg \
    /usr/share/keyrings/anydesk-keyring.gpg \
    /etc/apt/trusted.gpg.d/anydesk.gpg \
    2>/dev/null || true

########################################
# 2. ENSURE OPERA OFFICIAL REPOSITORY
########################################
echo "🌐 Configuring Opera Stable repository..."

sudo apt install -y curl ca-certificates gnupg

# Remove old/duplicate Opera repo definitions first.
sudo rm -f \
    /etc/apt/sources.list.d/opera.list \
    /etc/apt/sources.list.d/opera-stable.list \
    2>/dev/null || true

# Remove Opera entries embedded in other .list files to avoid duplicates.
for file in /etc/apt/sources.list /etc/apt/sources.list.d/*.list; do
    [ -f "$file" ] || continue
    if grep -qi "deb\.opera\.com" "$file"; then
        sudo sed -i '\|deb\.opera\.com|Id' "$file"
    fi
done

curl -fsSL https://deb.opera.com/archive.key \
    | gpg --dearmor \
    | sudo tee /usr/share/keyrings/opera-browser.gpg >/dev/null

echo "deb [arch=${ARCH} signed-by=/usr/share/keyrings/opera-browser.gpg] https://deb.opera.com/opera-stable/ stable non-free" \
    | sudo tee /etc/apt/sources.list.d/opera-stable.list >/dev/null

########################################
# 3. UPDATE APT
########################################
echo "🔄 Updating APT..."
sudo apt update

########################################
# 4. INSTALL / UPDATE OPERA
########################################
echo "🎭 Installing/updating Opera Stable..."
sudo apt install -y opera-stable

########################################
# 5. UPGRADE SYSTEM
########################################
echo "⬆️ Upgrading system..."
sudo apt full-upgrade -y

echo "🧹 Cleaning..."
sudo apt autoremove --purge -y
sudo apt clean

########################################
# 6. SNAP
########################################
if command -v snap >/dev/null 2>&1; then
    echo "📦 Snap update..."
    sudo snap refresh
fi

########################################
# 7. FLATPAK
########################################
if command -v flatpak >/dev/null 2>&1; then
    echo "🎯 Flatpak update..."
    flatpak update -y
fi

########################################
# 8. FIRMWARE
########################################
if command -v fwupdmgr >/dev/null 2>&1; then
    echo "🔌 Firmware update..."
    sudo fwupdmgr refresh --force || true
    sudo fwupdmgr update -y || true
fi

########################################
# 9. REPORT
########################################
echo
echo "======================================================"
echo " UPDATE COMPLETE"
echo "======================================================"

echo "Opera:"
if command -v opera >/dev/null 2>&1; then
    opera --version || true
elif command -v opera-stable >/dev/null 2>&1; then
    opera-stable --version || true
else
    dpkg-query -W -f='${Package} ${Version}\n' opera-stable 2>/dev/null || true
fi

echo
echo "AnyDesk:"
if dpkg-query -W -f='${Status}\n' anydesk 2>/dev/null | grep -q "install ok installed"; then
    echo "⚠️ AnyDesk is still installed."
else
    echo "✅ AnyDesk is removed."
fi

echo
echo "APT upgradable packages:"
apt -qq list --upgradable || true

echo
echo "✅ DONE"
