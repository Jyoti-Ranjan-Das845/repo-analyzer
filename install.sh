#!/usr/bin/env bash
# Installs the repo-analyzer command for the current user, so it runs from any folder.
#
#   ./install.sh               install, or update to this copy of the code
#   ./install.sh --uninstall   remove the command
#
# Without a clone:
#   curl -fsSL https://raw.githubusercontent.com/Jyoti-Ranjan-Das845/repo-analyzer/main/install.sh | bash
#
# The tool gets its own environment in your home folder and the command goes into ~/.local/bin. It uses uv if you
# have it, else pipx, else a Python 3.11+ virtual environment. If the command's folder is not on your PATH, it is
# added to your shell's startup file (set REPO_ANALYZER_NO_MODIFY_PATH=1 to skip that).

set -euo pipefail

NAME="repo-analyzer"
REPO_URL="https://github.com/Jyoti-Ranjan-Das845/repo-analyzer.git"
VENV_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/$NAME"      # used only when neither uv nor pipx is available
VENV_BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"

say() { printf '%s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

find_python() {                                               # the first Python 3.11 or newer on PATH
  local p
  for p in python3.14 python3.13 python3.12 python3.11 python3 python; do
    if have "$p" && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      command -v "$p"
      return 0
    fi
  done
  return 1
}

source_spec() {                                               # this clone when run from one, else GitHub
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || here=""
  if [ -n "$here" ] && grep -qs '^name = "repo-analyzer"' "$here/pyproject.toml"; then
    say "$here"
  else
    say "git+$REPO_URL"
  fi
}

ensure_path() {                                               # make the command's folder part of PATH
  local dir="$1" rc line
  case ":$PATH:" in *":$dir:"*) return 0 ;; esac
  if [ "${REPO_ANALYZER_NO_MODIFY_PATH:-}" = 1 ]; then
    say "  note:    $dir is not on your PATH; add it to run $NAME from any folder"
    return 0
  fi
  case "$(basename "${SHELL:-sh}")" in
    zsh)  rc="${ZDOTDIR:-$HOME}/.zshrc" ;;
    bash) if [ "$(uname -s)" = Darwin ]; then rc="$HOME/.bash_profile"; else rc="$HOME/.bashrc"; fi ;;
    fish) say "  note:    $dir is not on your PATH; run: fish_add_path $dir"; return 0 ;;
    *)    rc="$HOME/.profile" ;;
  esac
  line="export PATH=\"$dir:\$PATH\""
  if ! grep -qsF "$line" "$rc"; then
    printf '\n# added by the repo-analyzer installer\n%s\n' "$line" >> "$rc"
  fi
  say "  PATH:    $dir added in $rc; open a new terminal to use the command"
}

install() {
  local spec method bin_dir py
  spec="$(source_spec)"
  say "Installing $NAME from $spec"
  if have uv; then
    method="uv"
    uv tool install --force --reinstall --python '>=3.11' "$spec"
    bin_dir="$(uv tool dir --bin)"
  elif have pipx; then
    method="pipx"
    py="$(find_python)" || die "Python 3.11 or newer is needed (or install uv: https://docs.astral.sh/uv/)"
    pipx install --force --python "$py" "$spec"
    bin_dir="$(pipx environment --value PIPX_BIN_DIR)"
  else
    method="a virtual environment in $VENV_HOME"
    py="$(find_python)" || die "Python 3.11 or newer is needed (or install uv: https://docs.astral.sh/uv/)"
    mkdir -p "$VENV_HOME" "$VENV_BIN"
    "$py" -m venv --clear "$VENV_HOME/venv"
    "$VENV_HOME/venv/bin/python" -m pip install --quiet --upgrade pip
    "$VENV_HOME/venv/bin/python" -m pip install --quiet "$spec"
    ln -sf "$VENV_HOME/venv/bin/$NAME" "$VENV_BIN/$NAME"
    bin_dir="$VENV_BIN"
  fi
  [ -x "$bin_dir/$NAME" ] || die "the install finished but $bin_dir/$NAME is missing"
  say ""
  say "Installed $("$bin_dir/$NAME" --version) with $method"
  say "  command: $bin_dir/$NAME"
  ensure_path "$bin_dir"
  local tool
  for tool in omni claude; do
    have "$tool" || say "  note:    '$tool' is not on your PATH; $NAME needs it to run the agent (see the README)"
  done
  say "  try:     $NAME analyze /path/to/project"
}

uninstall() {
  local removed=0
  if have uv && grep -q "^$NAME " <<<"$(uv tool list 2>/dev/null)"; then
    uv tool uninstall "$NAME" && removed=1
  fi
  if have pipx && grep -q "^$NAME " <<<"$(pipx list --short 2>/dev/null)"; then
    pipx uninstall "$NAME" && removed=1
  fi
  if [ -d "$VENV_HOME/venv" ]; then
    rm -rf "$VENV_HOME"
    if [ -L "$VENV_BIN/$NAME" ]; then rm -f "$VENV_BIN/$NAME"; fi
    removed=1
  fi
  if [ "$removed" = 1 ]; then
    say "$NAME removed. Analyses stay in each project's .repo-analyzer/ folder; the parse cache is in ~/.cache/$NAME."
  else
    say "$NAME is not installed."
  fi
}

case "${1:-}" in
  "")          install ;;
  --uninstall) uninstall ;;
  -h|--help)   say "usage: install.sh [--uninstall]   (installs the $NAME command for the current user)" ;;
  *)           die "unknown option '$1' (use --uninstall, or no option to install)" ;;
esac
