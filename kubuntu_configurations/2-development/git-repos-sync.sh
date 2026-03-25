#!/usr/bin/env bash
set -u
set -o pipefail

# ============================================
# 🧰 PRODUCTION GRADE GIT REPO SYNC
# - clone/update multiple repos
# - logging
# - parallel execution
# - safe failure handling
# - git config + hooks + formatting
# ============================================

BASE_DIR="$HOME/Workspace/GitHub"
MAX_PARALLEL=2

REPOS=(
  "git@github.com:jkitsakis/my-private-repo.git"
  "git@github.com:jkitsakis/my-public-repo.git"
)

TIMESTAMP="$(date +'%Y%m%d_%H%M%S')"
RUN_DIR="$HOME/.local/share/git-repos-sync/$TIMESTAMP"
LOG_DIR="$RUN_DIR/logs"
STATUS_DIR="$RUN_DIR/status"
SUMMARY_FILE="$RUN_DIR/summary.txt"

mkdir -p "$BASE_DIR" "$LOG_DIR" "$STATUS_DIR"

# --------------------------------------------
# Helpers
# --------------------------------------------
log() {
  printf '[%s] %s\n' "$(date +'%F %T')" "$*"
}

sanitize_name() {
  local value="$1"
  value="${value##*/}"
  value="${value%.git}"
  printf '%s' "$value"
}

repo_default_branch() {
  local repo_dir="$1"
  local branch

  branch="$(git -C "$repo_dir" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  if [[ -n "$branch" ]]; then
    printf '%s\n' "${branch#origin/}"
    return 0
  fi

  branch="$(git -C "$repo_dir" remote show origin 2>/dev/null | awk '/HEAD branch/ {print $NF}' | head -n1)"
  if [[ -n "$branch" ]]; then
    printf '%s\n' "$branch"
    return 0
  fi

  printf '%s\n' "main"
}

has_command() {
  command -v "$1" >/dev/null 2>&1
}

# --------------------------------------------
# Global setup
# --------------------------------------------
install_dev_tools() {
  log "Installing developer tools..."

  if has_command python3; then
    python3 -m pip install --user pre-commit black >/dev/null 2>&1 || true
  fi

  if has_command npm; then
    npm install -g prettier >/dev/null 2>&1 || true
  fi

  if has_command sudo; then
    sudo apt-get update -y >/dev/null 2>&1 || true
    sudo apt-get install -y shfmt >/dev/null 2>&1 || true
  fi

  log "Developer tools step completed"
}

setup_git() {
  log "Applying global Git configuration..."

  git config --global user.name "yannis"
  git config --global user.email "yannis@users.noreply.github.com"

  git config --global i18n.commitEncoding utf-8
  git config --global i18n.logOutputEncoding utf-8

  # Cross-platform safe defaults
  git config --global core.autocrlf input
  git config --global core.eol lf
  git config --global core.longpaths true
  git config --global core.filemode false
  git config --global core.quotepath false

  git config --global pull.rebase true
  git config --global init.defaultBranch main
  git config --global fetch.prune true
  git config --global credential.helper store

  log "Global Git configuration applied"
}

check_ssh() {
  if ! has_command ssh; then
    log "ssh not found; skipping SSH connectivity pre-check"
    return 0
  fi

  log "Checking GitHub SSH connectivity..."
  if ssh -T -o BatchMode=yes -o ConnectTimeout=10 git@github.com >/dev/null 2>&1; then
    log "GitHub SSH connectivity OK"
    return 0
  fi

  log "GitHub SSH check returned non-zero. This may still be OK if GitHub rejects shell access but allows git operations."
  return 0
}

# --------------------------------------------
# Repo-specific setup
# --------------------------------------------
setup_repo_attributes() {
  local repo_dir="$1"

  cat > "$repo_dir/.gitattributes" <<'EOF'
* text=auto

*.sh text eol=lf
*.java text eol=lf
*.py text eol=lf
*.js text eol=lf
*.ts text eol=lf
*.tsx text eol=lf
*.jsx text eol=lf
*.css text eol=lf
*.scss text eol=lf
*.html text eol=lf
*.xml text eol=lf
*.yml text eol=lf
*.yaml text eol=lf
*.json text eol=lf
*.md text eol=lf
*.properties text eol=lf

*.bat text eol=crlf
*.cmd text eol=crlf
*.ps1 text eol=crlf

*.png binary
*.jpg binary
*.jpeg binary
*.gif binary
*.ico binary
*.pdf binary
*.jar binary
*.zip binary
*.tar binary
*.gz binary
*.tar.gz binary
EOF

  git -C "$repo_dir" add .gitattributes >/dev/null 2>&1 || true
  git -C "$repo_dir" commit -m "Add .gitattributes" >/dev/null 2>&1 || true
}

setup_precommit() {
  local repo_dir="$1"

  cat > "$repo_dir/.pre-commit-config.yaml" <<'EOF'
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-json
      - id: check-added-large-files

  - repo: https://github.com/psf/black
    rev: 24.4.2
    hooks:
      - id: black

  - repo: https://github.com/pre-commit/mirrors-prettier
    rev: v3.2.5
    hooks:
      - id: prettier
        types_or: [javascript, ts, json, yaml, css, html, jsx, tsx]

  - repo: https://github.com/mvdan/sh
    rev: v3.7.0
    hooks:
      - id: shfmt
EOF

  git -C "$repo_dir" add .pre-commit-config.yaml >/dev/null 2>&1 || true
  git -C "$repo_dir" commit -m "Add pre-commit config" >/dev/null 2>&1 || true
}

install_git_hooks() {
  local repo_dir="$1"

  if has_command pre-commit; then
    pre-commit install --install-hooks -c "$repo_dir/.pre-commit-config.yaml" -C "$repo_dir" >/dev/null 2>&1 || true
  fi
}

