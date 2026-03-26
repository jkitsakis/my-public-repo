#!/usr/bin/env bash
set -e

echo "🚀 Disable WiFi power saving"

WIFI_IFACE=$(iw dev | awk '$1=="Interface"{print $2}')

if [ -z "$WIFI_IFACE" ]; then
	echo "❌ No WiFi interface found"
	exit 1
fi

echo "👉 Interface: $WIFI_IFACE"

sudo iw dev "$WIFI_IFACE" set power_save off

echo "✅ Done"
