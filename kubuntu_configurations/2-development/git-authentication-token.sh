#!/usr/bin/env bash
set -euo pipefail

GITHUB_USER="jkitsakis"

read -s -p "🔐 Enter GitHub Token: " GITHUB_TOKEN
echo ""

echo "🔹 Cleaning existing GitHub credentials..."

git credential reject <<EOF
protocol=https
host=github.com
EOF

rm -f ~/.git-credentials ~/.config/git/credentials

git config --global --unset credential.helper 2>/dev/null || true

echo "✅ Old credentials removed."

git config --global credential.helper store

echo "🔐 Storing new GitHub token for $GITHUB_USER ..."

git credential approve <<EOF
protocol=https
host=github.com
username=$GITHUB_USER
password=$GITHUB_TOKEN
EOF

echo "🔍 Testing connection..."

if git ls-remote https://github.com/$GITHUB_USER/my-private-repo.git &>/dev/null; then
    echo "🎉 Authentication successful!"
else
    echo "❌ Authentication failed."
fi
