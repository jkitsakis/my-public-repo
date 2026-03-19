#!/bin/bash

if nmcli -t -f NAME connection show | grep -q "Huawei_pKpM9W"; then
    sudo nmcli connection modify "Huawei_pKpM9W" wifi.band bg
else
    echo "⚠️ Wi-Fi connection 'Huawei_pKpM9W' not found. Skipping..."
fi

sudo systemctl restart NetworkManager

sleep 5