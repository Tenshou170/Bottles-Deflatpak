#!/usr/bin/env bash
# check.sh — run all quality checks for Bottles-Deflatpak
# Auto-creates the venv and installs tools if needed.
#
# Usage:
#   ./check.sh          # check only (CI mode)
#   ./check.sh --fix    # auto-fix what ruff can fix
set -euo pipefail

FIX_MODE=0
if [[ "${1:-}" == "--fix" ]]; then
    FIX_MODE=1
fi

VENV_PATH="./venv"
PYTHON_BIN="$VENV_PATH/bin/python3"
PIP_BIN="$VENV_PATH/bin/pip"

# ---------------------------------------------------------------------------
# 1. Ensure venv exists and tools are installed
# ---------------------------------------------------------------------------
if [ ! -f "$PYTHON_BIN" ]; then
    echo "==> Creating virtual environment at $VENV_PATH …"
    python3 -m venv "$VENV_PATH"
fi

# Tools required by this script (minimal set — no PyGObject/gi needed)
REQUIRED_TOOLS=(ruff)
MISSING=()
for tool in "${REQUIRED_TOOLS[@]}"; do
    if [ ! -f "$VENV_PATH/bin/$tool" ]; then
        MISSING+=("$tool")
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "==> Installing missing tools: ${MISSING[*]} …"
    "$PIP_BIN" install --quiet "${MISSING[@]}"
fi

# ---------------------------------------------------------------------------
# 2. Track overall exit code — run all checks even if one fails
# ---------------------------------------------------------------------------
OVERALL=0

run_check() {
    local label="$1"; shift
    echo ""
    echo "━━━ $label ━━━"
    if "$@"; then
        echo "✓  $label passed"
    else
        echo "✗  $label FAILED (exit $?)"
        OVERALL=1
    fi
}

# ---------------------------------------------------------------------------
# 3. Ruff lint (includes unused-import checking via F401)
# ---------------------------------------------------------------------------
if [ "$FIX_MODE" -eq 1 ]; then
    run_check "Ruff lint" "$VENV_PATH/bin/ruff" check --fix .
else
    run_check "Ruff lint" "$VENV_PATH/bin/ruff" check .
fi

# ---------------------------------------------------------------------------
# 4. Ruff format check
# ---------------------------------------------------------------------------
if [ "$FIX_MODE" -eq 1 ]; then
    run_check "Ruff format" "$VENV_PATH/bin/ruff" format .
else
    run_check "Ruff format" "$VENV_PATH/bin/ruff" format --check .
fi

# ---------------------------------------------------------------------------
# 5. Summary
# ---------------------------------------------------------------------------
echo ""
if [ "$OVERALL" -eq 0 ]; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  All checks passed."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
else
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  One or more checks FAILED. See output above."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
fi

exit "$OVERALL"
