#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_PATH="${HOME}/.local/bin/restart-plasmashell.sh"
AUTOSTART_PATH="${HOME}/.config/autostart/restart-plasmashell.desktop"
LOG_PATH="${HOME}/.local/state/restart-plasmashell.log"
DELAY_SECONDS="${PLASMA_RESTART_DELAY:-5}"

install_workaround() {
    echo "Installing Plasma dual-monitor startup workaround..."

    mkdir -p "${HOME}/.local/bin"
    mkdir -p "${HOME}/.config/autostart"
    mkdir -p "${HOME}/.local/state"

    cat > "${SCRIPT_PATH}" <<EOF
#!/usr/bin/env bash
set -u

DELAY_SECONDS="\${PLASMA_RESTART_DELAY:-${DELAY_SECONDS}}"
LOG_PATH="\${HOME}/.local/state/restart-plasmashell.log"

mkdir -p "\$(dirname "\${LOG_PATH}")"

{
    echo
    echo "=== \$(date --iso-8601=seconds) ==="
    echo "Waiting \${DELAY_SECONDS} seconds for KScreen..."
} >> "\${LOG_PATH}"

sleep "\${DELAY_SECONDS}"

# Stop the Plasma instance managed by the user systemd session.
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user stop plasma-plasmashell.service >> "\${LOG_PATH}" 2>&1 || true
fi

# Ensure no old instance remains.
pkill -x plasmashell >> "\${LOG_PATH}" 2>&1 || true
sleep 1

# Prefer the Plasma systemd service when available.
if command -v systemctl >/dev/null 2>&1 &&
   systemctl --user list-unit-files plasma-plasmashell.service >/dev/null 2>&1; then
    systemctl --user start plasma-plasmashell.service >> "\${LOG_PATH}" 2>&1
else
    nohup /usr/bin/plasmashell --no-respawn >> "\${LOG_PATH}" 2>&1 &
fi

echo "Plasma Shell restart completed." >> "\${LOG_PATH}"
EOF

    chmod +x "${SCRIPT_PATH}"

    cat > "${AUTOSTART_PATH}" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Restart Plasma Shell
Comment=Restart Plasma Shell after KScreen initializes the monitors
Exec=${SCRIPT_PATH}
Terminal=false
OnlyShowIn=KDE;
X-KDE-Autostart-after=panel
X-KDE-StartupNotify=false
EOF

    chmod 644 "${AUTOSTART_PATH}"

    echo
    echo "Installation completed."
    echo "Restart script: ${SCRIPT_PATH}"
    echo "Autostart entry: ${AUTOSTART_PATH}"
    echo "Log file:        ${LOG_PATH}"
    echo
    echo "Log out and log in again to test it."
    echo "Default delay: ${DELAY_SECONDS} seconds."
}

remove_workaround() {
    rm -f "${AUTOSTART_PATH}" "${SCRIPT_PATH}"
    echo "The Plasma restart workaround was removed."
}

test_workaround() {
    if [[ ! -x "${SCRIPT_PATH}" ]]; then
        echo "The workaround is not installed." >&2
        exit 1
    fi

    echo "Running the restart script now..."
    "${SCRIPT_PATH}"
}

show_status() {
    if [[ -x "${SCRIPT_PATH}" && -f "${AUTOSTART_PATH}" ]]; then
        echo "Status: installed"
        echo "Restart script: ${SCRIPT_PATH}"
        echo "Autostart entry: ${AUTOSTART_PATH}"
        echo "Log file:        ${LOG_PATH}"
    else
        echo "Status: not fully installed"
        [[ -e "${SCRIPT_PATH}" ]] && echo "Restart script exists: ${SCRIPT_PATH}"
        [[ -e "${AUTOSTART_PATH}" ]] && echo "Autostart entry exists: ${AUTOSTART_PATH}"
    fi
}

case "${1:-install}" in
    install)
        install_workaround
        ;;
    remove|uninstall)
        remove_workaround
        ;;
    test)
        test_workaround
        ;;
    status)
        show_status
        ;;
    *)
        echo "Usage: $0 [install|remove|test|status]" >&2
        exit 2
        ;;
esac
