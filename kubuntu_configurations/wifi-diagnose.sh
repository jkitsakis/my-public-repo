#!/usr/bin/env bash
# Wi-Fi Diagnostic Script for Kubuntu
# Saves a full report and prints a short diagnosis.

set -euo pipefail

# ---------- setup ----------
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
REPORT="$HOME/wifi-diagnose_$TIMESTAMP.txt"
WIFI_IF=""
GATEWAY_IP=""
OK()  { echo -e "[OK]  $*" | tee -a "$REPORT"; }
WARN(){ echo -e "[WARN] $*" | tee -a "$REPORT"; }
ERR() { echo -e "[ERR] $*" | tee -a "$REPORT"; }

echo "🔧 Kubuntu Wi-Fi Diagnostic – $TIMESTAMP" | tee "$REPORT"
echo "User: $USER  Host: $(hostname)  Kernel: $(uname -r)" | tee -a "$REPORT"
echo "------------------------------------------------------------" | tee -a "$REPORT"

# Cache sudo once (needed for logs/dmesg); ignore if user cancels.
if command -v sudo >/dev/null 2>&1; then
  sudo -v 2>/dev/null || true
fi

run() {
  echo -e "\n$ $*" | tee -a "$REPORT"
  eval "$@" 2>&1 | tee -a "$REPORT" || true
}

# ---------- detect wifi interface ----------
echo -e "\n### Detecting Wi-Fi interface" | tee -a "$REPORT"
if command -v nmcli >/dev/null 2>&1; then
  WIFI_IF="$(nmcli -t -f DEVICE,TYPE device | awk -F: '$2=="wifi"{print $1; exit}')"
fi
if [[ -z "${WIFI_IF:-}" ]]; then
  # Fallback via iw
  if command -v iw >/dev/null 2>&1; then
    WIFI_IF="$(iw dev | awk '/Interface/{print $2; exit}')"
  fi
fi

if [[ -z "${WIFI_IF:-}" ]]; then
  ERR "No Wi-Fi interface found. Is your adapter disabled or driver missing?"
  run "nmcli device status"
  run "rfkill list"
  exit 1
else
  OK "Wi-Fi interface: $WIFI_IF"
fi

# ---------- basic status ----------
echo -e "\n### Basic status" | tee -a "$REPORT"
run "nmcli device status"
run "nmcli -g GENERAL.STATE,GENERAL.CONNECTION device show $WIFI_IF || true"
run "ip -4 a show dev $WIFI_IF"
run "ip route"
run "rfkill list"

# ---------- link info ----------
echo -e "\n### Link information" | tee -a "$REPORT"
if command -v iw >/dev/null 2>&1; then
  run "iw dev $WIFI_IF link"
fi
if command -v iwconfig >/dev/null 2>&1; then
  run "iwconfig $WIFI_IF"
fi

# ---------- gateway detection ----------
echo -e "\n### Detecting default gateway" | tee -a "$REPORT"
GATEWAY_IP="$(ip route | awk '/default via/{print $3; exit}')"
if [[ -n "$GATEWAY_IP" ]]; then
  OK "Default gateway: $GATEWAY_IP"
else
  ERR "No default gateway found (routing/DHCP issue likely)."
fi

# ---------- ping tests ----------
echo -e "\n### Connectivity tests" | tee -a "$REPORT"
IP_ADDR="$(ip -4 -o addr show dev $WIFI_IF | awk '{print $4}' | cut -d/ -f1 | head -n1)"
APIPA=0
if [[ "$IP_ADDR" =~ ^169\.254\. ]]; then APIPA=1; fi

if [[ -n "$GATEWAY_IP" ]]; then
  run "ping -c4 -w6 $GATEWAY_IP"
fi
run "ping -c4 -w8 8.8.8.8"
run "ping -c4 -w8 1.1.1.1"

