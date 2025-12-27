#!/usr/bin/env bash
set -euo pipefail

# === Config ===
NAS_HOST="192.168.8.50"
SHARE_NAME="Volume1"                         # change if your share name differs
SHARE="//${NAS_HOST}/${SHARE_NAME}"
MOUNTPOINT="${HOME}/NAS310S"
CRED_FILE="${HOME}/.smbcredentials-nas310s"  # file with username= / password=
PING_TIMEOUT_SECS=300     # 5 minutes
MOUNT_TIMEOUT_SECS=120    # 2 minutes

sudo apt update -qq
sudo apt install cifs-utils

echo "[NAS310S] SMB1 mount script started..."

# --- Preflight checks ---
if ! command -v mount.cifs >/dev/null 2>&1; then
  echo "[NAS310S] ERROR: 'cifs-utils' is not installed. Install it:  sudo apt install cifs-utils"
  exit 90
fi

# Wait for NAS to respond to ping
deadline=$((SECONDS + PING_TIMEOUT_SECS))
until ping -c1 -W1 "$NAS_HOST" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "[NAS310S] ERROR: NAS $NAS_HOST did not respond within ${PING_TIMEOUT_SECS}s."
    exit 1
  fi
  echo "[NAS310S] Waiting for NAS network..."
  sleep 5
done

# Ensure mountpoint exists
mkdir -p "$MOUNTPOINT"

# If already mounted, exit cleanly
if mountpoint -q "$MOUNTPOINT"; then
  echo "[NAS310S] Already mounted: $(findmnt -rno SOURCE "$MOUNTPOINT")"
  exit 0
fi

# Ensure credentials file exists
if [[ ! -f "$CRED_FILE" ]]; then
  cat <<EOF
[NAS310S] ERROR: Credentials file not found at $CRED_FILE

Create it with:
  printf "username=YOURUSER\npassword=YOURPASS\n" > "$CRED_FILE"
  chmod 600 "$CRED_FILE"

Then re-run this script.
EOF
  exit 3
fi
chmod 600 "$CRED_FILE" || true

# CIFS mount options (force SMB1)
uid=$(id -u)
gid=$(id -g)
BASE_OPTS="vers=1.0,credentials=${CRED_FILE},uid=${uid},gid=${gid},file_mode=0664,dir_mode=0775,noserverino,iocharset=utf8,nounix,soft"

echo "[NAS310S] Mounting $SHARE -> $MOUNTPOINT (SMB1, sec=ntlmv2)..."
set +e
sudo mount -t cifs "$SHARE" "$MOUNTPOINT" -o "sec=ntlmv2,${BASE_OPTS}"
rc=$?
set -e

# Fallback to NTLM (older NAS may need it)
if (( rc != 0 )); then
  echo "[NAS310S] First attempt failed (rc=$rc). Retrying with sec=ntlm..."
  set +e
  sudo mount -t cifs "$SHARE" "$MOUNTPOINT" -o "sec=ntlm,${BASE_OPTS}"
  rc=$?
  set -e
fi

if (( rc != 0 )); then
  echo "[NAS310S] ERROR: CIFS mount failed (rc=$rc). Diagnostics:"
  findmnt -T "$MOUNTPOINT" || true
  dmesg | tail -n 50 | sed 's/^/[kernel] /' || true
  echo "[NAS310S] Hints: If dmesg shows 'SMB1 disabled', your kernel/module may block SMB1 by default."
  exit 5
fi

# Wait until it's actually mounted
deadline=$((SECONDS + MOUNT_TIMEOUT_SECS))
until mountpoint -q "$MOUNTPOINT"; do
  if (( SECONDS >= deadline )); then
    echo "[NAS310S] ERROR: Mount did not appear within ${MOUNT_TIMEOUT_SECS}s."
    exit 6
  fi
  sleep 1
done

echo "[NAS310S] Mounted successfully!"
echo "[NAS310S] Source: $(findmnt -rno SOURCE "$MOUNTPOINT") | Type: $(findmnt -rno FSTYPE "$MOUNTPOINT")"
# Show a quick listing to prove contents are visible
ls -al "$MOUNTPOINT" | head -n 30
