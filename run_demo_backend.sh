#!/usr/bin/env bash
set -euo pipefail

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PATH="$SCRIPT_ROOT/backend"

if [[ ! -d "$BACKEND_PATH" ]]; then
  echo "Could not find backend directory at: $BACKEND_PATH" >&2
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD="python"
else
  echo "Python was not found. Install Python 3 and try again." >&2
  exit 1
fi

VENV_PATH="$SCRIPT_ROOT/.venv"
VENV_PYTHON="$VENV_PATH/bin/python"

venv_has_pip() {
  [[ -x "$VENV_PYTHON" ]] && "$VENV_PYTHON" -m pip --version >/dev/null 2>&1
}

bootstrap_pip() {
  local get_pip
  get_pip="$(mktemp)"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$get_pip"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$get_pip" https://bootstrap.pypa.io/get-pip.py
  else
    echo "Could not download pip (need curl or wget)." >&2
    rm -f "$get_pip"
    return 1
  fi
  "$VENV_PYTHON" "$get_pip"
  rm -f "$get_pip"
}

create_venv() {
  echo "Creating local virtual environment at: $VENV_PATH"
  rm -rf "$VENV_PATH"

  if "$PYTHON_CMD" -m venv "$VENV_PATH"; then
    return 0
  fi

  echo "Standard venv failed (often missing python3-venv / ensurepip). Trying without pip..."
  rm -rf "$VENV_PATH"
  if "$PYTHON_CMD" -m venv --without-pip "$VENV_PATH"; then
    bootstrap_pip
    return 0
  fi

  if command -v virtualenv >/dev/null 2>&1; then
    echo "Trying virtualenv..."
    virtualenv -p "$PYTHON_CMD" "$VENV_PATH"
    return 0
  fi

  echo "Could not create a virtual environment." >&2
  echo "If you have sudo, install it with:" >&2
  echo "  sudo apt install python3.12-venv" >&2
  exit 1
}

if ! venv_has_pip; then
  create_venv
  if ! venv_has_pip; then
    echo "Virtual environment exists but pip is missing." >&2
    exit 1
  fi
fi

run_python() {
  "$VENV_PYTHON" "$@"
}

cd "$BACKEND_PATH"

echo "Installing backend dependencies..."
run_python -m pip install -r requirements.txt

echo "Running one-time backend setup..."
run_python setup_environment.py

echo "Starting backend app..."
run_python app.py
