#!/usr/bin/env bash

set -euo pipefail

CONNECTION_NAME="Huawei_pKpM9W"
INTERFACE_NAME="wlp0s20f3"
TARGET_BSSID="DC:62:79:AF:1A:5E"

LOG_TAG="lock-huawei-wifi"

log() {
    logger -t "$LOG_TAG" "$1"
    echo "$1"
}

# Wait until NetworkManager is running.
for attempt in {1..30}; do
    if systemctl is-active --quiet NetworkManager; then
        break
    fi

    sleep 2
done

if ! systemctl is-active --quiet NetworkManager; then
    log "NetworkManager did not become active."
    exit 1
fi

# Wait until the Wi-Fi interface is available.
for attempt in {1..30}; do
    if nmcli -t -f DEVICE device status | grep -Fxq "$INTERFACE_NAME"; then
        break
    fi

    sleep 2
done

if ! nmcli -t -f DEVICE device status | grep -Fxq "$INTERFACE_NAME"; then
    log "Wi-Fi interface $INTERFACE_NAME was not found."
    exit 1
fi

if ! nmcli -t -f NAME connection show | grep -Fxq "$CONNECTION_NAME"; then
    log "NetworkManager connection '$CONNECTION_NAME' was not found."
    exit 1
fi

log "Locking '$CONNECTION_NAME' to BSSID $TARGET_BSSID."

nmcli connection modify \
    "$CONNECTION_NAME" \
    802-11-wireless.bssid "$TARGET_BSSID" \
    connection.interface-name "$INTERFACE_NAME"

# Disable Wi-Fi power saving for this connection as well.
nmcli connection modify \
    "$CONNECTION_NAME" \
    802-11-wireless.powersave 2

# Activate the connection. No explicit 'down' is required during boot.
nmcli connection up \
    "$CONNECTION_NAME" \
    ifname "$INTERFACE_NAME"

CONNECTED_BSSID="$(
    iw dev "$INTERFACE_NAME" link |
        awk '/Connected to/ {print toupper($3)}'
)"

if [[ "$CONNECTED_BSSID" == "$TARGET_BSSID" ]]; then
    log "Successfully connected to $TARGET_BSSID."
else
    log "Connection activated, but current BSSID is '${CONNECTED_BSSID:-unknown}'."
    exit 1
fi
