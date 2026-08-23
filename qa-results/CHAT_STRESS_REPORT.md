# 100 Private Chat Workflow Stress Report

## Scope

The product now has an owner-scoped durable Business Chat conversation API backed by separate Mongo conversation and message collections. This test created **100 deterministic-random private Business Chat workflow records** and durable in-memory conversation records inside an isolated Playwright API fixture. It did not write to MongoDB or call providers.

Executed on:

- Desktop Chromium: 1920×1080
- Mobile Chromium: 390×844

## Results

| Scenario | Desktop | Mobile |
|---|---:|---:|
| Load and search 100 randomized chats | PASS | PASS |
| Unicode and no-results search | PASS | PASS |
| Rapidly switch among 25 chats | PASS | PASS |
| Cross-chat identity contamination | PASS | PASS |
| Prompt template insertion | PASS | PASS |
| Response format and writing style | PASS | PASS |
| Model override | PASS | PASS |
| Workflow context collapse/reopen | PASS | PASS |
| Message execution and run association | PASS | PASS |
| Ten same-tick Send actions | PASS after fix | PASS after fix |
| Durable transcript restoration after refresh | PASS after fix | PASS after fix |
| Publication request isolation | PASS | PASS |
| Archive isolation | PASS | PASS |
| Browser Back/Forward identity | PASS | PASS |

**Browser executions: 14 passed / 14.**

## Defect Found and Fixed

### Duplicate workflow runs from same-tick Send events

Before the fix, dispatching ten click events to the enabled Send button in one browser task produced **10 POST `/api/workflows/run` requests**.

Root cause: `running=true` is a React state update and does not synchronously disable the button during the current JavaScript task.

Fix: `BusinessChatConversation` now uses a synchronous `runSubmissionRef` guard before adding a message or starting an API request. The guard resets after the run stops or launch fails.

The retained ten-click Playwright regression now produces exactly one run on desktop and mobile.

### Transcript lost on browser refresh

Before the fix, all Business Chat messages lived only in React component state and disappeared on reload.

Fix: Business Chat now resolves one owner-scoped conversation per workflow, persists ordered idempotent transcript messages, passes the durable `conversation_id` and `message_id` into workflow execution, and restores the latest run context after reload. Conversation metadata and messages are stored separately so transcripts do not grow a single Mongo document toward the 16 MB limit.

The retained refresh regression now restores Unicode user and assistant messages exactly once on desktop and mobile. It also verifies that reload does not launch a second workflow run.

## Additional Validation

- Frontend suite: **46 files / 416 tests passed**.
- Backend suite: **2005 passed / 2 skipped**.
- ESLint: PASS.
- TypeScript: PASS.
- Production build: PASS.
- Initial JS bundle: **269.65 KB** / **83.65 KB gzip**.
- Business Chat lazy chunk: **86.20 KB** / **24.04 KB gzip**.
- `git diff --check`: PASS.

## Remaining Limitation

None for durable single-owner Business Chat transcript restoration. Multi-tab concurrency still relies on idempotent message IDs and Mongo unique indexes; broader multi-tab browser stress remains optional follow-up coverage.