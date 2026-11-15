#!/usr/bin/env bash
# Installs 42next in ~/.local/share/42next (hidden) and links the `42next`
# command in ~/.local/bin. No sudo. Works from a clone or piped:
#   curl -fsSL https://raw.githubusercontent.com/maxencelobry/42next/main/install.sh | bash
set -e
repo="https://github.com/maxencelobry/42next.git"
dir="$HOME/.local/share/42next"
old="$HOME/42next"
bin="$HOME/.local/bin"

command -v git >/dev/null || { echo "git is required"; exit 1; }
command -v node >/dev/null || { echo "node 18+ is required: https://nodejs.org/en/download"; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required: https://www.python.org/downloads/"; exit 1; }

# local-clone mode only when this script is a real file next to bin/42next.js.
# Piped (curl | bash) there is no script file: never link the current folder.
src="${BASH_SOURCE[0]:-}"
here=""
if [ -n "$src" ] && [ -f "$src" ]; then
	here="$(cd "$(dirname "$src")" 2>/dev/null && pwd || true)"
fi
if [ -n "$here" ] && [ -f "$here/bin/42next.js" ]; then
	dir="$here"
else
	# older installs lived in ~/42next: move them out of sight
	if [ ! -e "$dir" ] && [ -d "$old/.git" ] \
		&& git -C "$old" remote get-url origin 2>/dev/null | grep -q "maxencelobry/42next"; then
		mkdir -p "$(dirname "$dir")"
		mv "$old" "$dir"
		echo "moved ~/42next to ~/.local/share/42next"
	fi
	if [ -d "$dir/.git" ]; then
		git -C "$dir" pull --ff-only --quiet
	else
		mkdir -p "$(dirname "$dir")"
		git clone --quiet "$repo" "$dir"
	fi
fi

mkdir -p "$bin"
chmod +x "$dir/bin/42next.js" "$dir/bin/clean-pc.sh"
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
if [ -n "$missing" ]; then
	echo "optional, not found (those checks are skipped):$missing"
	# shellcheck disable=SC2086
	python3 -B -c 'import sys; sys.path.insert(0, sys.argv[1]); import core
print("\n".join("  " + x for x in core.install_hints(sys.argv[2:])))' \
		"$dir/testers/common" $missing 2>/dev/null || true
fi
