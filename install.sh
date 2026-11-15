#!/usr/bin/env bash
# Installs 42next in ~/42next and links the `42next` command in ~/.local/bin.
# No sudo. Works from a clone or piped:
#   curl -fsSL https://raw.githubusercontent.com/maxencelobry/42next/main/install.sh | bash
set -e
repo="https://github.com/maxencelobry/42next.git"
dir="$HOME/42next"
bin="$HOME/.local/bin"

command -v git >/dev/null || { echo "git is required"; exit 1; }
command -v node >/dev/null || { echo "node 18+ is required: https://nodejs.org/en/download"; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required: https://www.python.org/downloads/"; exit 1; }

here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -f "$here/bin/42next.js" ]; then
	dir="$here"
elif [ -d "$dir/.git" ]; then
	git -C "$dir" pull --ff-only --quiet
else
	git clone --quiet "$repo" "$dir"
fi

mkdir -p "$bin"
chmod +x "$dir/bin/42next.js" "$dir/clean-pc.sh"
ln -sf "$dir/bin/42next.js" "$bin/42next"
echo "42next installed: run it in a project folder with  42next"

case ":$PATH:" in
	*":$bin:"*) ;;
	*) echo "add this line to your ~/.zshrc or ~/.bashrc:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

missing=""
for tool in cc make norminette valgrind flake8 mypy; do
	command -v "$tool" >/dev/null || missing="$missing $tool"
done
[ -z "$missing" ] || echo "optional, not found (those checks are skipped):$missing"
