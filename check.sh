#!/usr/bin/env bash
# check.sh — run the project's quality checks from a local venv.
#
# Runs the exact hooks declared in .pre-commit-config.yaml (whitespace/EOF,
# YAML/XML/JSON, large files, ruff lint, ruff-format and mypy) through
# pre-commit installed in ./venv. Nothing is installed system-wide; hook
# environments are cached by pre-commit under ~/.cache/pre-commit.
#
# Usage:
#   ./check.sh              # run every hook on every file
#   ./check.sh --fix        # same, then re-run once so autofixes settle
#   ./check.sh --all        # accepted for compatibility (all files are default)
#
# Hook versions (ruff, mypy, their stubs) live in .pre-commit-config.yaml, so
# this script, CI and a local `pre-commit install` all run identical tools.
# Bump versions there, not here.

set -euo pipefail

FIX_MODE=0
for arg in "$@"; do
    case "$arg" in
        --fix) FIX_MODE=1 ;;
        --all) ;; # all files are always checked
        -h | --help)
            sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown option: $arg (try --help)" >&2
            exit 2
            ;;
    esac
done

VENV_PATH="./venv"
PYTHON_BIN="$VENV_PATH/bin/python3"
PIP_BIN="$VENV_PATH/bin/pip"
PRE_COMMIT_VERSION="4.7.0"

# ---------------------------------------------------------------------------
# 1. Ensure the venv exists
# ---------------------------------------------------------------------------
if [ ! -f "$PYTHON_BIN" ]; then
    echo "==> Creating virtual environment at $VENV_PATH ..."
    python3 -m venv "$VENV_PATH"
fi

# ---------------------------------------------------------------------------
# 2. Ensure a pinned pre-commit is available in it
# ---------------------------------------------------------------------------
_installed_version() {
    "$PYTHON_BIN" -c "
import importlib.metadata
try:
    print(importlib.metadata.version('$1'))
except importlib.metadata.PackageNotFoundError:
    print('')
" 2>/dev/null
}

if [ "$(_installed_version pre-commit)" != "$PRE_COMMIT_VERSION" ]; then
    echo "==> Installing pre-commit==$PRE_COMMIT_VERSION ..."
    "$PIP_BIN" install --quiet "pre-commit==${PRE_COMMIT_VERSION}"
fi

# ---------------------------------------------------------------------------
# 3. Run the hooks
# ---------------------------------------------------------------------------
run_hooks() {
    echo ""
    echo "--- pre-commit (all files) ---"
    "$PYTHON_BIN" -m pre_commit run --all-files
}

STATUS=0
run_hooks || STATUS=$?

if [ "$STATUS" -ne 0 ] && [ "$FIX_MODE" -eq 1 ]; then
    # Hooks that can autofix (ruff --fix, ruff-format, eof-fixer, ...) have
    # already rewritten files; run once more to report the settled state.
    echo ""
    echo "==> Autofix pass changed files; re-running hooks ..."
    STATUS=0
    run_hooks || STATUS=$?
fi

echo ""
if [ "$STATUS" -eq 0 ]; then
    echo "============================================="
    echo "  All checks passed."
    echo "============================================="
else
    echo "============================================="
    echo "  One or more checks FAILED. See output above."
    if [ "$FIX_MODE" -eq 0 ]; then
        echo "  Some hooks autofix in place; re-run with --fix or commit the changes."
    fi
    echo "============================================="
fi

exit "$STATUS"
