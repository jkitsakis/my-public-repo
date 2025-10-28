#!/usr/bin/env bash
set -euo pipefail

# === Settings you can change ===
CONN="Huawei_pKpM9W"     # your saved Wi-Fi connection name (nmcli connection show)
IFACE="wlp1s0"           # your Wi-Fi interface (ip -br link / nmcli device)
COUNTRY="GR"             # regulatory domain (iw reg get)
POWERSAVE_CONF="/etc/NetworkManager/conf.d/wifi-powersave.conf"

usage() {
  cat <<USAGE
Usage: sudo $0 [--apply | --revert | --status]

  --apply   Apply stability settings (2.4 GHz only, WPA2-PSK only, no powersave, stable MAC, GR)
  --revert  Revert to defaults (dual band auto, remove powersave conf, preserve MAC)
  --status  Show current status
USAGE
  exit 1
}

need_bin() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1"; exit 1; }; }

show_status() {
  echo "=== STATUS ==="
  echo "- Connection profile: $CONN"
  echo "- Interface: $IFACE"
  echo
  echo "[nmcli connection show $CONN]"
  nmcli -g 802-11-wireless.band,802-11-wireless.cloned-mac-address,802-11-wireless.mac-address-blacklist,802-11-wireless-security.key-mgmt,802-11-wireless-security.proto,802-11-wireless-security.group,802-11-wireless-security.pairwise connection show "$CONN" \
    | awk -F: 'BEGIN{print "band|cloned-mac|mac-blacklist|key-mgmt|proto|group|pairwise"}{print}'
  echo
  echo "[iw dev $IFACE link]"
  iw dev "$IFACE" link || true
  echo
  echo "[iw reg get]"
  iw reg get | sed 's/^/  /'
  echo
  echo "[Powersave conf exists?]  $POWERSAVE_CONF -> $( [ -f "$POWERSAVE_CONF" ] && echo PRESENT || echo MISSING )"
  echo
  echo "[Recent NM log tail]"
  journalctl -u NetworkManager -n 60 --no-pager || true
}

apply_settings() {
  echo ">>> Applying Wi-Fi stability settings…"

  # A) Lock to 2.4 GHz band (bg)
  nmcli connection modify "$CONN" 802-11-wireless.band bg

  # B) WPA2-PSK only (no SAE/FT)
  nmcli connection modify "$CONN" 802-11-wireless-security.key-mgmt wpa-psk
  nmcli connection modify "$CONN" 802-11-wireless-security.proto rsn
  nmcli connection modify "$CONN" 802-11-wireless-security.group ccmp
  nmcli connection modify "$CONN" 802-11-wireless-security.pairwise ccmp

  # C) Disable Wi-Fi powersave (global NM)
  mkdir -p "$(dirname "$POWERSAVE_CONF")"
  cat > "$POWERSAVE_CONF" <<CONF
[connection]
wifi.powersave = 2
CONF

  # D) Stable MAC on this profile (avoid random MAC churn on some routers)
  nmcli connection modify "$CONN" 802-11-wireless.cloned-mac-address permanent
  nmcli connection modify "$CONN" 802-11-wireless.mac-address-blacklist ""

  # E) Regulatory domain
  iw reg set "$COUNTRY" || true

  # Restart NM to pick up powersave change, then reconnect
  systemctl restart NetworkManager
  sleep 2
  nmcli connection up "$CONN" || nmcli dev wifi connect "$(nmcli -g connection.id connection show "$CONN")"

  # Ensure device power save is off (runtime)
  iw dev "$IFACE" set power_save off || true

  echo ">>> Applied."
}

revert_settings() {
  echo ">>> Reverting Wi-Fi settings…"

  # A) Back to auto band selection
  nmcli connection modify "$CONN" 802-11-wireless.band auto

  # B) Keep WPA2 settings (safe defaults). If you *need* WPA3 later, set in router + client.
  nmcli connection modify "$CONN" 802-11-wireless-security.key-mgmt wpa-psk
  nmcli connection modify "$CONN" 802-11-wireless-security.proto rsn
  nmcli connection modify "$CONN" 802-11-wireless-security.group ccmp
  nmcli connection modify "$CONN" 802-11-wireless-security.pairwise ccmp

  # C) Remove powersave override (back to NM default)
  [ -f "$POWERSAVE_CONF" ] && rm -f "$POWERSAVE_CONF" || true

  # D) Stop forcing MAC; go back to preserve
  nmcli connection modify "$CONN" 802-11-wireless.cloned-mac-address preserve
  nmcli connection modify "$CONN" 802-11-wireless.mac-address-blacklist ""

  # Restart NM and reconnect
  systemctl restart NetworkManager
  sleep 2
  nmcli connection up "$CONN" || nmcli dev wifi connect "$(nmcli -g connection.id connection show "$CONN")"

  # Let runtime power save follow NM defaults
  echo ">>> Reverted."
}

main() {
  [ $# -eq 0 ] && usage
  need_bin nmcli
  need_bin iw
  case "${1:-}" in
    --apply)  apply_settings; show_status ;;
    --revert) revert_settings; show_status ;;
    --status) show_status ;;
    *) usage ;;
  esac
}

main "$@"

