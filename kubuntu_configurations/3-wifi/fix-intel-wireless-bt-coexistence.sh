#!/bin/bash

# --- Disable Bluetooth/Wi-Fi coexistence for Intel chipsets ---
echo "🔧 Checking for Intel Wi-Fi chipset..."
if lspci -nnk | grep -qi "Intel.*Wireless"; then
    echo "✅ Intel Wi-Fi chipset detected."
    CONF_FILE="/etc/modprobe.d/iwlwifi-disable-coexistence.conf"

    # Create modprobe config if not exists
    if [ ! -f "$CONF_FILE" ]; then
        echo "🔹 Disabling Bluetooth/Wi-Fi coexistence..."
        echo "options iwlwifi bt_coex_active=0" | sudo tee "$CONF_FILE" >/dev/null
        echo "✅ Created $CONF_FILE"
    else
        # Ensure the option exists (idempotent)
        if ! grep -q "bt_coex_active=0" "$CONF_FILE"; then
            echo "options iwlwifi bt_coex_active=0" | sudo tee -a "$CONF_FILE" >/dev/null
            echo "✅ Updated $CONF_FILE"
        else
            echo "ℹ️ Coexistence already disabled in $CONF_FILE"
        fi
    fi

    # Apply changes
    echo "🔁 Reloading Intel Wi-Fi driver..."
    sudo modprobe -r iwlwifi 2>/dev/null || true
    sudo modprobe iwlwifi 2>/dev/null || true

    # Ensure persistence across boots
    echo "🔄 Updating initramfs..."
    sudo update-initramfs -u

    echo "🎉 Bluetooth/Wi-Fi coexistence disabled for Intel Wi-Fi cards."
else
    echo "ℹ️ No Intel Wi-Fi chipset detected. Skipping coexistence setting."
fi
