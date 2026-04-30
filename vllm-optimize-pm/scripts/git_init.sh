#!/usr/bin/env bash
# git_init.sh — Initialize container repos to clean git state, optionally apply base patch.
# Usage: PROGRESS_LOG=<log> bash git_init.sh <container> <vllm_src> <vllm_ascend_src> [base_patch]
#
# base_patch: optional host-side patch file to apply after clean (for model-specific fixes).
#             The patch is copied into the container and applied via `git apply`.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/orchestrator_common.sh"

if [[ $# -lt 3 || $# -gt 4 ]]; then
    log_error "Usage: git_init.sh <container> <vllm_src> <vllm_ascend_src> [base_patch]"
    exit 1
fi

CONTAINER="$1"
VLLM_SRC="$2"
VLLM_ASCEND_SRC="$3"
BASE_PATCH="${4:-}"

step_start "git-init"

for REPO in "$VLLM_SRC" "$VLLM_ASCEND_SRC"; do
    REPO_NAME=$(basename "$REPO")
    log_info "Initializing $REPO_NAME at $REPO ..."

    # Add safe directory
    docker exec "$CONTAINER" bash --norc --noprofile -c \
        "git config --global --add safe.directory '$REPO'"

    # Discard staged, unstaged modifications and untracked files
    docker exec "$CONTAINER" bash --norc --noprofile -c \
        "cd '$REPO' && git reset HEAD . 2>/dev/null; git checkout . && git clean -fd"

    # Record HEAD SHA
    SHA=$(docker exec "$CONTAINER" bash --norc --noprofile -c \
        "cd '$REPO' && git rev-parse HEAD")
    log_info "$REPO_NAME HEAD: $SHA"

    # Verify clean
    STATUS=$(docker exec "$CONTAINER" bash --norc --noprofile -c \
        "cd '$REPO' && git status --porcelain")
    if [[ -n "$STATUS" ]]; then
        log_error "$REPO_NAME is not clean after reset:\n$STATUS"
        exit 1
    fi
    log_info "$REPO_NAME: clean"
done

# Apply base patch if provided
if [[ -n "$BASE_PATCH" && -f "$BASE_PATCH" ]]; then
    log_info "Applying base patch: $BASE_PATCH"
    # Detect which repo the patch targets by checking diff headers
    PATCH_REPOS=""
    if grep -q "^diff --git a/vllm/" "$BASE_PATCH" 2>/dev/null; then
        PATCH_REPOS="$VLLM_SRC"
    fi
    if grep -q "^diff --git a/vllm_ascend/" "$BASE_PATCH" 2>/dev/null; then
        PATCH_REPOS="${PATCH_REPOS:+$PATCH_REPOS }$VLLM_ASCEND_SRC"
    fi
    # Try applying to each detected repo
    for REPO in $PATCH_REPOS; do
        REPO_NAME=$(basename "$REPO")
        docker cp "$BASE_PATCH" "$CONTAINER:/tmp/_base_patch.diff"
        docker exec "$CONTAINER" bash --norc --noprofile -c \
            "cd '$REPO' && git apply /tmp/_base_patch.diff 2>/dev/null || true"
        APPLIED=$(docker exec "$CONTAINER" bash --norc --noprofile -c \
            "cd '$REPO' && git diff --stat" 2>/dev/null)
        if [[ -n "$APPLIED" ]]; then
            log_info "$REPO_NAME: base patch applied — $APPLIED"
        fi
    done
    # If no repo detected, try both
    if [[ -z "$PATCH_REPOS" ]]; then
        for REPO in "$VLLM_SRC" "$VLLM_ASCEND_SRC"; do
            docker cp "$BASE_PATCH" "$CONTAINER:/tmp/_base_patch.diff"
            docker exec "$CONTAINER" bash --norc --noprofile -c \
                "cd '$REPO' && git apply /tmp/_base_patch.diff 2>/dev/null || true"
        done
        log_info "Base patch applied (auto-detect)"
    fi
fi

step_end
