#!/usr/bin/env bash
set -euo pipefail

# hook 会从 stdin 收到 JSON（这里不强依赖内容，直接提醒即可）
cat >/dev/null

# 声音提醒（macOS）
afplay /System/Library/Sounds/Glass.aiff >/dev/null 2>&1 &

# 可选：系统通知（横幅）
osascript -e 'display notification "Gemini task finished" with title "Gemini CLI"' >/dev/null 2>&1 &
