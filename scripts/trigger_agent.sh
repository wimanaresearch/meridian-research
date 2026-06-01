#!/usr/bin/env bash
# scripts/trigger_agent.sh
#
# Reference script for manually triggering a Meridian agent via
# GitHub repository_dispatch. This is the same call cron-job.org
# makes on the production schedule.
#
# Usage:
#   CRON_TRIGGER_TOKEN=ghp_... ./scripts/trigger_agent.sh <event_type>
#
# Event types:
#   trigger-morning-macro
#   trigger-idx-news
#   trigger-crypto-ta
#   trigger-us-ta
#   trigger-crypto-news
#   trigger-idx-lq45
#   trigger-weekly-recap
#   trigger-liquidity-radar
#
# Example:
#   CRON_TRIGGER_TOKEN=ghp_... ./scripts/trigger_agent.sh trigger-morning-macro

set -euo pipefail

REPO="wimanaresearch/meridian-research"
TOKEN="${CRON_TRIGGER_TOKEN:?Set CRON_TRIGGER_TOKEN env var}"
EVENT_TYPE="${1:?Usage: $0 <event_type>}"

curl -s -o /dev/null -w "HTTP %{http_code}\n" \
  -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${TOKEN}" \
  "https://api.github.com/repos/${REPO}/dispatches" \
  -d "{\"event_type\":\"${EVENT_TYPE}\"}"
