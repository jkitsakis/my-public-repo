#!/usr/bin/env bash
set -e

echo "🐍 Installing pyenv for managing multiple Python versions..."

# --- Install dependencies ---
echo "📦 Installing dependencies..."
sudo apt update -qq
sudo apt install -y \
  make build-essential libssl-dev zlib1g-dev libbz2-dev \
  libreadline-dev libsqlite3-dev wget curl llvm \
  libncurses5-dev libncursesw5-dev xz-utils tk-dev \
  libffi-dev liblzma-dev git ca-certificates

# --- Install pyenv (if not already present) ---
if [ ! -d "$HOME/.pyenv" ]; then
    echo "📥 Cloning pyenv repository..."
    git clone https://github.com/pyenv/pyenv.git ~/.pyenv
else
    echo "ℹ️ pyenv already exists, updating..."
    cd ~/.pyenv && git pull && cd -
fi

# --- Configure shell integration ---
if ! grep -q 'PYENV_ROOT' ~/.bashrc; then
    echo "🔧 Configuring pyenv environment in ~/.bashrc..."
    cat <<'EOF' >> ~/.bashrc

# >>> pyenv initialization >>>
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
if command -v pyenv >/dev/null 2>&1; then
  eval "$(pyenv init -)"
fi
# <<< pyenv initialization <<<
EOF
fi

# --- Load environment for current session ---
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"

echo "✅ pyenv installed and configured."
echo "🔁 Restart your terminal or run: source ~/.bashrc"
echo "👉 You can now use: pyenv install 3.12.6 , pyenv global 3.12.6 , pyenv versions"
