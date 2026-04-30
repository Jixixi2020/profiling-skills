#!/usr/bin/env bash
# rollback_code_fix.sh — Rollback container source via git checkout, optionally re-apply base patch.
# Usage: PROGRESS_LOG=<log> bash rollback_code_fix.sh <container> <vllm_src> <vllm_ascend_src> [base_patch]
#
# base_patch: optional host-side patch file to re-apply after rollback (for model-specific fixes).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/orchestrator_common.sh"

if [[ $# -lt 3 || $# -gt 4 ]]; then
    log_error "Usage: rollback_code_fix.sh <container> <vllm_src> <vllm_ascend_src> [base_patch]"
    exit 1
fi

CONTAINER="$1"
VLLM_SRC="$2"
VLLM_ASCEND_SRC="$3"
BASE_PATCH="${4:-}"

step_start "rollback-code-fix"

for REPO in "$VLLM_SRC" "$VLLM_ASCEND_SRC"; do
    REPO_NAME=$(basename "$REPO")
    log_info "Rolling back $REPO_NAME via git checkout ..."
    docker exec "$CONTAINER" bash --norc --noprofile -c \
        "cd '$REPO' && git reset HEAD . 2>/dev/null; git checkout ."
    log_info "$REPO_NAME: rolled back to HEAD"
done

# Re-apply base patch if provided
if [[ -n "$BASE_PATCH" && -f "$BASE_PATCH" ]]; then
    log_info "Re-applying base patch after rollback..."
    for REPO in "$VLLM_SRC" "$VLLM_ASCEND_SRC"; do
        docker cp "$BASE_PATCH" "$CONTAINER:/tmp/_base_patch.diff"
        docker exec "$CONTAINER" bash --norc --noprofile -c \
            "cd '$REPO' && git apply /tmp/_base_patch.diff 2>/dev/null || true"
    done
    log_info "Base patch re-applied"
fi

step_end
