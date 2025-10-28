#!/usr/bin/env bash
set -euo pipefail

echo "🧹 Cleaning system logs safely..."

# Step 1. Create a backup directory in current working directory
BACKUP_DIR="$PWD/system-logs-backup-$(date +'%Y%m%d_%H%M%S')"
mkdir -p "$BACKUP_DIR"
echo "📁 Saving existing logs to: $BACKUP_DIR"

# Copy /var/log
sudo cp -r /var/log "$BACKUP_DIR/var-log" 2>/dev/null || true

# Copy journal logs if they exist
if [ -d /var/log/journal ]; then
    sudo cp -r /var/log/journal "$BACKUP_DIR/journal" 2>/dev/null || true
fi
if [ -d /run/log/journal ]; then
    sudo cp -r /run/log/journal "$BACKUP_DIR/run-journal" 2>/dev/null || true
fi

# Step 2. Stop logging services (they’ll restart automatically later)
echo "🛑 Stopping journald temporarily..."
sudo systemctl stop systemd-journald.service systemd-journald.socket systemd-journald-dev-log.socket || true

# Step 3. Clear journal logs
sudo rm -rf /var/log/journal/* /run/log/journal/*
sudo journalctl --rotate || true
sudo journalctl --vacuum-time=1s || true

# Step 4. Clear legacy /var/log files
echo "🗑️ Removing old text logs..."
sudo rm -rf /var/log/*.log /var/log/syslog /var/log/auth.log /var/log/kern.log /var/log/apt/* /var/log/dpkg.log*

# Step 5. Restart logging services
echo "🔄 Restarting journald and rsyslog..."
sudo systemctl start systemd-journald.socket systemd-journald-dev-log.socket
sudo systemctl restart systemd-journald rsyslog || true
# sudo systemctl restart NetworkManager || true

# Step 6. Verify
echo "✅ Logs cleared. Current usage:"
sudo journalctl --disk-usage || true
echo "Backup saved at: $BACKUP_DIR"