# ---------- DNS tests ----------
echo -e "\n### DNS tests" | tee -a "$REPORT"
if command -v resolvectl >/dev/null 2>&1; then
  run "resolvectl status"
fi
run "cat /etc/resolv.conf"
run "getent hosts google.com || true"
run "ping -c3 -w6 google.com || true"
run "nslookup google.com 8.8.8.8 || true"

# ---------- power management ----------
echo -e "\n### Power management" | tee -a "$REPORT"
if command -v iwconfig >/dev/null 2>&1; then
  PM_STATE="$(iwconfig $WIFI_IF 2>/dev/null | awk '/Power Management/{print $3}')"
  echo "Power Management: ${PM_STATE:-unknown}" | tee -a "$REPORT"
fi

# ---------- driver / module ----------
echo -e "\n### Driver / module" | tee -a "$REPORT"
run "lspci -nnk | grep -A3 -i network || true"
run "lsusb || true"
# Common module name via ethtool (if available)
if command -v ethtool >/dev/null 2>&1; then
  run "ethtool -i $WIFI_IF || true"
fi

# ---------- logs (last hour) ----------
echo -e "\n### Recent logs (last 60 minutes)" | tee -a "$REPORT"
run "sudo journalctl -u NetworkManager --since '60 minutes ago' --no-pager"
run "sudo dmesg --ctime | tail -n 200"

# ---------- quick diagnosis ----------
echo -e "\n### Quick diagnosis" | tee -a "$REPORT"

CONN_STATE="$(nmcli -t -f GENERAL.STATE device show $WIFI_IF 2>/dev/null | sed 's/GENERAL.STATE://;s/ (.*)//')"
HAS_IP=0
[[ -n "${IP_ADDR:-}" ]] && [[ "$APIPA" -eq 0 ]] && HAS_IP=1

if [[ "$CONN_STATE" == *"connected"* || "$CONN_STATE" == "100" ]]; then
  OK "NetworkManager reports interface is CONNECTED."
else
  WARN "Interface not fully connected (state: ${CONN_STATE:-unknown})."
fi

if [[ "$HAS_IP" -eq 1 ]]; then
  OK "Valid IPv4 address on $WIFI_IF: $IP_ADDR"
else
  if [[ "$APIPA" -eq 1 ]]; then
    ERR "Got APIPA address ($IP_ADDR) → DHCP failed. Router DHCP or IP conflict likely."
  else
    ERR "No IPv4 address on $WIFI_IF."
  fi
fi

if [[ -n "$GATEWAY_IP" ]]; then
  if ping -c1 -w3 "$GATEWAY_IP" >/dev/null 2>&1; then
    OK "Gateway reachable."
  else
    ERR "Gateway not reachable → Wi-Fi link quality or router issue."
  fi
else
  ERR "No default route → DHCP/router misconfiguration."
fi

if ping -c1 -w3 8.8.8.8 >/dev/null 2>&1; then
  OK "Internet reachable by IP."
  if getent hosts google.com >/dev/null 2>&1; then
    OK "DNS resolution works."
  else
    ERR "DNS looks broken → set manual DNS (8.8.8.8 / 1.1.1.1) in NetworkManager."
  fi
else
  ERR "Cannot reach Internet by IP → ISP outage, WAN down, or upstream routing issue."
fi

echo -e "\n📄 Full report saved to: $REPORT"
echo "Tips:"
echo "• If DHCP/APIPA issues → Restart router; in NM set IPv4 to Automatic (DHCP)."
echo "• If DNS issues → In NM Connection → IPv4 → DNS: 8.8.8.8,1.1.1.1, then reconnect."
echo "• If gateway unreachable → Move closer to AP, try 2.4GHz, reboot router."
echo "• If driver glitches (Realtek/Broadcom) → disable power saving: sudo iwconfig $WIFI_IF power off"
echo "• Quick reset: sudo systemctl restart NetworkManager && nmcli dev disconnect $WIFI_IF && nmcli dev connect $WIFI_IF"
