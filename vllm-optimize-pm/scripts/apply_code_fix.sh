#!/usr/bin/env bash
# apply_code_fix.sh — Push modified files from host fix_dir into container target_src.
# Usage: PROGRESS_LOG=<log> bash apply_code_fix.sh <container> <fix_dir> <target_src>
#
# fix_dir contains modified files with relative paths matching target_src structure.
# Files are pushed via tar pipe: tar cf - -C fix_dir . | docker exec -i container tar xf - -C target_src

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/orchestrator_common.sh"

if [[ $# -ne 3 ]]; then
    log_error "Usage: apply_code_fix.sh <container> <fix_dir> <target_src>"
    exit 1
fi

CONTAINER="$1"
FIX_DIR="$2"
TARGET_SRC="$3"

step_start "apply-code-fix"

if [[ ! -d "$FIX_DIR" ]]; then
    log_error "fix_dir does not exist: $FIX_DIR"
    exit 1
fi

FILE_COUNT=$(find "$FIX_DIR" -type f | wc -l)
if [[ "$FILE_COUNT" -eq 0 ]]; then
    log_warn "No files in fix_dir: $FIX_DIR"
    step_end
    exit 0
fi

log_info "Pushing $FILE_COUNT file(s) from $FIX_DIR to $CONTAINER:$TARGET_SRC"

# List files being overwritten
find "$FIX_DIR" -type f -printf '%P\n' | while read -r rel; do
    log_info "  overwrite: $TARGET_SRC/$rel"
done

# Push via tar pipe
tar cf - -C "$FIX_DIR" . | docker exec -i "$CONTAINER" tar xf - -C "$TARGET_SRC"

log_info "Applied $FILE_COUNT file(s) to $CONTAINER:$TARGET_SRC"
step_end
