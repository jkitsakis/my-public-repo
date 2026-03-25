#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-full}"   # clean | repair | dev | full

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BASE_DIR="$PWD/system-maintenance-$TIMESTAMP"
mkdir -p "$BASE_DIR"

echo "🧰 Mode: $MODE"
echo "📁 Output: $BASE_DIR"

sudo -v || { echo "❌ Authentication failed"; exit 1; }

# ============================================
# 🔧 SYSTEM REPAIR
# ============================================
repair_system() {
  echo "🔧 Repairing system..."

  sudo dpkg --configure -a || true
  sudo apt-get install -f -y || true
  sudo apt-get update -y
  sudo apt-get upgrade -y
  sudo apt-get dist-upgrade -y
  sudo apt-get autoremove -y --purge
  sudo apt-get clean

  echo "✅ System repair done"
}

# ============================================
# 🧹 SYSTEM CLEAN
# ============================================
clean_system() {
  echo "🧹 Cleaning system..."

  sudo find /tmp -type f -mtime +1 -delete 2>/dev/null || true
  sudo find /var/tmp -type f -mtime +1 -delete 2>/dev/null || true

  sudo journalctl --vacuum-time=3d

  for dir in /home/*/.cache; do
    [ -d "$dir" ] && rm -rf "$dir"/* || true
  done

  rm -rf /home/*/.cache/thumbnails/* 2>/dev/null || true
  sudo rm -rf /var/crash/* 2>/dev/null || true

  sudo find /var/log -type f -name "*.gz" -delete 2>/dev/null || true
  sudo find /var/log -type f -name "*.log" -exec truncate -s 0 {} \;

  # Snap
  if command -v snap &>/dev/null; then
    sudo snap set system refresh.retain=2
    sudo snap list --all | awk '/disabled/{print $1, $3}' | while read n r; do
      sudo snap remove "$n" --revision="$r" || true
    done
  fi

  # Flatpak
  if command -v flatpak &>/dev/null; then
    sudo flatpak uninstall --unused -y || true
    sudo flatpak repair --noninteractive || true
  fi

  # Trash
  for t in /home/*/.local/share/Trash /root/.local/share/Trash; do
    [ -d "$t/files" ] && rm -rf "$t/files/"* "$t/info/"* || true
  done

  echo "✅ System cleaned"
}

# ============================================
# 🐳 DOCKER CLEANUP
# ============================================
docker_cleanup() {
  if ! command -v docker &>/dev/null; then
    echo "⚠️ Docker not installed, skipping..."
    return
  fi

  echo "🐳 Cleaning Docker..."

  docker system df

  # Remove stopped containers
  docker container prune -f

  # Remove unused images
  docker image prune -a -f

  # Remove unused networks
  docker network prune -f

  # Remove unused volumes (SAFE: only dangling)
  docker volume prune -f

  # Remove build cache
  docker builder prune -a -f

  echo "✅ Docker cleaned"
}

# ============================================
# 🟢 NODE / NPM CLEANUP
# ============================================
node_cleanup() {
  if command -v npm &>/dev/null; then
    echo "🟢 Cleaning npm..."
    npm cache clean --force || true
  fi
}

# ============================================
# 🐍 PYTHON CLEANUP
# ============================================
python_cleanup() {
  if command -v pip &>/dev/null; then
    echo "🐍 Cleaning pip cache..."
    pip cache purge || true
  fi

  # Remove __pycache__
  echo "🐍 Removing __pycache__..."
  find ~ -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
}

# ============================================
# ☕ JAVA CLEANUP
# ============================================
java_cleanup() {
  echo "☕ Cleaning Java caches..."

  # Maven
  [ -d "$HOME/.m2/repository" ] && find "$HOME/.m2/repository" -name "*.lastUpdated" -delete

  # Gradle
  [ -d "$HOME/.gradle/caches" ] && rm -rf "$HOME/.gradle/caches/"*

  echo "✅ Java cleaned"
}

# ============================================
# 🚀 DEV CLEANUP
# ============================================
dev_cleanup() {
  docker_cleanup
  node_cleanup
  python_cleanup
  java_cleanup
}

# ============================================
# 🚀 EXECUTION
# ============================================
case "$MODE" in
  clean)
    clean_system
    ;;
  repair)
    repair_system
    ;;
  dev)
    dev_cleanup
    ;;
  full)
    repair_system
    clean_system
    dev_cleanup
    ;;
  *)
    echo "❌ Usage: $0 [clean|repair|dev|full]"
    exit 1
    ;;
esac

echo ""
echo "📊 Disk usage:"
df -h | grep -E "Filesystem|/dev/"

echo "🎉 DONE"
