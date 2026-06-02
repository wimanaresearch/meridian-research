# Trigger Architecture

## Overview

All agents are scheduled exclusively via **cron-job.org**, which fires a
`repository_dispatch` event to the GitHub API. GitHub Actions workflows listen
for that event and run the agent.

```
cron-job.org (external scheduler)
  → POST https://api.github.com/repos/wimanaresearch/meridian-research/dispatches
      body: {"event_type": "trigger-<agent>"}
  → GitHub receives repository_dispatch
  → Matching workflow fires
  → python main.py <agent>
  → Posts brief to Discord webhook
```

The dispatch call is also documented in `scripts/trigger_agent.sh`, which can
be used to fire any agent manually from the command line without going through
the GitHub UI.

---

## Why GitHub's Built-in Cron Was Removed

Each workflow previously had **three** trigger types: `schedule`, `workflow_dispatch`,
and `repository_dispatch`. With both `schedule` and the cron-job.org
`repository_dispatch` set to the same time, every agent fired **twice** per
scheduled run — posting duplicate briefs to Discord.

The `schedule` block was removed from all workflow files. cron-job.org is now
the single source of scheduling truth.

> **Warning: do not re-add `schedule:` blocks to any workflow `.yml` file.**
> Doing so will reintroduce duplicate firing.

---

## cron-job.org Schedule

Each job POSTs the corresponding `event_type` to the GitHub dispatches endpoint.

| Agent | event_type | UTC time | WIB (UTC+7) | Days |
|---|---|---|---|---|
| Morning Macro | `trigger-morning-macro` | 23:30 | 06:30 | Daily |
| IDX News | `trigger-idx-news` | 01:00 | 08:00 | Mon–Fri |
| Crypto TA | `trigger-crypto-ta` | 02:00 | 09:00 | Daily |
| US Movement | `trigger-us-ta` | 02:00 | 09:00 | Tue–Sat |
| IDX Movement | `trigger-idx-lq45` | 09:30 | 16:30 | Mon–Fri |
| Crypto News | `trigger-crypto-news` | 07:00 | 14:00 | Daily |
| Weekly Recap | `trigger-weekly-recap` | 12:00 | 19:00 | Sunday |

> Morning Macro runs at 23:30 UTC, which is the **previous calendar day** in UTC
> but 06:30 of the current day in WIB. cron-job.org jobs should be configured
> with the UTC times in this table, not the WIB times.

---

## Manual Runs

`workflow_dispatch` is kept on every workflow. This allows one-off manual
triggers from:

- The **GitHub Actions UI** → select workflow → "Run workflow"
- The **`gh` CLI**: `gh workflow run <workflow-file>.yml`
- The **`scripts/trigger_agent.sh`** script (uses `repository_dispatch`, same
  path as cron-job.org)

Manual runs go through the same job steps as scheduled runs and will post to
Discord, so use with intent.

---

## Inactive Workflows

The following workflows exist in `.github/workflows/` but are **not yet active**.
Do not add them to cron-job.org until the agents are production-ready.

| File | event_type | Agent |
|---|---|---|
| `us-news.yml` | `trigger-us-news` | `python main.py us_news` |
| `liquidity-radar.yml` | `trigger-liquidity-radar` | `python main.py liquidity_radar` |

Both files are marked with a `# NOT YET ACTIVE` comment at the top.

---

## Concurrency & Timeout

Every workflow job includes:

```yaml
timeout-minutes: 10
concurrency:
  group: ${{ github.workflow }}
  cancel-in-progress: false
```

`timeout-minutes: 10` kills a hung run before the next scheduled fire.
`cancel-in-progress: false` lets a run finish if a second trigger arrives
while it is still running (queues rather than cancels), protecting against
partial Discord posts.