run_initial_format() {
  local repo_dir="$1"

  if has_command pre-commit; then
    pre-commit run --all-files -c "$repo_dir/.pre-commit-config.yaml" -C "$repo_dir" >/dev/null 2>&1 || true
    git -C "$repo_dir" add . >/dev/null 2>&1 || true
    git -C "$repo_dir" commit -m "Apply formatting standards" >/dev/null 2>&1 || true
  fi
}

ensure_origin_url() {
  local repo_dir="$1"
  local repo_url="$2"
  git -C "$repo_dir" remote set-url origin "$repo_url" >/dev/null 2>&1 || true
}

sync_one_repo() {
  local repo_url="$1"
  local repo_name repo_dir default_branch status_file
  repo_name="$(sanitize_name "$repo_url")"
  repo_dir="$BASE_DIR/$repo_name"
  status_file="$STATUS_DIR/$repo_name.status"

  {
    log "==== START $repo_name ===="
    log "Repo URL: $repo_url"
    log "Repo dir: $repo_dir"

    if [[ ! -d "$repo_dir/.git" ]]; then
      log "Cloning repository..."
      if ! git clone "$repo_url" "$repo_dir"; then
        log "Clone failed"
        echo "FAILED: clone" > "$status_file"
        exit 1
      fi

      ensure_origin_url "$repo_dir" "$repo_url"
      setup_repo_attributes "$repo_dir"
      setup_precommit "$repo_dir"
      install_git_hooks "$repo_dir"
      run_initial_format "$repo_dir"

      default_branch="$(repo_default_branch "$repo_dir")"
      log "Detected default branch: $default_branch"

      if [[ -n "$(git -C "$repo_dir" status --porcelain 2>/dev/null)" ]]; then
        log "Pushing initial repo hygiene changes..."
        git -C "$repo_dir" push -u origin "$default_branch" || true
      fi

      echo "OK: cloned" > "$status_file"
      log "==== END $repo_name ===="
      exit 0
    fi

    log "Repository already exists, updating..."
    ensure_origin_url "$repo_dir" "$repo_url"

    git -C "$repo_dir" fetch --all --prune
    default_branch="$(repo_default_branch "$repo_dir")"
    log "Detected default branch: $default_branch"

    current_branch="$(git -C "$repo_dir" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "$default_branch")"
    log "Current branch: $current_branch"

    if [[ "$current_branch" != "$default_branch" ]]; then
      log "Checking out default branch $default_branch"
      git -C "$repo_dir" checkout "$default_branch" >/dev/null 2>&1 || git -C "$repo_dir" checkout -b "$default_branch" "origin/$default_branch"
    fi

    git -C "$repo_dir" pull --rebase origin "$default_branch"

    setup_repo_attributes "$repo_dir"
    setup_precommit "$repo_dir"
    install_git_hooks "$repo_dir"

    if has_command pre-commit; then
      pre-commit run --all-files -c "$repo_dir/.pre-commit-config.yaml" -C "$repo_dir" >/dev/null 2>&1 || true
    fi

    if [[ -n "$(git -C "$repo_dir" status --porcelain 2>/dev/null)" ]]; then
      log "Changes detected, committing and pushing..."
      git -C "$repo_dir" add .
      git -C "$repo_dir" commit -m "Auto-sync: $(date +'%F %T')" || true
      git -C "$repo_dir" push origin "$default_branch"
      echo "OK: updated+push" > "$status_file"
    else
      log "No changes detected"
      echo "OK: up-to-date" > "$status_file"
    fi

    log "==== END $repo_name ===="
  } > "$LOG_DIR/$repo_name.log" 2>&1
}

run_parallel() {
  local -a pids=()
  local -a names=()
  local repo repo_name

  for repo in "${REPOS[@]}"; do
    repo_name="$(sanitize_name "$repo")"
    log "Queueing $repo_name"

    sync_one_repo "$repo" &
    pids+=("$!")
    names+=("$repo_name")

    while [[ "$(jobs -pr | wc -l)" -ge "$MAX_PARALLEL" ]]; do
      sleep 1
    done
  done

  local i rc failed=0
  for i in "${!pids[@]}"; do
    if wait "${pids[$i]}"; then
      log "Completed: ${names[$i]}"
    else
      log "Failed: ${names[$i]}"
      failed=1
    fi
  done

  return "$failed"
}

write_summary() {
  {
    echo "Git Repo Sync Summary"
    echo "Run directory: $RUN_DIR"
    echo "Base directory: $BASE_DIR"
    echo "Timestamp: $TIMESTAMP"
    echo
    for repo in "${REPOS[@]}"; do
      local repo_name
      repo_name="$(sanitize_name "$repo")"
      printf '%s -> ' "$repo_name"
      if [[ -f "$STATUS_DIR/$repo_name.status" ]]; then
        cat "$STATUS_DIR/$repo_name.status"
      else
        echo "UNKNOWN"
      fi
    done
    echo
    echo "Per-repo logs:"
    ls -1 "$LOG_DIR"
  } > "$SUMMARY_FILE"
}

# --------------------------------------------
# Main
# --------------------------------------------
main() {
  log "Run directory: $RUN_DIR"
  install_dev_tools
  setup_git
  check_ssh

  if run_parallel; then
    log "All repo jobs completed"
  else
    log "Some repo jobs failed"
  fi

  write_summary

  echo
  echo "📂 Base repo directory: $BASE_DIR"
  echo "🪵 Run logs: $LOG_DIR"
  echo "📄 Summary: $SUMMARY_FILE"
  echo

  cat "$SUMMARY_FILE"
}

main "$@"
