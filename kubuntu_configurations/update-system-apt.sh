#!/usr/bin/env bash
set -e

echo "🩺 APT DOCTOR STARTING..."

BACKUP_DIR="/etc/apt/sources.list.d/backup_$(date +%s)"
sudo mkdir -p "$BACKUP_DIR"

########################################
# 1. FIX DUPLICATE REPOS
########################################
echo "🔍 Checking duplicate repositories..."

normalize() {
	sed -E 's/#.*//' | sed -E 's/[[:space:]]+/ /g' | sed -E 's/^ +| +$//g'
}

declare -A seen

for file in /etc/apt/sources.list /etc/apt/sources.list.d/*.list; do
	[ -f "$file" ] || continue

	mapfile -t lines < <(normalize <"$file" | grep -E '^deb ' || true)

	new_lines=()
	changed=false

	for line in "${lines[@]}"; do
		if [[ -z "${seen[$line]}" ]]; then
			seen[$line]=1
			new_lines+=("$line")
		else
			echo "⚠️ Duplicate repo: $line"
			changed=true
		fi
	done

	if [ "$changed" = true ]; then
		echo "🛠 Fixing $file"
		sudo cp "$file" "$BACKUP_DIR/"

		{
			grep '^#' "$file" || true
			for l in "${new_lines[@]}"; do
				echo "$l"
			done
		} | sudo tee "$file" >/dev/null
	fi
done

########################################
# 2. REMOVE KNOWN DUPLICATE FILES
########################################
echo "🧹 Cleaning known duplicate repo files..."

sudo rm -f /etc/apt/sources.list.d/google-chrome.list 2>/dev/null || true
sudo rm -f /etc/apt/sources.list.d/opera-stable.list 2>/dev/null || true

########################################
# 3. CHECK BROKEN REPOS
########################################
echo "🔍 Checking for broken repositories..."

APT_OUTPUT=$(sudo apt update 2>&1 || true)

echo "$APT_OUTPUT" | grep -E "404|NO_PUBKEY|EXPKEYSIG" && BROKEN=true || BROKEN=false

########################################
# 4. AUTO-FIX REPOS (if broken)
########################################
if [ "$BROKEN" = true ]; then
	echo "🚨 Broken repos detected — repairing..."

	# Chrome
	if ! grep -q "dl.google.com" /etc/apt/sources.list.d/*.list 2>/dev/null; then
		echo "🔧 Reinstalling Chrome repo..."
		wget -q -O - https://dl.google.com/linux/linux_signing_key.pub |
			sudo gpg --dearmor -o /usr/share/keyrings/google.gpg

		echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/google.gpg] http://dl.google.com/linux/chrome/deb/ stable main" |
			sudo tee /etc/apt/sources.list.d/google.list
	fi

	# Opera
	if ! grep -q "deb.opera.com" /etc/apt/sources.list.d/*.list 2>/dev/null; then
		echo "🔧 Reinstalling Opera repo..."
		wget -qO- https://deb.opera.com/archive.key |
			sudo gpg --dearmor -o /usr/share/keyrings/opera.gpg

		echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/opera.gpg] https://deb.opera.com/opera-stable/ stable non-free" |
			sudo tee /etc/apt/sources.list.d/opera.list
	fi

	# AnyDesk
	if ! grep -q "anydesk" /etc/apt/sources.list.d/*.list 2>/dev/null; then
		echo "🔧 Reinstalling AnyDesk repo..."
		wget -qO- https://keys.anydesk.com/repos/DEB-GPG-KEY |
			sudo gpg --dearmor -o /usr/share/keyrings/anydesk.gpg

		echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/anydesk.gpg] http://deb.anydesk.com/ all main" |
			sudo tee /etc/apt/sources.list.d/anydesk.list
	fi

	# LibreOffice
	if ! grep -q "libreoffice" /etc/apt/sources.list.d/*.list 2>/dev/null; then
		echo "🔧 Reinstalling LibreOffice PPA..."
		sudo add-apt-repository -y ppa:libreoffice/ppa
	fi
fi

########################################
# 5. FINAL UPDATE & UPGRADE
########################################
echo "🔄 Final APT update..."
sudo apt update

echo "⬆️ Upgrading system..."
sudo apt full-upgrade -y

echo "🧹 Cleaning..."
sudo apt autoremove --purge -y
sudo apt clean

########################################
# 6. SNAP & FLATPAK
########################################
echo "📦 Snap update..."
sudo snap refresh

echo "🎯 Flatpak update..."
flatpak update -y

########################################
# 7. FIRMWARE
########################################
echo "🔌 Firmware update..."
if command -v fwupdmgr &>/dev/null; then
	sudo fwupdmgr refresh --force
	sudo fwupdmgr update -y
fi

########################################
# 8. HEALTH REPORT
########################################
echo "📊 SYSTEM HEALTH REPORT"
echo "------------------------"

echo "APT upgradable:"
apt -qq list --upgradable || true

echo ""
echo "Disk usage:"
df -h /

echo ""
echo "Memory:"
free -h

echo ""
echo "Kernel:"
uname -r

echo ""
echo "✅ APT DOCTOR COMPLETED!"
echo "Backup: $BACKUP_DIR"
