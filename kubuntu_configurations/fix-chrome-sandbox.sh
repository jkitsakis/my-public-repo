#!/usr/bin/env bash
set -e

echo "=============================================="
echo "🧩 Fix Chrome / Opera / Kodi Sandbox Problems"
echo "=============================================="

# 1️⃣ Enable unprivileged user namespaces
echo "[1/6] Enabling user namespaces..."
echo "kernel.unprivileged_userns_clone=1" | sudo tee /etc/sysctl.d/00-enable-userns.conf >/dev/null
sudo sysctl --system | grep userns_clone || true

# 2️⃣ Ensure lockdown=none in GRUB
echo "[2/6] Ensuring kernel lockdown=none..."
if grep -q 'GRUB_CMDLINE_LINUX="' /etc/default/grub; then
  sudo sed -i 's/GRUB_CMDLINE_LINUX="/GRUB_CMDLINE_LINUX="lockdown=none /' /etc/default/grub
else
  echo 'GRUB_CMDLINE_LINUX="lockdown=none"' | sudo tee -a /etc/default/grub
fi
sudo update-grub >/dev/null

# 3️⃣ Fix SUID permissions for sandbox helpers
echo "[3/6] Fixing SUID permissions..."
for bin in \
  /opt/google/chrome/chrome-sandbox \
  /usr/lib/x86_64-linux-gnu/opera/opera_sandbox \
  /usr/bin/bwrap; do
  if [ -f "$bin" ]; then
    echo " - Setting SUID on $bin"
    sudo chown root:root "$bin"
    sudo chmod 4755 "$bin"
  fi
done

# 4️⃣ Remount important dirs with suid allowed
echo "[4/6] Remounting /usr /var /tmp with suid..."
for dir in /usr /var /tmp; do
  sudo mount -o remount,suid "$dir" 2>/dev/null || true
done

# 5️⃣ Show current lockdown mode and userns setting
echo "[5/6] Checking kernel sandbox configuration..."
LOCKDOWN=$(cat /sys/kernel/security/lockdown 2>/dev/null || echo "unknown")
USERNS=$(cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null || echo "unknown")
echo " - lockdown: $LOCKDOWN"
echo " - userns_clone: $USERNS"

# 6️⃣ Final summary
echo "=============================================="
echo "✅ Fix complete. Please reboot your system now."
echo "   After reboot, verify with:"
echo "      cat /sys/kernel/security/lockdown"
echo "      google-chrome &"
echo "      opera &"
echo "      flatpak run tv.kodi.Kodi"
echo "=============================================="
