#!/usr/bin/env bash
# Install/configure NetworkManager to use iwd on Ubuntu/Kubuntu.
# Usage:
#   sudo ./install-configure-iwd.sh install
#   sudo ./install-configure-iwd.sh status
#   sudo ./install-configure-iwd.sh rollback

set -Eeuo pipefail

ACTION="${1:-install}"
NM_CONF_DIR="/etc/NetworkManager/conf.d"
IWD_BACKEND_CONF="${NM_CONF_DIR}/wifi_backend.conf"
BACKUP_DIR="/var/backups/wifi-iwd-switch"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
RUN_BACKUP_DIR="${BACKUP_DIR}/${TIMESTAMP}"
LOG_TAG="iwd-installer"

log() {
    printf '[%s] %s\n' "$(date '+%F %T')" "$*"
    logger -t "$LOG_TAG" -- "$*" 2>/dev/null || true
}

fail() {
    log "ERROR: $*"
    exit 1
}

require_root() {
    [[ "${EUID}" -eq 0 ]] || fail "Run this script with sudo."
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

wait_for_service() {
    local service="$1"
    local timeout_seconds="${2:-20}"
    local elapsed=0

    while (( elapsed < timeout_seconds )); do
        if systemctl is-active --quiet "$service"; then
            return 0
        fi
        sleep 1
        ((elapsed += 1))
    done
    return 1
}

show_status() {
    echo
    echo "=== Package status ==="
    dpkg-query -W -f='iwd: ${Status} ${Version}\n' iwd 2>/dev/null || echo "iwd: not installed"
    dpkg-query -W -f='NetworkManager: ${Status} ${Version}\n' network-manager 2>/dev/null || true

    echo
    echo "=== Backend configuration ==="
    if [[ -f "$IWD_BACKEND_CONF" ]]; then
        cat "$IWD_BACKEND_CONF"
    else
        echo "$IWD_BACKEND_CONF does not exist."
    fi

    echo
    echo "=== Service status ==="
    systemctl is-active iwd 2>/dev/null || true
    systemctl is-enabled iwd 2>/dev/null || true
    systemctl is-active NetworkManager 2>/dev/null || true
    systemctl is-active wpa_supplicant 2>/dev/null || true

    echo
    echo "=== NetworkManager/iwd evidence from current boot ==="
    journalctl -u NetworkManager -b --no-pager 2>/dev/null |
        grep -Ei 'iwd|wifi.backend|new IWD device state' |
        tail -n 30 || true

    echo
    echo "=== Current Wi-Fi devices ==="
    nmcli -f DEVICE,TYPE,STATE,CONNECTION device status 2>/dev/null || true
}

install_iwd_backend() {
    require_root
    require_command apt-get
    require_command systemctl
    require_command nmcli
    require_command journalctl

    log "Creating backup directory: $RUN_BACKUP_DIR"
    mkdir -p "$RUN_BACKUP_DIR"

    if [[ -f "$IWD_BACKEND_CONF" ]]; then
        cp -a "$IWD_BACKEND_CONF" "$RUN_BACKUP_DIR/wifi_backend.conf.before"
    fi

    if [[ -d /etc/iwd ]]; then
        cp -a /etc/iwd "$RUN_BACKUP_DIR/iwd.before" 2>/dev/null || true
    fi

    systemctl is-enabled wpa_supplicant.service >"$RUN_BACKUP_DIR/wpa_supplicant.enabled.before" 2>&1 || true
    systemctl is-active wpa_supplicant.service >"$RUN_BACKUP_DIR/wpa_supplicant.active.before" 2>&1 || true

    log "Installing iwd."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y iwd

    mkdir -p "$NM_CONF_DIR"
    cat >"$IWD_BACKEND_CONF" <<'CONF'
# Managed by install-configure-iwd.sh
# NetworkManager remains responsible for IP addressing/DHCP.
[device]
wifi.backend=iwd
CONF
    chmod 0644 "$IWD_BACKEND_CONF"

    # Do not enable iwd's own network configuration; NetworkManager owns DHCP/DNS.
    mkdir -p /etc/iwd
    if [[ ! -f /etc/iwd/main.conf ]]; then
        cat >/etc/iwd/main.conf <<'CONF'
# Managed by install-configure-iwd.sh
[General]
EnableNetworkConfiguration=false
CONF
        chmod 0644 /etc/iwd/main.conf
    elif ! grep -Eq '^\s*EnableNetworkConfiguration\s*=' /etc/iwd/main.conf; then
        cat >>/etc/iwd/main.conf <<'CONF'

# Added by install-configure-iwd.sh
[General]
EnableNetworkConfiguration=false
CONF
    fi

    log "Enabling and starting iwd."
    systemctl enable --now iwd.service

    # With the iwd backend selected, prevent a separately started global
    # wpa_supplicant service from competing for the same Wi-Fi device.
    log "Stopping and disabling the global wpa_supplicant service."
    systemctl disable --now wpa_supplicant.service 2>/dev/null || true

    log "Restarting NetworkManager. Wi-Fi will disconnect briefly."
    systemctl restart NetworkManager.service

    wait_for_service iwd.service 20 || fail "iwd did not become active."
    wait_for_service NetworkManager.service 20 || fail "NetworkManager did not become active."

    sleep 3

    if ! journalctl -u NetworkManager -b --no-pager |
        grep -Eq 'new IWD device state|/net/connman/iwd'; then
        log "WARNING: NetworkManager is active, but iwd backend evidence was not found yet."
        log "Try reconnecting Wi-Fi, then run: sudo $0 status"
    else
        log "NetworkManager is using iwd."
    fi

    log "Installation completed. Backup: $RUN_BACKUP_DIR"
    show_status
}

rollback_iwd_backend() {
    require_root
    require_command systemctl

    log "Removing NetworkManager iwd backend override."
    rm -f "$IWD_BACKEND_CONF"

    log "Stopping and disabling iwd."
    systemctl disable --now iwd.service 2>/dev/null || true

    log "Enabling and starting wpa_supplicant."
    systemctl enable --now wpa_supplicant.service

    log "Restarting NetworkManager. Wi-Fi will disconnect briefly."
    systemctl restart NetworkManager.service

    wait_for_service NetworkManager.service 20 || fail "NetworkManager did not become active."
    log "Rollback completed. NetworkManager will use its default wpa_supplicant backend."
    show_status
}

case "$ACTION" in
    install)
        install_iwd_backend
        ;;
    status)
        require_root
        show_status
        ;;
    rollback|uninstall|revert)
        rollback_iwd_backend
        ;;
    *)
        cat <<USAGE
Usage:
  sudo $0 install
  sudo $0 status
  sudo $0 rollback
USAGE
        exit 2
        ;;
esac
