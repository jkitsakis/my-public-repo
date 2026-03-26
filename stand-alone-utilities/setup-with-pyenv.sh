#!/usr/bin/env bash
set -euo pipefail

# 🔥 FIX pyenv not working inside script
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"

if command -v pyenv >/dev/null 2>&1; then
	eval "$(pyenv init -)"
fi

VENV_DIR=".venv"
PROJECT_DIR="$(pwd -P)"

pause() {
	read -rp "Press Enter to continue..."
}

print_header() {
	clear
	echo "=============================="
	echo "🐍 Pyenv Smart Venv Manager"
	echo "📁 Project: $PROJECT_DIR"
	if [ -d "$VENV_DIR" ]; then
		echo "📦 Venv: ✅ exists"
	else
		echo "📦 Venv: ❌ missing"
	fi
	echo "=============================="
}

activate_venv() {
	if [ ! -f "${VENV_DIR}/bin/activate" ]; then
		echo "No virtual environment found."
		return 1
	fi
	source "${VENV_DIR}/bin/activate"
}

get_pyenv_versions() {
	pyenv versions --bare 2>/dev/null | sed '/^$/d'
}

get_local_version() {
	if [ -f ".python-version" ]; then
		cat .python-version
	else
		echo ""
	fi
}

select_or_auto_pyenv() {
	local versions
	mapfile -t versions < <(get_pyenv_versions)

	if [ "${#versions[@]}" -eq 0 ]; then
		echo "❌ No pyenv versions installed."
		return 1
	fi

	local local_version
	local_version="$(get_local_version)"

	# Priority 1: .python-version
	if [ -n "$local_version" ]; then
		echo "📌 Using project Python (.python-version): $local_version"

		if ! pyenv versions --bare | grep -q "^$local_version$"; then
			echo "⚠️ Version not installed. Installing..."
			pyenv install "$local_version"
		fi

		pyenv shell "$local_version"
		return 0
	fi

	# Priority 2: only one version → auto
	if [ "${#versions[@]}" -eq 1 ]; then
		echo "📌 Only one version found → auto selecting: ${versions[0]}"
		pyenv shell "${versions[0]}"
		return 0
	fi

	# Priority 3: multiple → user selects
	echo "Select Python version:"
	for i in "${!versions[@]}"; do
		printf "%d) %s\n" "$((i + 1))" "${versions[$i]}"
	done

	echo
	read -rp "Choice: " choice

	if ! [[ "$choice" =~ ^[0-9]+$ ]] || ((choice < 1 || choice > ${#versions[@]})); then
		echo "Invalid selection"
		return 1
	fi

	local selected="${versions[$((choice - 1))]}"
	echo "✅ Selected: $selected"
	pyenv shell "$selected"
}

create_venv() {
	select_or_auto_pyenv || return 1

	echo "Creating virtual environment..."
	rm -rf "$VENV_DIR"
	python -m venv "$VENV_DIR"

	source "${VENV_DIR}/bin/activate"
	python -m pip install --upgrade pip --quiet

	echo "✅ venv created with $(python -V)"
}

install_requirements() {

	if [ ! -f "${VENV_DIR}/bin/activate" ]; then
		echo "⚠️ No venv found → creating automatically..."
		create_venv || return 1
	fi

	activate_venv || return 1

	if [ ! -f "requirements.txt" ]; then
		echo "No requirements.txt found"
		return 1
	fi

	python -m pip install -r requirements.txt \
		--prefer-binary \
		--disable-pip-version-check
}

export_requirements() {
	activate_venv || return 1
	python -m pip list --format=freeze >requirements.txt
	echo "✅ requirements.txt updated"
}

show_info() {
	print_header
	echo "pyenv global: $(pyenv global 2>/dev/null || echo 'N/A')"
	echo "pyenv local : $(get_local_version || echo 'N/A')"
	echo

	if activate_venv; then
		echo "Venv Python:"
		python -V
		which python
	fi
	pause
}

# MENU
while true; do
	print_header
	echo "1) Create .venv (smart pyenv)"
	echo "2) Install requirements.txt"
	echo "3) Export requirements.txt"
	echo "4) Show environment info"
	echo "5) Exit"
	echo

	read -rp "Choice: " choice

	case "$choice" in
	1)
		create_venv
		pause
		;;
	2)
		install_requirements
		pause
		;;
	3)
		export_requirements
		pause
		;;
	4)
		show_info
		;;
	5)
		exit 0
		;;
	*)
		echo "Invalid choice"
		pause
		;;
	esac
done
