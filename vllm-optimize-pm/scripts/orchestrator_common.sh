#!/usr/bin/env bash
# orchestrator_common.sh — vllm-optimize-loop orchestrator shared functions
# Provides unified logging, step timing for the optimization loop orchestrator.
# Usage: source "$(dirname "$0")/orchestrator_common.sh"

set -euo pipefail

# ========== Global Variables ==========
PROGRESS_LOG="${PROGRESS_LOG:-/dev/null}"
STEP_LABEL="${STEP_LABEL:-}"
LOOP_NUM="${LOOP_NUM:-}"
_STEP_START_TIME=""

# ========== Logging Functions ==========

_timestamp() {
    date '+%Y-%m-%d %H:%M:%S'
}

_build_prefix() {
    local level="$1"
    local prefix="[$(_timestamp)] [$level]"
    [[ -n "$LOOP_NUM" ]] && prefix="$prefix [Loop#${LOOP_NUM}]"
    [[ -n "$STEP_LABEL" ]] && prefix="$prefix [$STEP_LABEL]"
    echo "$prefix"
}

log_info() {
    local msg="$*"
    echo "$(_build_prefix INFO) $msg" | tee -a "$PROGRESS_LOG"
}

log_warn() {
    local msg="$*"
    echo "$(_build_prefix WARN) $msg" | tee -a "$PROGRESS_LOG"
}

log_error() {
    local msg="$*"
    echo "$(_build_prefix ERROR) $msg" | tee -a "$PROGRESS_LOG" >&2
}

log_section() {
    local title="$*"
    {
        echo ""
        echo "[$(_timestamp)] ========================================"
        [[ -n "$LOOP_NUM" ]] && echo "[$(_timestamp)] [Loop#${LOOP_NUM}] $title" || echo "[$(_timestamp)] $title"
        echo "[$(_timestamp)] ========================================"
    } | tee -a "$PROGRESS_LOG"
}

log_detail() {
    local msg="$*"
    echo "$(_build_prefix DEBUG) $msg" >> "$PROGRESS_LOG"
}

# ========== Step Timing ==========

step_start() {
    local label="$1"
    STEP_LABEL="$label"
    _STEP_START_TIME=$(date +%s)
    log_info "Started"
}

step_end() {
    local end_time
    end_time=$(date +%s)
    if [[ -n "$_STEP_START_TIME" ]]; then
        local elapsed=$(( end_time - _STEP_START_TIME ))
        log_info "Completed in ${elapsed}s"
    else
        log_info "Completed"
    fi
    STEP_LABEL=""
    _STEP_START_TIME=""
}
