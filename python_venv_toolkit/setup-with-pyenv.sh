#!/usr/bin/env bash
set -euo pipefail

VENV_DIR=".venv"

pause() {
  read -rp "Press Enter to continue..."
}

eval "$(pyenv init -)"


# Main menu loop
while true; do
  clear
  echo "1. Choose Python version and create $VENV_DIR"
  echo "2. Export requirements.txt"
  echo "3. Install from requirements.txt"
  echo "4. Exit"
  read -rp "Enter your choice: " CHOICE

  case "$CHOICE" in
    1)
      # CHOOSE_PYTHON
      clear
      echo "Searching for installed Python versions..."
      mapfile -t VERSIONS < <(pyenv versions --bare 2>/dev/null | sed '/^$/d')
      if [ "${#VERSIONS[@]}" -eq 0 ]; then
        echo "No Python versions found in pyenv."
        read -rp "Would you like to install one now? (y/N): " RESP
        if [[ "$RESP" =~ ^[Yy]$ ]]; then
          echo "Enter version to install (e.g., 3.11.5):"
          read -r VER
          if [ -n "$VER" ]; then
            if pyenv install "$VER"; then
              VERSIONS=("$VER")
            else
              echo "Failed to install $VER."
              pause
              continue
            fi
          else
            echo "No version entered."
            pause
            continue
          fi
        else
          pause
          continue
        fi
      fi

      echo
      echo "Select a Python version:"
      for idx in "${!VERSIONS[@]}"; do
        i=$((idx + 1))
        printf "%d. %s\n" "$i" "${VERSIONS[$idx]}"
      done

      read -rp "Enter the number of the Python version to use: " pychoice
      if ! [[ "$pychoice" =~ ^[0-9]+$ ]]; then
        echo "Invalid selection."
        pause
        continue
      fi
      if (( pychoice < 1 || pychoice > ${#VERSIONS[@]} )); then
        echo "Invalid selection."
        pause
        continue
      fi

      sel_version="${VERSIONS[$((pychoice-1))]}"
      echo "Using Python version: $sel_version"

      # Activate for this shell
      if ! pyenv shell "$sel_version"; then
        echo "Failed to activate version $sel_version."
        pause
        continue
      fi

      # Create venv if not exists
      if [ -d "$VENV_DIR" ]; then
        echo "Virtual environment already exists at $VENV_DIR."
      else
        python -m venv "$VENV_DIR"
        echo "Virtual environment created at $VENV_DIR."
      fi

      pause
      ;;
    2)
      # EXPORT_REQUIREMENTS
      if [ ! -f "${VENV_DIR}/bin/activate" ]; then
        echo "No virtual environment found. Please run option 1 first."
        pause
        continue
      fi

      # Activate venv
      # shellcheck disable=SC1090
      source "${VENV_DIR}/bin/activate"

      if ! pip show pipreqs >/dev/null 2>&1; then
        echo "Installing pipreqs..."
        pip install pipreqs
      fi

      echo "Exporting requirements.txt..."
      # Use the same flags as the Windows script for compatibility
      pipreqs . --force --savepath=requirements.txt --use-local --no-pin

      echo "requirements.txt exported."
      pause
      ;;
    3)
      # INSTALL_REQUIREMENTS
      if [ ! -f "requirements.txt" ]; then
        echo "requirements.txt not found."
        pause
        continue
      fi
      if [ ! -f "${VENV_DIR}/bin/activate" ]; then
        echo "No virtual environment found. Please run option 1 first."
        pause
        continue
      fi

      # Activate venv
      # shellcheck disable=SC1090
      source "${VENV_DIR}/bin/activate"
      echo "Installing from requirements.txt..."
      pip install -r requirements.txt
      echo "Installation complete."
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
