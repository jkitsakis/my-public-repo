#!/usr/bin/env bash
set -euo pipefail

# ===============================================
#  Clean Temporary & Unnecessary Files – v2
#  Prompts for password using sudo -v (Option 1)
# ===============================================

# Prompt for user password
sudo -v || { echo "❌ Authentication failed. Exiting."; exit 1; }

echo "🧹 Cleaning temporary and unnecessary files..."

# 1️⃣ Variables
BACKUP_DIR="$PWD/system-temp-backup-$(date +'%Y%m%d_%H%M%S')"
mkdir -p "$BACKUP_DIR"

# 2️⃣ Save list of items to backup log
echo "📁 Backing up file list to $BACKUP_DIR/cleanup-list.txt"
{
  echo "=== Files to be cleaned ==="
  sudo find /tmp /var/tmp -type f -mtime +1 2>/dev/null
  echo "=== APT caches ==="
  sudo du -sh /var/cache/apt/* 2>/dev/null || true
  echo "=== User caches ==="
  sudo du -sh /home/*/.cache 2>/dev/null || true
} > "$BACKUP_DIR/cleanup-list.txt"

# 3️⃣ Clean APT cache
echo "🧰 Cleaning APT caches..."
sudo apt-get clean
sudo apt-get autoclean -y
sudo apt-get autoremove -y --purge

# 4️⃣ Clear system temporary folders
echo "🗑️ Clearing /tmp and /var/tmp..."
sudo rm -rf /tmp/* /var/tmp/* 2>/dev/null || true

# 5️⃣ Clear journal logs older than 1 day
echo "🧾 Clearing old journal logs..."
sudo journalctl --vacuum-time=1day || true

# 6️⃣ Clear user-level caches
echo "👤 Cleaning user caches..."
for dir in /home/*/.cache; do
  [ -d "$dir" ] && sudo rm -rf "${dir:?}/"* 2>/dev/null || true
done

# 7️⃣ Clear thumbnails
echo "🖼️ Clearing thumbnail caches..."
sudo rm -rf /home/*/.cache/thumbnails/* 2>/dev/null || true

# 8️⃣ Truncate system logs under /var/log (safe wipe)
echo "📜 Truncating old /var/log files..."
sudo find /var/log -type f -name "*.log" -exec truncate -s 0 {} \; 2>/dev/null

# 9️⃣ Clean Snap cache
if command -v snap &>/dev/null; then
  echo "📦 Cleaning old Snap revisions..."
  sudo snap set system refresh.retain=2
  sudo snap list --all | awk '/disabled/{print $1, $3}' | while read snapname revision; do
    sudo snap remove "$snapname" --revision="$revision" 2>/dev/null || true
  done
fi

# 🔟 Clean Flatpak cache (compatible with all versions)
if command -v flatpak &>/dev/null; then
  echo "📦 Cleaning Flatpak cache..."
  # Remove unused runtimes and apps, auto-confirm if possible
  if flatpak uninstall --help | grep -q -- '--unused'; then
    sudo flatpak uninstall --unused -y 2>/dev/null || sudo flatpak uninstall --unused || true
  else
    echo "⚠️ Flatpak version does not support '--unused'. Skipping automatic uninstall."
  fi

  # Repair repositories quietly if supported
  if flatpak repair --help | grep -q -- '--noninteractive'; then
    sudo flatpak repair --noninteractive || true
  else
    sudo flatpak repair || true
  fi
fi


# 11️⃣ Empty trash for all users
echo "🗑️ Emptying trash..."
for trash in /home/*/.local/share/Trash /root/.local/share/Trash; do
  [ -d "$trash/files" ] && sudo rm -rf "$trash/files/"* "$trash/info/"* 2>/dev/null || true
done

# 12️⃣ Final free-space report
echo "✅ Cleanup complete!"
df -h | grep -E "Filesystem|/dev/"

echo "📋 Backup of cleanup list saved in: $BACKUP_DIR"
