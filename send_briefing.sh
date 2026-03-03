#!/bin/bash
# Storm briefing: generates briefing, then sends via Gmail using Claude Code.
# Cron: 0 13 * * 1,4 /Users/renato/weatherchaser/send_briefing.sh

set -e
cd "$(dirname "$0")"

# 1. Generate briefing + HTML email
python3 main.py briefing --email --quiet

# 2. Send via Claude Code + Gmail MCP
claude -p "Read data/latest_email_meta.txt for the recipient and subject, then read data/latest_email.html for the body. Send this as an HTML email using the Gmail MCP tool gmail_create_draft — but actually send it, not as a draft. Use the exact HTML content as the email body, the exact subject line, and send to the address in the meta file." --allowedTools "mcp__claude_ai_Gmail__gmail_create_draft" 2>/dev/null

echo "Briefing sent."
