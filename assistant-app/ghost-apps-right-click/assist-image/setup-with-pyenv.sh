#!/usr/bin/env bash
set -euo pipefail

VENV_DIR=".venv"
PROJECT_DIR="$(pwd -P)"

# Initialize pyenv for non-interactive shell
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"

if command -v pyenv >/dev/null 2>&1; then
  eval "$(pyenv init --path)"
  eval "$(pyenv init -)"
fi


pause() {
  read -rp "Press Enter to continue..."
}

activate_venv() {
  if [ ! -f "${VENV_DIR}/bin/activate" ]; then
    echo "No virtual environment found at ${VENV_DIR}. Run option 1 first."
    return 1
  fi
  # shellcheck disable=SC1090
  source "${VENV_DIR}/bin/activate"
}

create_venv_with_py() {
  local pyexe="$1"
  if ! command -v "$pyexe" >/dev/null 2>&1; then
    echo "Python not found: $pyexe"
    return 1
  fi
  "$pyexe" -m venv "$VENV_DIR"
  # shellcheck disable=SC1090
  source "${VENV_DIR}/bin/activate"
  python -m pip install --upgrade pip
  echo "✅ venv created & activated at $VENV_DIR (Python $(python -V))"
}

export_requirements() {
  activate_venv || return 1
  python -m pip install --upgrade pip >/dev/null
  echo "Exporting requirements.txt from ${PROJECT_DIR} ..."
  python -m pip freeze > "${PROJECT_DIR}/requirements.txt"
  echo "✅ Created ${PROJECT_DIR}/requirements.txt via pip freeze (full environment)."

}

install_requirements() {
  activate_venv || return 1
  if [ ! -f "${PROJECT_DIR}/requirements.txt" ]; then
    echo "requirements.txt not found in ${PROJECT_DIR}"
    return 1
  fi
  echo "Installing from requirements.txt..."
  python -m pip install -r "${PROJECT_DIR}/requirements.txt"
  echo "✅ Installation complete."
}

# Main menu loop
while true; do
  clear
  echo "1. Choose Python version and create ${VENV_DIR}"
  echo "2. Export requirements.txt"
  echo "3. Install from requirements.txt"
  echo "4. Exit"
  read -rp "Enter your choice: " CHOICE

  case "$CHOICE" in
    1)
      clear
      echo "Searching for installed Python versions (pyenv)..."
      if command -v pyenv >/dev/null 2>&1; then
        mapfile -t VERSIONS < <(pyenv versions --bare 2>/dev/null | sed '/^$/d' || true)
      else
        VERSIONS=()
      fi

      if [ "${#VERSIONS[@]}" -eq 0 ]; then
        echo "No pyenv versions found (or pyenv not installed)."
        echo "You can enter a system Python executable instead (e.g., python3.12 or /usr/bin/python3)."
        read -rp "Python executable to use: " PYEXE
        if [ -z "${PYEXE:-}" ]; then
          echo "No Python entered."
          pause; continue
        fi
        create_venv_with_py "$PYEXE" || { pause; continue; }
      else
        echo
        echo "Select a Python version:"
        for idx in "${!VERSIONS[@]}"; do
          printf "%d. %s\n" "$((idx+1))" "${VERSIONS[$idx]}"
        done
        read -rp "Enter the number of the Python version to use: " pychoice
        if ! [[ "$pychoice" =~ ^[0-9]+$ ]] || (( pychoice < 1 || pychoice > ${#VERSIONS[@]} )); then
          echo "Invalid selection."
          pause; continue
        fi
        sel_version="${VERSIONS[$((pychoice-1))]}"
        echo "Using pyenv Python: $sel_version"
        # Activate pyenv version for this shell process
        if ! pyenv shell "$sel_version"; then
          echo "Failed to activate pyenv version $sel_version."
          pause; continue
        fi
        create_venv_with_py "python" || { pause; continue; }
      fi

      # If venv already exists, inform
      if [ -d "$VENV_DIR" ]; then
        echo "Virtual environment is ready at $VENV_DIR."
      fi
      pause
      ;;
    2)
      export_requirements || true
      pause
      ;;
    3)
      install_requirements || true
      pause
      ;;
    4)
      exit 0
      ;;
    *)
      echo "Invalid choice."
      pause
      ;;
  esac
done
