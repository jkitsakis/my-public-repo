#!/usr/bin/env bash
set -e

echo "🚀 Installing browsers"

sudo apt update
sudo apt install -y wget gnupg ca-certificates

# Chrome
wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | \
sudo gpg --dearmor -o /usr/share/keyrings/google.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/google.gpg] http://dl.google.com/linux/chrome/deb/ stable main" | \
sudo tee /etc/apt/sources.list.d/google.list

# Opera
wget -qO- https://deb.opera.com/archive.key | \
sudo gpg --dearmor -o /usr/share/keyrings/opera.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/opera.gpg] https://deb.opera.com/opera-stable/ stable non-free" | \
sudo tee /etc/apt/sources.list.d/opera.list

sudo apt update
sudo apt install -y google-chrome-stable opera-stable

echo "✅ Browsers installed"
