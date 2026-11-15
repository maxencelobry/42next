#!/usr/bin/env bash
# Cleans useless caches in your home folder. What it removes:
# - __pycache__, .mypy_cache, .pytest_cache, .ruff_cache everywhere in your home
# - ~/.cache (pip, mypy, browsers...) and flatpak app caches
# - VS Code and Discord caches and logs (skipped while the app is open)
# - old Claude Code versions (keeps the newest and the one in use)
# - the trash
set -u

# every rm below is under $HOME: refuse an empty or root home
case "${HOME:-}" in
	"" | "/") echo "HOME is empty or /: nothing cleaned"; exit 1 ;;
esac
[ -d "$HOME" ] || { echo "HOME ($HOME) is not a folder: nothing cleaned"; exit 1; }

size() { s=$(du -sxk "$HOME" 2>/dev/null | cut -f1); echo "${s:-0}"; }
before=$(size)

me="${USER:-$(id -un)}"
running() { pgrep -u "$me" -x "$1" >/dev/null 2>&1; }

find "$HOME" -xdev \
	\( -path "$HOME/.vscode" -o -path "$HOME/.config" -o -path "$HOME/.var" \
	-o -path "$HOME/.local/share/claude" -o -name .git -o -name node_modules \) -prune -o \
	-type d \( -name __pycache__ -o -name .mypy_cache -o -name .pytest_cache \
	-o -name .ruff_cache \) -prune -exec rm -rf {} + 2>/dev/null

# ~/.cache, except the 42next state (last run, update stamp)
if [ -d "$HOME/.cache" ]; then
	find "$HOME/.cache" -mindepth 1 -maxdepth 1 ! -name '42next-*' -exec rm -rf {} + 2>/dev/null
fi
rm -rf "$HOME"/.var/app/*/cache/* 2>/dev/null
rm -rf "$HOME/.local/share/Trash/files"/* "$HOME/.local/share/Trash/files"/.[!.]* \
	"$HOME/.local/share/Trash/info"/* 2>/dev/null
# macOS trash
rm -rf "$HOME/.Trash"/* "$HOME/.Trash"/.[!.]* 2>/dev/null

if ! running code; then
	c="$HOME/.config/Code"
	rm -rf "$c/CachedExtensionVSIXs" "$c/Cache" "$c/CachedData" "$c/Code Cache" \
		"$c/GPUCache" "$c/logs" "$c/Crashpad" 2>/dev/null
else
	echo "VS Code is open: VS Code cache skipped"
fi

if ! running Discord; then
	d="$HOME/.config/discord"
	rm -rf "$d/Cache" "$d/Code Cache" "$d/GPUCache" "$d/logs" "$d/Crashpad" 2>/dev/null
	find "$d/download" -maxdepth 1 -type f -delete 2>/dev/null
else
	echo "Discord is open: Discord cache skipped"
fi

# old Claude Code versions: keep the newest and the one ~/.local/bin/claude runs
v="$HOME/.local/share/claude/versions"
if [ -d "$v" ]; then
	inuse=$(basename "$(readlink "$HOME/.local/bin/claude" 2>/dev/null)" 2>/dev/null)
	ls -1 "$v" | sort -V | sed '$d' | while read -r old; do
		[ -n "$old" ] && [ "$old" != "$inuse" ] && rm -rf "${v:?}/$old"
	done
fi

after=$(size)
freed=$(( before - after ))
[ "$freed" -ge 0 ] || freed=0
echo "Freed: $(( freed / 1024 )) MB  (home: $(( after / 1024 )) MB)"
df -h "$HOME" | tail -1
