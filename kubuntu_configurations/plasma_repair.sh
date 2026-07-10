#!/usr/bin/env bash
set -e

BACKUP_DIR="$HOME/plasma-backup-$(date +%Y%m%d-%H%M%S)"
CONFIG_FILE="$HOME/.config/plasma-org.kde.plasma.desktop-appletsrc"
PLASMA_RC="$HOME/.config/plasmarc"

echo "Creating backup: $BACKUP_DIR"
mkdir -p "$BACKUP_DIR"

[ -f "$CONFIG_FILE" ] && cp "$CONFIG_FILE" "$BACKUP_DIR/"
[ -f "$PLASMA_RC" ] && cp "$PLASMA_RC" "$BACKUP_DIR/"

echo "Stopping plasmashell..."
kquitapp6 plasmashell 2>/dev/null || killall plasmashell 2>/dev/null || true
sleep 2

echo "Resetting broken Plasma desktop layout..."
[ -f "$CONFIG_FILE" ] && mv "$CONFIG_FILE" "$CONFIG_FILE.broken.$(date +%Y%m%d-%H%M%S)"
[ -f "$PLASMA_RC" ] && mv "$PLASMA_RC" "$PLASMA_RC.broken.$(date +%Y%m%d-%H%M%S)"

echo "Starting plasmashell..."
plasmashell --replace >/tmp/plasma-repair.log 2>&1 &

echo
echo "Done."
echo "If the desktop background does not return in 20 seconds, reboot:"
echo "  reboot"
echo
echo "Backup saved in:"
echo "  $BACKUP_DIR"
echo "Log:"
echo "  /tmp/plasma-repair.log"
