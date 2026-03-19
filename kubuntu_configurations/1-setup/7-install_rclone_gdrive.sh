#!/usr/bin/env bash
set -e

echo "🚀 Installing rclone + Google Drive"

sudo apt update
sudo apt install -y curl fuse3

curl https://rclone.org/install.sh | sudo bash

mkdir -p ~/GDrive

echo "👉 Run: rclone config"
echo "👉 Then mount manually first to test:"
echo "   rclone mount GDrive: ~/GDrive"
