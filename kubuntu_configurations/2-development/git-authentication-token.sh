 
 #!/usr/bin/env bash
# ---------------------------------------------------------
# 🧹 GitHub Credential Reset Script
# Cleans old Git auth data and sets a new Personal Access Token
# ---------------------------------------------------------

# === Configuration ===
GITHUB_USER="jkitsakis"
GITHUB_TOKEN=""

echo "🔹 Cleaning existing GitHub credentials..."

# Remove stored credentials
git credential reject <<EOF
protocol=https
host=github.com
EOF

rm -f ~/.git-credentials
rm -f ~/.config/git/credentials

# Remove any global credential helper
git config --global --unset credential.helper 2>/dev/null

echo "✅ Old credentials removed."

# Configure new credential helper
git config --global credential.helper store

# Approve and store the new token
echo "🔐 Storing new GitHub token for $GITHUB_USER ..."
git credential approve <<EOF
protocol=https
host=github.com
username=$GITHUB_USER
password=$GITHUB_TOKEN
EOF

echo "✅ New token stored successfully."

# Test authentication
echo "🔍 Testing connection..."
if git ls-remote https://github.com/$GITHUB_USER/my-private-repo.git &>/dev/null; then
    echo "🎉 Authentication successful!"
else
    echo "❌ Authentication failed. Please check your token permissions."
fi

