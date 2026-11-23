#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${HOME}/.gemini/config/plugins/convo-mesh"
PYTHON_PATH=""
UNINSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      TARGET_DIR="$2"
      shift 2
      ;;
    --python)
      PYTHON_PATH="$2"
      shift 2
      ;;
    --uninstall)
      UNINSTALL=1
      shift
      ;;
    -h|--help)
      echo "Usage: ./install.sh [--target <dir>] [--python <path>] [--uninstall]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ "${UNINSTALL}" -eq 1 ]]; then
  if [[ -d "${TARGET_DIR}" ]]; then
    rm -rf "${TARGET_DIR}"
    echo "Removed convo-mesh plugin from: ${TARGET_DIR}"
  else
    echo "convo-mesh is not installed at: ${TARGET_DIR}"
  fi
  echo "Note: Your local database (~/.gemini/antigravity/convo-mesh/mesh.db) was preserved."
  exit 0
fi

resolve_python() {
  local candidates=()
  if [[ -n "${PYTHON_PATH}" ]]; then
    candidates=("${PYTHON_PATH}")
  else
    candidates=("python3" "python")
  fi

  for cand in "${candidates[@]}"; do
    if command -v "${cand}" >/dev/null 2>&1; then
      if "${cand}" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1; then
        "${cand}" -c "import sys; print(sys.executable)"
        return 0
      fi
    fi
  done
  return 1
}

RESOLVED_PYTHON="$(resolve_python || true)"
if [[ -z "${RESOLVED_PYTHON}" ]]; then
  echo "Error: Python 3.10+ is required. Install Python 3.10+ or pass --python <path>." >&2
  exit 1
fi

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Using Python: ${RESOLVED_PYTHON}"
echo "Installing convo-mesh from ${SOURCE_ROOT} -> ${TARGET_DIR}"

mkdir -p "${TARGET_DIR}"
cp "${SOURCE_ROOT}/plugin.json" "${TARGET_DIR}/plugin.json"
cp "${SOURCE_ROOT}/cli.py" "${TARGET_DIR}/cli.py"

for dir in rules skills core hooks mcp; do
  rm -rf "${TARGET_DIR:?}/${dir}"
  cp -R "${SOURCE_ROOT}/${dir}" "${TARGET_DIR}/${dir}"
  find "${TARGET_DIR}/${dir}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
  find "${TARGET_DIR}/${dir}" -type f -name "*.pyc" -delete 2>/dev/null || true
done

"${RESOLVED_PYTHON}" - "${TARGET_DIR}" "${RESOLVED_PYTHON}" <<'PYEOF'
import json
import sys
from pathlib import Path

target_dir = Path(sys.argv[1])
py_exe = sys.argv[2]
quoted_cmd = f'"{py_exe}" hooks/mesh_hook.py'

hooks_cfg = {
    "convo-mesh-coordinator": {
        "SessionStart": [{"type": "command", "command": quoted_cmd, "timeout": 10}],
        "PreInvocation": [{"type": "command", "command": quoted_cmd, "timeout": 10}],
        "PostToolUse": [
            {
                "matcher": "write_file|edit_file",
                "hooks": [{"type": "command", "command": quoted_cmd, "timeout": 10}],
            }
        ],
        "Stop": [{"type": "command", "command": quoted_cmd, "timeout": 10}],
    }
}

mcp_cfg = {
    "mcpServers": {
        "convo-mesh": {
            "command": py_exe,
            "type": "stdio",
            "args": ["-u", "mcp/server.py"],
        }
    }
}

(target_dir / "hooks.json").write_text(json.dumps(hooks_cfg, indent=2) + "\n", encoding="utf-8")
(target_dir / "mcp_config.json").write_text(json.dumps(mcp_cfg, indent=2) + "\n", encoding="utf-8")
PYEOF

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

(
  cd "${TARGET_DIR}"
  export CONVO_MESH_DB="${TMP_DIR}/smoke.db"
  printf '%s\n' '{"hookEventName":"SessionStart","conversationId":"install-check","workspacePaths":["."]}' \
    | "${RESOLVED_PYTHON}" hooks/mesh_hook.py >/dev/null
  printf '%s\n%s\n%s\n' \
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
    '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
    '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
    | "${RESOLVED_PYTHON}" -u mcp/server.py \
    | "${RESOLVED_PYTHON}" -c 'import json,sys; lines=[json.loads(l) for l in sys.stdin if l.strip()]; assert len(lines[1]["result"]["tools"]) == 14'
)

echo ""
echo "[OK] convo-mesh installed and verified at: ${TARGET_DIR}"
echo "Next steps:"
echo "  1. Open a new Antigravity conversation (or reload Antigravity)."
echo "  2. Verify 'convo-mesh' appears under Additional Options (...) > MCP Servers or Skills & Customizations."
