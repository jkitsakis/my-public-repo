#!/usr/bin/env bash
set -euo pipefail

# =========================================================
# 🚀 ENTERPRISE GIT ORCHESTRATOR (NO PRE-COMMIT)
# =========================================================

BASE_DIR="$HOME/Workspace/GitHub"
MAX_PARALLEL=2

REPOS=(
	"git@github.com:jkitsakis/my-private-repo.git|https://github.com/jkitsakis/my-private-repo.git"
	"git@github.com:jkitsakis/my-public-repo.git|https://github.com/jkitsakis/my-public-repo.git"
)

TIMESTAMP="$(date +'%Y%m%d_%H%M%S')"
RUN_DIR="$HOME/.local/share/git-orchestrator/$TIMESTAMP"
LOG_DIR="$RUN_DIR/logs"
STATUS_DIR="$RUN_DIR/status"
SUMMARY_FILE="$RUN_DIR/summary.txt"

mkdir -p "$BASE_DIR" "$LOG_DIR" "$STATUS_DIR"

# =========================================================
# LOGGING
# =========================================================
log() {
	printf '[%s] %s\n' "$(date +'%F %T')" "$*"
}

sanitize_name() {
	local v="$1"
	v="${v##*/}"
	v="${v%.git}"
	printf '%s' "$v"
}

has_command() {
	command -v "$1" >/dev/null 2>&1
}

ssh_available() {
	ssh -T -o BatchMode=yes -o ConnectTimeout=5 git@github.com >/dev/null 2>&1
}

repo_default_branch() {
	local d="$1"
	git -C "$d" remote set-head origin -a >/dev/null 2>&1 || true
	git -C "$d" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|origin/||' || echo "main"
}

# =========================================================
# GLOBAL SETUP
# =========================================================
setup_git() {
	log "Configuring Git..."

	git config --global user.name "yannis"
	git config --global user.email "yannis@users.noreply.github.com"

	git config --global core.autocrlf input
	git config --global core.eol lf
	git config --global core.longpaths true
	git config --global core.filemode false

	git config --global pull.rebase true
	git config --global fetch.prune true
	git config --global credential.helper cache
}

install_dev_tools() {
	log "Installing tools..."

	has_command python3 && python3 -m pip install --user black >/dev/null 2>&1 || true
	has_command npm && npm install -g prettier >/dev/null 2>&1 || true
	sudo apt-get update -y >/dev/null 2>&1 || true
	sudo apt-get install -y shfmt >/dev/null 2>&1 || true
}

