#!/usr/bin/env bash
set -Eeuo pipefail

REMOTE="GDrive:"
MOUNT_DIR="${HOME}/GDrive"
LOG_FILE="${HOME}/rclone-gdrive-mount.log"

info() {
  echo "ℹ️  $*"
}

ok() {
  echo "✅ $*"
}

warn() {
  echo "⚠️  $*"
}

fail() {
  echo "❌ $*" >&2
  exit 1
}

command -v rclone >/dev/null 2>&1 || fail "rclone is not installed or not in PATH."
command -v fusermount >/dev/null 2>&1 || warn "fusermount not found. Will try umount fallback if needed."

info "Restarting rclone mount..."
info "Remote: ${REMOTE}"
info "Mount directory: ${MOUNT_DIR}"

mkdir -p "${MOUNT_DIR}"

# Stop any existing rclone process for this exact remote/mount combination.
if pgrep -f "rclone mount ${REMOTE} ${MOUNT_DIR}" >/dev/null 2>&1; then
  info "Stopping existing rclone mount process..."
  pkill -f "rclone mount ${REMOTE} ${MOUNT_DIR}" || true
  sleep 2
fi

# Clean stale FUSE mount if the directory is still mounted.
if mountpoint -q "${MOUNT_DIR}"; then
  info "Unmounting existing mountpoint..."
  if command -v fusermount >/dev/null 2>&1; then
    fusermount -uz "${MOUNT_DIR}" || true
  fi

  if mountpoint -q "${MOUNT_DIR}"; then
    umount -l "${MOUNT_DIR}" || true
  fi

  sleep 1
fi

if mountpoint -q "${MOUNT_DIR}"; then
  fail "Could not unmount existing mount at ${MOUNT_DIR}. Close open files/terminals and retry."
fi

info "Starting new rclone mount..."
rclone mount "${REMOTE}" "${MOUNT_DIR}" \
  --vfs-cache-mode writes \
  --dir-cache-time 72h \
  --poll-interval 15s \
  --log-file "${LOG_FILE}" \
  --log-level INFO \
  --daemon

sleep 2

if mountpoint -q "${MOUNT_DIR}"; then
  ok "Mounted ${REMOTE} at ${MOUNT_DIR}"
  info "Log file: ${LOG_FILE}"
else
  fail "Mount command started but ${MOUNT_DIR} is not mounted. Check log: ${LOG_FILE}"
fi
