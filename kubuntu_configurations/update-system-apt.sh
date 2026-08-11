#!/usr/bin/env bash
set -Eeuo pipefail

echo "======================================================"
echo " APT DOCTOR + OPERA REPAIR + ANYDESK REMOVAL"
echo "======================================================"

ARCH="$(dpkg --print-architecture)"
BACKUP_DIR="/etc/apt/sources.list.d/backup_$(date +%Y%m%d_%H%M%S)"
sudo mkdir -p "$BACKUP_DIR"

########################################
# 1. BACK UP APT SOURCE FILES
########################################
echo "💾 Backing up APT source files..."

[ -f /etc/apt/sources.list ] && \
    sudo cp -a /etc/apt/sources.list "$BACKUP_DIR/" || true

for file in /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
    [ -f "$file" ] || continue
    sudo cp -a "$file" "$BACKUP_DIR/" || true
done

########################################
# 2. REMOVE ANYDESK COMPLETELY
########################################
echo "🧹 Removing AnyDesk package/repository/key..."

sudo apt purge -y anydesk 2>/dev/null || true

sudo rm -f \
    /etc/apt/sources.list.d/anydesk.list \
    /etc/apt/sources.list.d/anydesk-stable.list \
    /usr/share/keyrings/anydesk.gpg \
    /usr/share/keyrings/anydesk-keyring.gpg \
    /etc/apt/trusted.gpg.d/anydesk.gpg \
    2>/dev/null || true

# Remove AnyDesk entries if they were placed inside another .list file.
for file in /etc/apt/sources.list /etc/apt/sources.list.d/*.list; do
    [ -f "$file" ] || continue
    if grep -qi "anydesk" "$file"; then
        sudo sed -i '/anydesk/Id' "$file"
    fi
done

########################################
# 3. FIX OPERA REPOSITORY
########################################
echo "🎭 Repairing Opera Stable repository..."

sudo apt install -y curl ca-certificates gnupg

# Remove old Opera source files and old key files that may conflict.
sudo rm -f \
    /etc/apt/sources.list.d/opera.list \
    /etc/apt/sources.list.d/opera-stable.list \
    /usr/share/keyrings/opera.gpg \
    /usr/share/keyrings/opera-browser.gpg \
    2>/dev/null || true

# Remove Opera definitions from other .list files to prevent duplicate
# or conflicting Signed-By entries.
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
# 4. REMOVE EXACT DUPLICATE DEB LINES
########################################
echo "🔍 Checking duplicate APT repository lines..."

declare -A SEEN_REPOS=()

for file in /etc/apt/sources.list /etc/apt/sources.list.d/*.list; do
    [ -f "$file" ] || continue

    tmp="$(mktemp)"
    changed=false

    while IFS= read -r line || [ -n "$line" ]; do
        normalized="$(printf '%s\n' "$line" \
            | sed -E 's/[[:space:]]+/ /g; s/^ +//; s/ +$//')"

        # Preserve comments, blank lines, and non-deb lines unchanged.
        if [[ ! "$normalized" =~ ^deb[[:space:]] ]]; then
            printf '%s\n' "$line" >> "$tmp"
            continue
        fi

        if [[ -n "${SEEN_REPOS[$normalized]:-}" ]]; then
            echo "⚠️ Removing duplicate from $file:"
            echo "   $normalized"
            changed=true
            continue
        fi

        SEEN_REPOS["$normalized"]=1
        printf '%s\n' "$line" >> "$tmp"
    done < "$file"

    if [ "$changed" = true ]; then
        sudo cp "$file" "$BACKUP_DIR/$(basename "$file").before-dedup" 2>/dev/null || true
        sudo cp "$tmp" "$file"
    fi

    rm -f "$tmp"
done

########################################
# 5. REPAIR DPKG/APT STATE
########################################
echo "🛠 Repairing package state..."

sudo dpkg --configure -a
sudo apt --fix-broken install -y

########################################
# 6. APT UPDATE
########################################
echo "🔄 Updating APT..."
sudo apt update

########################################
# 7. ENSURE OPERA IS INSTALLED
########################################
echo "🌐 Installing/updating Opera Stable..."
sudo apt install -y opera-stable

########################################
# 8. FULL SYSTEM UPGRADE
########################################
echo "⬆️ Upgrading system..."
sudo apt full-upgrade -y

echo "🧹 Cleaning..."
sudo apt autoremove --purge -y
sudo apt clean

########################################
# 9. SNAP / FLATPAK / FIRMWARE
########################################
if command -v snap >/dev/null 2>&1; then
    echo "📦 Snap update..."
    sudo snap refresh
fi

if command -v flatpak >/dev/null 2>&1; then
    echo "🎯 Flatpak update..."
    flatpak update -y
fi

if command -v fwupdmgr >/dev/null 2>&1; then
    echo "🔌 Firmware update..."
    sudo fwupdmgr refresh --force || true
    sudo fwupdmgr update -y || true
fi

########################################
# 10. HEALTH REPORT
########################################
echo
echo "======================================================"
echo " SYSTEM HEALTH REPORT"
echo "======================================================"

echo "Opera package:"
dpkg-query -W -f='${Package} ${Version} - ${Status}\n' opera-stable 2>/dev/null \
    || echo "❌ Opera Stable is not installed."

echo
echo "Opera executable:"
if command -v opera >/dev/null 2>&1; then
    opera --version || true
elif command -v opera-stable >/dev/null 2>&1; then
    opera-stable --version || true
else
    echo "❌ Opera executable not found."
fi

echo
echo "AnyDesk:"
if dpkg-query -W -f='${Status}\n' anydesk 2>/dev/null | grep -q "install ok installed"; then
    echo "❌ AnyDesk is still installed."
else
    echo "✅ AnyDesk is removed."
fi

echo
echo "Opera repository:"
grep -RHi "deb\.opera\.com" \
    /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null || true

echo
echo "AnyDesk repository:"
if grep -RHi "anydesk" \
    /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
    echo "⚠️ AnyDesk repository reference still exists."
else
    echo "✅ No AnyDesk repository remains."
fi

echo
echo "APT upgradable:"
apt -qq list --upgradable || true

echo
echo "Disk usage:"
df -h /

echo
echo "Memory:"
free -h

echo
echo "Kernel:"
uname -r

echo
echo "✅ APT DOCTOR COMPLETED"
echo "Backup: $BACKUP_DIR"