# =========================================================
# CORE SYNC
# =========================================================
sync_repo() {
	local pair="$1"

	local SSH_URL="${pair%%|*}"
	local HTTPS_URL="${pair##*|}"
	local URL="$SSH_URL"

	if ! ssh_available; then
		URL="$HTTPS_URL"
	fi

	local NAME
	NAME="$(sanitize_name "$SSH_URL")"

	local DIR="$BASE_DIR/$NAME"
	local STATUS_FILE="$STATUS_DIR/$NAME.status"

	{
		log "[$NAME] START"

		# ---------------------------------
		# CLONE
		# ---------------------------------
		if [[ ! -d "$DIR/.git" ]]; then
			log "[$NAME] Cloning..."

			if ! git clone "$URL" "$DIR"; then
				log "[$NAME] SSH failed → retry HTTPS"
				if ! git clone "$HTTPS_URL" "$DIR"; then
					log "[$NAME] ❌ CLONE FAILED"
					echo "FAILED: clone" >"$STATUS_FILE"
					exit 1
				fi
			fi

			echo "OK: cloned" >"$STATUS_FILE"
			log "[$NAME] CLONED"
			exit 0
		fi

		cd "$DIR"

		# ---------------------------------
		# REMOVE ANY GIT HOOKS (NO PRE-COMMIT EVER)
		# ---------------------------------
		rm -f .git/hooks/pre-commit .git/hooks/pre-push .git/hooks/commit-msg || true

		# ---------------------------------
		# STASH
		# ---------------------------------
		STASHED=0
		if [[ -n "$(git status --porcelain)" ]]; then
			log "[$NAME] Stashing changes"
			git stash push -u -m "auto-sync" || true
			STASHED=1
		fi

		# ---------------------------------
		# FETCH
		# ---------------------------------
		git fetch --all --prune

		BRANCH="$(repo_default_branch "$DIR")"

		git checkout "$BRANCH" >/dev/null 2>&1 || git checkout -b "$BRANCH" "origin/$BRANCH"

		# ---------------------------------
		# SAFE SYNC
		# ---------------------------------
		log "[$NAME] Safe sync..."

		if ! git rebase "origin/$BRANCH"; then
			log "[$NAME] Rebase failed → fallback merge"
			git rebase --abort || true

			if ! git merge --no-edit "origin/$BRANCH"; then
				log "[$NAME] ❌ MERGE FAILED"
				echo "FAILED: conflict" >"$STATUS_FILE"
				exit 1
			fi
		fi

		# ---------------------------------
		# RESTORE STASH
		# ---------------------------------
		if [[ "$STASHED" -eq 1 ]]; then
			log "[$NAME] Restoring stash"
			git stash pop || log "[$NAME] ⚠️ Stash conflict"
		fi

		# ---------------------------------
		# OPTIONAL FORMATTING (SAFE)
		# ---------------------------------
		has_command black && black . >/dev/null 2>&1 || true
		has_command prettier && prettier --write . >/dev/null 2>&1 || true
		has_command shfmt && shfmt -w . >/dev/null 2>&1 || true

		# ---------------------------------
		# COMMIT / PUSH (NO HOOKS WILL RUN)
		# ---------------------------------
		if [[ -n "$(git status --porcelain)" ]]; then
			log "[$NAME] Committing changes"

			git add .
			git commit -m "Auto-sync $(date +'%F %T')" || true

			if ! git push origin "$BRANCH"; then
				log "[$NAME] ⚠️ Push failed"
			fi

			echo "OK: updated" >"$STATUS_FILE"
		else
			echo "OK: clean" >"$STATUS_FILE"
		fi

		log "[$NAME] DONE"

	} | tee "$LOG_DIR/$NAME.log"
}

# =========================================================
# PARALLEL EXECUTION
# =========================================================
run_parallel() {
	local pids=()
	local names=()

	for repo in "${REPOS[@]}"; do
		name="$(sanitize_name "${repo%%|*}")"
		log "Queueing $name"

		sync_repo "$repo" &
		pids+=($!)
		names+=("$name")

		while [[ $(jobs -pr | wc -l) -ge $MAX_PARALLEL ]]; do
			sleep 1
		done
	done

	local failed=0

	for i in "${!pids[@]}"; do
		if wait "${pids[$i]}"; then
			log "Completed: ${names[$i]}"
		else
			log "Failed: ${names[$i]}"
			failed=1
		fi
	done

	return $failed
}

# =========================================================
# SUMMARY
# =========================================================
summary() {
	{
		echo "Git Orchestrator Summary"
		echo "Run: $RUN_DIR"
		echo

		for repo in "${REPOS[@]}"; do
			name="$(sanitize_name "${repo%%|*}")"
			printf '%s -> ' "$name"
			cat "$STATUS_DIR/$name.status" 2>/dev/null || echo "UNKNOWN"
		done

		echo
		echo "Logs:"
		ls -1 "$LOG_DIR"
	} >"$SUMMARY_FILE"

	cat "$SUMMARY_FILE"
}

# =========================================================
# MAIN
# =========================================================
main() {
	log "Run: $RUN_DIR"

	install_dev_tools
	setup_git

	if run_parallel; then
		log "All repos processed"
	else
		log "Some repos failed"
	fi

	summary
}

main "$@"
