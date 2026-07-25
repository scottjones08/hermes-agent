---
name: wind-neos-eve-import
description: "Use when Wind needs to create or reconcile Eve matters from Neos open cases, honor a do-not-import list, turn Neos communications notes into a SharePoint-backed DOCX summary, or run the workflow on a schedule through browser automation."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Wind, Neos, Eve, SharePoint, Legal-Ops, Browser-Automation]
    related_skills: [hermes-agent]
---

# Wind Neos to Eve Import

## Overview

This skill creates and maintains Eve matters for Wind Injury Law from the
**Neos Open Case List**. It never treats Eve as the legal system of record:
Neos owns the case identity and notes, SharePoint owns the generated document,
and Eve consumes the SharePoint matter folder through Wind's existing bridge.

The workflow has two layers:

1. `scripts/import_manager.py` owns explicit exclusions, duplicate prevention,
   note fingerprints, private manifests, and the final DOCX.
2. Browser tools operate the signed-in Neos and Eve interfaces. SharePoint
   delivery uses a direct SharePoint tool when available, a locally synced
   SharePoint root, or a supported browser upload.

All client data is private. Never write Neos exports, notes, manifests, or
generated summaries into a git repository.

## When to Use

Use this skill when the user asks to:

- import only open Neos cases into Eve;
- add, remove, or review cases on the Eve do-not-import list;
- create Eve matters automatically as new Neos cases appear;
- summarize Neos emails, texts, calls, and other notes into a document;
- reconcile an Eve matter with its Neos case number or SharePoint folder;
- schedule the workflow through Hermes cron;
- perform a dry run before allowing live matter creation.

Do not use this skill to:

- write legal conclusions back into Neos;
- import closed, inactive, declined, rejected, or archived cases;
- move raw client data into a public or shared repository;
- guess a SharePoint folder match or create duplicate Eve matters;
- create an Eve matter from a name alone.

## Non-Negotiable Controls

1. **Open cases only.** Start from the saved Neos Open Case List. If an
   exported row explicitly says closed/inactive/declined/rejected/archived,
   skip it even if it appeared in that report.
2. **Exclusions run first.** Check the do-not-import list before searching or
   creating anything in Eve.
3. **Case number is the durable key.** Prefer immutable Neos Case ID when
   available; otherwise normalize the case number. A client or case name is
   never a key.
4. **Search before create.** Search Eve for the exact case number. If exactly
   one matter exists, bind it to local state. If more than one exists, stop
   with `blocked_duplicate`; do not choose one.
5. **Exact SharePoint match.** Match one case folder whose case-number token
   equals the Neos case number. Zero or multiple matches is a blocker.
6. **Stable matter name.** New Eve matters use:
   `<case number> - <Neos case name>`.
7. **Stable document name.** The generated file is always:
   `Neos Notes Summary.docx`.
8. **Idempotent updates.** Re-run only when the case is new or its normalized
   notes fingerprint changed.
9. **No silent partial success.** State is updated only after the resulting Eve
   matter and SharePoint document are visibly verified.
10. **No legal invention.** The summary may condense communications, but it
    must preserve uncertainty, distinguish fact from inference, and point back
    to Neos as the authoritative source.

## Private Control Plane

Locate the bundled script:

```bash
SKILL_SCRIPT="${HERMES_HOME:-$HOME/.hermes}/skills/autonomous-ai-agents/wind-neos-eve-import/scripts/import_manager.py"
if [ ! -f "$SKILL_SCRIPT" ]; then
  SKILL_SCRIPT="skills/autonomous-ai-agents/wind-neos-eve-import/scripts/import_manager.py"
fi
```

The default private data home is:

```text
~/.hermes/private/wind-neos-eve-import
```

Override it only with another private, access-controlled location:

```bash
export NEOS_EVE_IMPORT_HOME="/secure/path/wind-neos-eve-import"
```

Initialize:

```bash
python "$SKILL_SCRIPT" init
python "$SKILL_SCRIPT" self-test
```

### Manage Exclusions

Add by case number:

```bash
python "$SKILL_SCRIPT" exclude add "24-00123" \
  --reason "User directed: do not create in Eve" \
  --added-by "Scott"
```

Add by immutable Neos ID when available:

```bash
python "$SKILL_SCRIPT" exclude add "24-00123" \
  --neos-case-id "NEOS-CASE-ID" \
  --reason "User directed: do not create in Eve"
```

List and remove:

```bash
python "$SKILL_SCRIPT" exclude list
python "$SKILL_SCRIPT" exclude remove "24-00123"
```

When the user gives a list, echo back only case numbers and reasons—not client
names—before applying it. Refuse ambiguous name-only exclusions and ask for
case numbers.

## Browser Prerequisites

The operating agent needs:

