#!/bin/bash
# Storm briefing: generates briefing, then creates Gmail draft using Claude Code.
# Cron: 0 13 * * 1,4 /Users/renato/weatherchaser/send_briefing.sh
#
# Runs Monday and Thursday at 7:00 AM CT (13:00 UTC).
# Creates a draft in Gmail — open Gmail and hit send.

set -e

WORKDIR="/Users/renato/weatherchaser"
PYTHON="/Library/Developer/CommandLineTools/usr/bin/python3"
CLAUDE="/Users/renato/.local/bin/claude"
LOG="$WORKDIR/data/briefing.log"

cd "$WORKDIR"

echo "$(date): Starting briefing..." >> "$LOG"

# 1. Generate briefing + HTML email
$PYTHON main.py briefing --email --quiet 2>> "$LOG"

# 2. Check that email HTML was generated (validation may have blocked it)
if [ ! -f data/latest_email.html ]; then
  echo "$(date): Email HTML not generated (validation failed?). Skipping." >> "$LOG"
  exit 1
fi

# 3. Create Gmail draft via Claude Code + Gmail MCP
$CLAUDE -p "Read the file data/latest_email_meta.txt for the recipient and subject line, then read data/latest_email.html for the HTML body. Create a Gmail draft using the gmail_create_draft tool with contentType text/html, using the exact subject, recipient, and HTML body from those files." \
  --allowedTools "mcp__claude_ai_Gmail__gmail_create_draft,Read" \
  >> "$LOG" 2>&1

echo "$(date): Draft created." >> "$LOG"
