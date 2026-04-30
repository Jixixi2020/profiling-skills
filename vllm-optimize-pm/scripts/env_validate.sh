#!/usr/bin/env bash
# env_validate.sh — Validate environment before optimization loop starts.
# Usage: PROGRESS_LOG=<log> bash env_validate.sh <container> <serve_script> <benchmark_script> \
#            <vllm_src> <vllm_ascend_src> <base_dir>

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/orchestrator_common.sh"

PARSE_SERVE_SCRIPT="$SKILLS_DIR/vllm-perf-analyzer-configs/scripts/parse_serve_script.py"

if [[ $# -lt 6 ]]; then
    log_error "Usage: env_validate.sh <container> <serve_script> <benchmark_script> <vllm_src> <vllm_ascend_src> <base_dir>"
    exit 1
fi

CONTAINER="$1"
SERVE_SCRIPT="$2"
BENCHMARK_SCRIPT="$3"
VLLM_SRC="$4"
VLLM_ASCEND_SRC="$5"
BASE_DIR="$6"

step_start "env-validate"

# 1. docker command available
log_info "Checking docker command..."
if ! command -v docker &>/dev/null; then
    log_error "docker command not found"
    exit 1
fi

# 2. container is running
log_info "Checking container '$CONTAINER' is running..."
CONTAINER_STATE=$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)
if [[ "$CONTAINER_STATE" != "true" ]]; then
    log_error "Container '$CONTAINER' is not running"
    exit 1
fi

# 3. serve_script exists in container
log_info "Checking serve_script '$SERVE_SCRIPT' in container..."
if ! docker exec "$CONTAINER" bash --norc --noprofile -c "test -f '$SERVE_SCRIPT'" 2>/dev/null; then
    log_error "serve_script '$SERVE_SCRIPT' not found in container"
    exit 1
fi

# 4. benchmark_script exists in container
log_info "Checking benchmark_script '$BENCHMARK_SCRIPT' in container..."
if ! docker exec "$CONTAINER" bash --norc --noprofile -c "test -f '$BENCHMARK_SCRIPT'" 2>/dev/null; then
    log_error "benchmark_script '$BENCHMARK_SCRIPT' not found in container"
    exit 1
fi

# 5. parse serve_script to extract port/model_name
log_info "Parsing serve_script to extract parameters..."
SERVE_INFO=$(docker exec "$CONTAINER" bash --norc --noprofile -c "cat $SERVE_SCRIPT" | python3 "$PARSE_SERVE_SCRIPT" - 2>&1) || {
    log_error "Failed to parse serve_script: $SERVE_INFO"
    exit 1
}
PORT=$(echo "$SERVE_INFO" | python3 -c "import json,sys; print(json.load(sys.stdin)['port'])")
MODEL_NAME=$(echo "$SERVE_INFO" | python3 -c "import json,sys; print(json.load(sys.stdin)['model_name'])")
log_info "Extracted: port=$PORT, model_name=$MODEL_NAME"

# 6. vllm_src exists in container
log_info "Checking vllm source '$VLLM_SRC' in container..."
if ! docker exec "$CONTAINER" bash --norc --noprofile -c "test -d '${VLLM_SRC}'" 2>/dev/null; then
    log_error "vllm source directory '$VLLM_SRC' not found in container"
    exit 1
fi

# 7. vllm_ascend_src exists in container
log_info "Checking vllm-ascend source '$VLLM_ASCEND_SRC' in container..."
if ! docker exec "$CONTAINER" bash --norc --noprofile -c "test -d '${VLLM_ASCEND_SRC}'" 2>/dev/null; then
    log_error "vllm-ascend source directory '$VLLM_ASCEND_SRC' not found in container"
    exit 1
fi

# 8. NPU available
log_info "Checking NPU availability..."
if ! docker exec "$CONTAINER" bash --norc --noprofile -c "npu-smi info" &>/dev/null; then
    log_error "NPU not available (npu-smi info failed)"
    exit 1
fi

# 9. Clean residual NPU processes if any
log_info "Checking for residual NPU processes..."
RESIDUAL=$(docker exec "$CONTAINER" bash --norc --noprofile -c "pgrep -f 'vll[m]' 2>/dev/null || true" | tr -d '[:space:]')
if [[ -n "$RESIDUAL" ]]; then
    log_warn "Detected residual NPU processes, cleaning up..."
    docker exec "$CONTAINER" bash --norc --noprofile -c "pkill -9 VLLM 2>/dev/null; pkill -9 python 2>/dev/null; true"
    sleep 10
    log_info "Residual processes cleaned"
fi

# 10. python3 + matplotlib available
log_info "Checking python3 and matplotlib..."
if ! command -v python3 &>/dev/null; then
    log_error "python3 not found on host"
    exit 1
fi
if ! python3 -c "import matplotlib" &>/dev/null; then
    log_error "matplotlib not available"
    exit 1
fi

# 11. Create directories
log_info "Creating base directory structure..."
mkdir -p "$BASE_DIR"
mkdir -p "$BASE_DIR/top_orchestrator_result"

step_end
log_info "Environment validation passed"