- `browser_navigate`, `browser_snapshot`, `browser_click`, and `browser_type`;
- an authenticated Neos session;
- an authenticated Eve session;
- one supported SharePoint write path:
  - a direct SharePoint connector/tool;
  - `WIND_SHAREPOINT_CASEFILES_ROOT` pointing at a locally synced case library;
  - or browser file upload/CDP support.

Codex may use its in-app browser. Hermes uses its `browser` toolset. When using
Hermes with a live local browser profile, connect it with `/browser connect`
before the run. Never scrape cookies, tokens, passwords, local storage, or
session files.

If the required signed-in browser is unavailable, stop after producing the
private plan. Do not fall back to public web search and do not mark actions
successful.

## Source Collection

### Neos Open Cases

Navigate to Neos and open the saved **Open Case List** report. Use the current
browser snapshot to locate controls; do not hard-code ref IDs across runs.

Required fields:

- Case Number or Case No
- Case Name or Name
- Neos Case ID when the report provides it
- Current Status when the report provides it

Collect every page. Preferred source order:

1. report CSV export when the browser surface returns a safe download path;
2. saved/scheduled Neos report already delivered to the private import home;
3. DOM table extraction page by page with `browser_console`;
4. an approved Neos API adapter after Assembly access is available.

Hermes' standard browser backend may not support file downloads. In that case,
extract visible report rows page by page and persist them with a terminal
script into the private data home. Prove completeness by comparing the
collected row count with the report's displayed total. If totals differ, stop.

### Neos Case Notes

Open the saved **Case Notes** report. Required fields:

- Case Number or Case No
- Note or Body
- Entry Date when available
- Type/Channel, Direction, Staff, Subject, and Entry ID when available

The report must cover the same open-case population. Collect every page and
prove the row count. Notes may include privileged, health, or personal data;
never paste them into chat delivery messages or logs.

If the browser backend cannot completely collect the report, leave the matter
unmodified with `blocked_incomplete_notes`.

## Build the Import Plan

With private CSV paths:

```bash
python "$SKILL_SCRIPT" plan \
  --cases "$NEOS_EVE_IMPORT_HOME/inbox/open_cases.csv" \
  --notes "$NEOS_EVE_IMPORT_HOME/inbox/case_notes.csv" \
  --max-actions 5
```

The command prints the manifest path and counts. Read the manifest and process
only `create_and_sync` and `update_summary` actions.

Action meanings:

| Action | Meaning |
|---|---|
| `excluded` | Explicit do-not-import entry matched |
| `skipped_not_open` | Neos reported a closed-like status |
| `blocked_missing_name` | Neos case name is absent |
| `create_and_sync` | No verified Eve matter exists |
| `update_summary` | Matter exists and notes fingerprint changed |
| `noop` | Matter and notes are current |
| `deferred` | Safe per-run action cap was reached |

The manifest and source packets contain client information and must stay under
the private data home.

## Generate the Notes Summary

For each mutating action, read its private `notes_json_path`. Summarize all
notes—not merely the most recent—and use these exact sections:

```markdown
# Neos Notes Summary

## Matter Snapshot
Case number, case name, note count, date range, generated timestamp.

## Executive Summary
Plain-language current situation based only on the notes.

## Communications Summary
Grouped summary of emails, texts, calls, and other communications. Identify
direction and parties only when the notes support it.

## Open Items
Unresolved requests, promised follow-ups, deadlines, missing records, or
questions. Write "None identified in the exported notes" if no item is found.

## Recent Timeline
Concise chronological bullets, with dates, for material recent events.

## Source and Safety
State that this is an AI-generated operational summary from Neos notes, may
omit nuance, is not legal advice, and must be checked against Neos.
```

Rules:

- Never invent a deadline, liability assessment, medical diagnosis, settlement
  value, representation status, or completed task.
- Preserve qualifiers such as "client stated", "appears", and "not confirmed".
- A note that says an email/text/call was sent is activity, not proof it was
  received or answered.
- Include disagreements or contradictory notes rather than choosing a winner.
- Avoid unnecessary sensitive detail when it does not affect the current case
  status or an open item.
- The output should be useful to Eve while remaining short enough to review.

Write the Markdown summary beside the source packet, then create the DOCX:

```bash
python "$SKILL_SCRIPT" finalize-summary \
  --summary-file "/private/run/CASE/summary.md" \
  --output "/private/run/CASE/Neos Notes Summary.docx"
```

The script rejects summaries missing a required section.

## Eve Matter Workflow

For each `create_and_sync` action:

1. Navigate to Eve's matter list.
2. Search the exact Neos case number.
3. If exactly one matching Eve matter exists, use it and do not create.
4. If more than one exists, stop and record a blocked duplicate.
5. If none exists, select **New Matter**.
6. Enter the manifest's `eve_matter_name`.
7. Select **Sync from Microsoft SharePoint**.
8. Search/navigate the case library using `sharepoint_folder_query`.
9. Select only an exact, unique case-number folder.
10. Create the matter.
11. Re-open/search the matter and verify the case number and SharePoint sync
    indicator before recording success.

