#!/usr/bin/env bash
# Cleans useless caches in your home folder. What it removes:
# - __pycache__, .mypy_cache, .pytest_cache, .ruff_cache everywhere in your home
# - ~/.cache (pip, mypy, browsers...) and flatpak app caches
# - VS Code and Discord caches and logs (skipped while the app is open)
# - old Claude Code versions (keeps the newest)
# - the trash
set -u

before=$(du -sxk "$HOME" 2>/dev/null | cut -f1)

running() { pgrep -u "$USER" -x "$1" >/dev/null 2>&1; }

find "$HOME" -xdev \
	\( -path "$HOME/.vscode" -o -path "$HOME/.config" -o -path "$HOME/.var" \
	-o -path "$HOME/.local/share/claude" -o -name .git -o -name node_modules \) -prune -o \
	-type d \( -name __pycache__ -o -name .mypy_cache -o -name .pytest_cache \
	-o -name .ruff_cache \) -prune -print0 2>/dev/null | xargs -0 -r rm -rf

rm -rf "$HOME/.cache"/* "$HOME/.cache"/.[!.]* 2>/dev/null
rm -rf "$HOME"/.var/app/*/cache/* 2>/dev/null
rm -rf "$HOME/.local/share/Trash/files"/* "$HOME/.local/share/Trash/files"/.[!.]* \
	"$HOME/.local/share/Trash/info"/* 2>/dev/null

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

v="$HOME/.local/share/claude/versions"
if [ -d "$v" ]; then
	ls -1 "$v" | sort -V | head -n -1 | while read -r old; do rm -f "$v/$old"; done
fi

after=$(du -sxk "$HOME" 2>/dev/null | cut -f1)
echo "Freed: $(( (before - after) / 1024 )) MB  (home: $(( after / 1024 )) MB)"
df -h "$HOME" | tail -1
