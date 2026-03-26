#!/usr/bin/env bash
set -euo pipefail

# ============================================
# 📡 FULL WIFI DIAGNOSTIC TOOL (MERGED)
# ============================================

TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
REPORT="$HOME/wifi-diagnose_$TIMESTAMP.txt"

log() { echo -e "$1" | tee -a "$REPORT"; }

log "🔧 Wi-Fi Full Diagnostic – $TIMESTAMP"
log "User: $USER | Host: $(hostname) | Kernel: $(uname -r)"
log "------------------------------------------------------------"

# Sudo cache
sudo -v 2>/dev/null || true

run() {
	log "\n$ $*"
	eval "$@" 2>&1 | tee -a "$REPORT" || true
}

# ============================================
# 🔍 Detect Wi-Fi interface
# ============================================
log "\n=== Detecting Wi-Fi Interface ==="

WIFI_IF="$(nmcli -t -f DEVICE,TYPE device 2>/dev/null | awk -F: '$2=="wifi"{print $1; exit}')"

if [[ -z "${WIFI_IF:-}" ]]; then
	WIFI_IF="$(iw dev 2>/dev/null | awk '/Interface/{print $2; exit}')"
fi

if [[ -z "${WIFI_IF:-}" ]]; then
	log "❌ No Wi-Fi interface found"
	run "nmcli device status"
	run "rfkill list"
	exit 1
else
	log "✅ Wi-Fi Interface: $WIFI_IF"
fi

# ============================================
# 📊 BASIC INFO
# ============================================
log "\n=== BASIC STATUS ==="
run "nmcli device status"
run "nmcli connection show --active"
run "ip -4 a show dev $WIFI_IF"
run "ip route"
run "rfkill list"

# ============================================
# 📡 SIGNAL & LINK INFO
# ============================================
log "\n=== SIGNAL & LINK INFO ==="
run "iw dev $WIFI_IF link || true"
run "iwconfig $WIFI_IF || true"

# ============================================
# 🔌 INTERFACES & DRIVERS
# ============================================
log "\n=== NETWORK INTERFACES ==="
run "iw dev"

log "\n=== DRIVER / MODULE INFO ==="
run "lspci -nnk | grep -A3 -i network || true"
run "lsusb || true"
run "lsmod | grep -E 'iwlwifi|rtl|rtw|brcm|bcma' || true"

# ============================================
# ⚡ POWER MANAGEMENT
# ============================================
log "\n=== POWER MANAGEMENT ==="
run "iwconfig $WIFI_IF | grep 'Power Management' || true"

# ============================================
# 🌐 GATEWAY & CONNECTIVITY
# ============================================
log "\n=== NETWORK CONNECTIVITY ==="

GATEWAY_IP="$(ip route | awk '/default via/{print $3; exit}')"
log "Gateway: ${GATEWAY_IP:-NOT FOUND}"

run "ping -c4 -w5 ${GATEWAY_IP:-8.8.8.8} || true"
run "ping -c4 -w5 8.8.8.8 || true"
run "ping -c4 -w5 1.1.1.1 || true"

# ============================================
# 🌍 DNS TEST
# ============================================
log "\n=== DNS TEST ==="
run "cat /etc/resolv.conf"
run "getent hosts google.com || true"
run "ping -c3 google.com || true"
run "nslookup google.com 8.8.8.8 || true"

# ============================================
# 📜 LOGS
# ============================================
log "\n=== RECENT NETWORK LOGS ==="
run "sudo journalctl -u NetworkManager --since '15 minutes ago' --no-pager"
run "sudo dmesg --ctime | tail -n 100"

# ============================================
# 🧠 QUICK DIAGNOSIS
# ============================================
log "\n=== QUICK DIAGNOSIS ==="

IP_ADDR="$(ip -4 -o addr show dev $WIFI_IF | awk '{print $4}' | cut -d/ -f1 | head -n1)"
CONN_STATE="$(nmcli -t -f GENERAL.STATE device show $WIFI_IF 2>/dev/null | sed 's/GENERAL.STATE://')"

if [[ "$CONN_STATE" == *"connected"* || "$CONN_STATE" == "100" ]]; then
	log "✅ Connected to Wi-Fi"
else
	log "⚠️ Not properly connected"
fi

if [[ "$IP_ADDR" =~ ^169\.254\. ]]; then
	log "❌ APIPA address → DHCP FAILED"
elif [[ -z "$IP_ADDR" ]]; then
	log "❌ No IP address"
else
	log "✅ IP Address: $IP_ADDR"
fi

if [[ -n "$GATEWAY_IP" ]] && ping -c1 "$GATEWAY_IP" &>/dev/null; then
	log "✅ Gateway reachable"
else
	log "❌ Gateway unreachable"
fi

if ping -c1 8.8.8.8 &>/dev/null; then
	log "✅ Internet reachable"
else
	log "❌ No internet access"
fi

if getent hosts google.com &>/dev/null; then
	log "✅ DNS working"
else
	log "❌ DNS issue detected"
fi

# ============================================
# 📄 FINAL
# ============================================
log "\n📄 Full report saved to: $REPORT"

echo ""
echo "🚀 QUICK FIX COMMANDS:"
echo "----------------------------------"
echo "Restart NetworkManager:"
echo "  sudo systemctl restart NetworkManager"
echo ""
echo "Reconnect Wi-Fi:"
echo "  nmcli dev disconnect $WIFI_IF && nmcli dev connect $WIFI_IF"
echo ""
echo "Disable power saving:"
echo "  sudo iwconfig $WIFI_IF power off"
echo ""
echo "Set DNS:"
echo "  nmcli connection modify <your-connection> ipv4.dns '8.8.8.8 1.1.1.1'"
echo "----------------------------------"