Never create the Eve matter when the SharePoint folder cannot be resolved.
Creating an empty matter now and "fixing it later" breaks idempotency.

## Deliver the Summary Through SharePoint

Preferred order:

1. **SharePoint connector/tool:** find the exact case-number folder and upsert
   `Neos Notes Summary.docx`.
2. **Locally synced root:** resolve exactly one direct case folder beneath
   `WIND_SHAREPOINT_CASEFILES_ROOT`, then copy/replace the DOCX atomically.
3. **Browser upload:** use only when the active browser supports a file chooser
   or CDP file input. Verify the final filename and modified timestamp.

Do not create a second similarly named folder. Do not upload to a parent folder
when the case folder is missing.

After delivery, open or resync the Eve matter and verify that the document is
visible to Eve. Eve's bridge may ingest on its normal schedule; record
`summary_uploaded` after SharePoint verification, and `synced` only after Eve
visibility is confirmed.

Record success:

```bash
python "$SKILL_SCRIPT" record \
  --case-number "24-00123" \
  --neos-case-id "OPTIONAL-ID" \
  --status summary_uploaded \
  --eve-matter-id "EVE-MATTER-ID" \
  --eve-matter-url "https://..." \
  --notes-fingerprint "SHA256-FROM-MANIFEST"
```

Record failures without pretending success:

```bash
python "$SKILL_SCRIPT" record \
  --case-number "24-00123" \
  --status blocked \
  --error "Multiple Eve matters matched the exact case number"
```

## Automatic Import

Start with a small recurring run and a five-action cap. A browser-driven job is
less reliable than an API integration, so use a reconciliation-first schedule.

Example Hermes instruction:

```text
Use the wind-neos-eve-import skill. Run the open-case reconciliation in the
configured signed-in browser. Honor the exclusion list before any Eve action.
Process at most five mutating cases, generate/update the SharePoint notes
summary, verify each result, and report only counts plus blockers—no client
names or note text.
```

Example schedule:

```bash
hermes cron create "30m" \
  "Use the wind-neos-eve-import skill. Reconcile open Neos cases into Eve with a five-action cap. Honor exclusions first. Verify SharePoint and Eve. Report counts and blockers only." \
  --name "Wind Neos to Eve reconciliation" \
  --skill wind-neos-eve-import \
  --workdir "/path/to/private/operator/workdir"
```

Check the installed Hermes version's `hermes cron create --help` before executing,
because CLI flags can change. Do not create a scheduled live job until:

- one dry run has the expected open-case and exclusion counts;
- one manually supervised case succeeds end to end;
- the browser profile remains authenticated after restart;
- the SharePoint delivery path is proven;
- the user approves the schedule and live-create mode.

Hermes cron has a three-minute run limit in current bundled guidance. If the
five-action run cannot finish in that window, reduce `--max-actions` to one or
two and let later runs drain the queue.

## Reconciliation Report

Return only:

- open cases seen;
- excluded;
- already current;
- Eve matters created;
- summaries updated/uploaded;
- deferred;
- blocked, grouped by reason.

Do not include client names, note excerpts, health information, or document
content in cron delivery messages.

## Common Pitfalls

1. **Name-only matching.** Names change and collide. Use Neos Case ID or exact
   normalized case number.
2. **Exporting all cases.** The source is the saved Open Case List, with a
   second explicit closed-status guard.
3. **Checking exclusions after Eve search.** The exclusion check is the first
   per-case action.
4. **Trusting a partial table scrape.** Compare collected rows with the visible
   report total and stop on a mismatch.
5. **Creating a matter before resolving SharePoint.** Folder resolution is a
   prerequisite, not cleanup.
6. **Generating a confident legal narrative.** The document is an operational
   communications summary, not legal analysis.
7. **Updating state before verification.** Record only what the browser and
   SharePoint surfaces prove.
8. **Writing private artifacts into the repo.** The control-plane script
   deliberately rejects a run output inside the current repository.
9. **Assuming browser automation is unattended forever.** SSO, MFA, CAPTCHA,
   UI changes, or session expiry must block the run and alert the operator.
10. **Running too many cases per cron tick.** Keep a small mutation cap so the
    job finishes and later runs continue idempotently.

## Verification Checklist

- [ ] Neos source is the Open Case List
- [ ] Collected case total matches the report total
- [ ] Collected note total matches the report total
- [ ] Exclusions were evaluated before Eve actions
- [ ] No closed-like status reached the create queue
- [ ] Eve was searched by exact case number before create
- [ ] Exactly one SharePoint case folder matched
- [ ] Matter name follows `<case number> - <case name>`
- [ ] Summary covers all exported notes and required sections
- [ ] `Neos Notes Summary.docx` exists in the case SharePoint folder
- [ ] Eve matter and sync state were visibly verified
- [ ] Local state was updated only after verification
- [ ] Delivery report contains counts and blockers, not client data
