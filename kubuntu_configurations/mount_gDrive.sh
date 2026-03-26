#!/usr/bin/env bash

echo "🔄 Restarting rclone mount..."

# Kill existing mount
pkill -f "rclone mount GDrive:"

sleep 2

# Start new mount
rclone mount GDrive: ~/GDrive \
	--vfs-cache-mode writes \
	--daemon

echo "✅ Mounted GDrive"
