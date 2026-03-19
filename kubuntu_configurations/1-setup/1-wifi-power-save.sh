#!/usr/bin/env bash
set -e

WIFI_IFACE=$(iw dev | awk '$1=="Interface"{print $2}')

if [ -z "$WIFI_IFACE" ]; then
    echo "❌ No WiFi interface found"
    exit 1
fi

echo "✅ Found WiFi interface: $WIFI_IFACE"

sudo iw dev "$WIFI_IFACE" set power_save off
echo "🚀 Power saving disabled on $WIFI_IFACE"
