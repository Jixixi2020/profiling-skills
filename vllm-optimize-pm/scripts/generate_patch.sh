#!/usr/bin/env bash
# generate_patch.sh — Generate unified diff via git diff from container repos.
# Usage: PROGRESS_LOG=<log> bash generate_patch.sh <container> <vllm_src> <vllm_ascend_src> <output_patch>

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/orchestrator_common.sh"

if [[ $# -ne 4 ]]; then
    log_error "Usage: generate_patch.sh <container> <vllm_src> <vllm_ascend_src> <output_patch>"
    exit 1
fi

CONTAINER="$1"
VLLM_SRC="$2"
VLLM_ASCEND_SRC="$3"
OUTPUT_PATCH="$4"

step_start "generate-patch"

mkdir -p "$(dirname "$OUTPUT_PATCH")"
: > "$OUTPUT_PATCH"

for REPO in "$VLLM_SRC" "$VLLM_ASCEND_SRC"; do
    REPO_NAME=$(basename "$REPO")
    log_info "Generating git diff for $REPO_NAME ..."
    # git diff returns 1 when differences found, which is expected
    docker exec "$CONTAINER" bash --norc --noprofile -c \
        "cd '$REPO' && git diff" >> "$OUTPUT_PATCH" || true
done

# Log patch stats
if [[ -s "$OUTPUT_PATCH" ]]; then
    FILES_CHANGED=$(grep -c '^diff ' "$OUTPUT_PATCH" || true)
    LINES_ADDED=$(grep -c '^+[^+]' "$OUTPUT_PATCH" || true)
    LINES_REMOVED=$(grep -c '^-[^-]' "$OUTPUT_PATCH" || true)
    log_info "Patch generated: ${FILES_CHANGED} files changed, +${LINES_ADDED} -${LINES_REMOVED} lines"
    log_info "Patch written to: $OUTPUT_PATCH"
else
    log_info "No code changes detected, empty patch created"
fi

step_end
