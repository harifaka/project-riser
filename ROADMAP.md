# Local Repo Graveyard & Chat Resurrection System

## Product Requirements Document

This roadmap reflects the shipped RAM-first architecture for Project Riser. The product keeps raw ZIP bytes, chat bodies, and all analysis state in memory. A lightweight JSON cache persists repository metadata, tag definitions, and settings so the app can resume without storing the heavy operational payloads on disk.

## 1. Core Architecture & Data Management

### RAM vs disk

- Repository processing, ZIP bytes, code scanning, and AI prompts remain in memory.
- Message bodies and parsed conversation text stay in RAM, never as uploads/ copies.
- Generated exports are streamed in memory and downloaded without writing to output/.
- The only on-disk cache is a compact JSON file for taxonomy and persisted repo metadata; it does not store raw repo content or chat dumps.
- Hugging Face model caches are optional and not required for the default local Ollama workflow.

### FIFO cache and scan persistence

- The active repo ZIP cache uses an in-memory FIFO strategy with a default capacity of 5 repos.
- The app persists repo metadata and analysis snapshots to data/scan_cache.json so the saved state survives a restart.
- A content hash of GitHub `pushed_at`, size, and repo id, together with a tag-list fingerprint, skips reprocessing when neither changed.
- Snapshot writes are batched. ZIP bytes and chat bodies stay in memory.

### Tag taxonomy

- Global tags are user-managed and shared across repos and chats.
- The app persists the taxonomy and assignment metadata in data/tags.json.
- Tag names, notes, and colors can be created or updated without storing raw repo/chat payloads.

## 2. Two-Step Sync & UI Flow

### Seamless two-step batch processing

1. The GitHub sync response immediately seeds every repo card into the UI grid.
2. A bounded pool analyzes only repos whose content hash or tag fingerprint changed, and updates those cards in place.
3. A percent bar shows ready or error cards against the total, including how many came from cache. A collapsible log shows worker and Laya events. Cards stay interactive during the scan.

Dead LoC and Dead Repos are derived from the current cards, so the counters follow finished analysis instead of resetting at the start of a sync.

## 3. Laya & confidence filtering

The app uses a lightweight Laya-style score model against the current taxonomy instead of a hardcoded tag list. The confidence threshold is persisted with the tag settings and is used to filter UI visibility and match scoring.

## 4. Descriptions and summaries

- Repository description length is configurable through a word budget.
- The app clamps generated text to the configured limit before showing it in the repo grid.
- Chat analysis still supports a short summary and closure classification, and the title-generation flow remains lightweight and local.

## 5. Chat corpus pipeline

- ChatGPT JSON and Gemini HTML/JSON exports are parsed in memory.
- Import analysis stores a summary, closure, an optional calendar day, and Laya tag scores.
- The Chat tab reads conversations, draws a day heatmap, and counts matches per tag.
- Retagging reruns Laya on loaded conversations when the tag list changes, without a GitHub sync.
- Manual assignments are kept separately from Laya scores.

## 6. Deep extraction and code intelligence

The repository analyzer fills fields such as:
- tech stack from package and manifest files
- env keys from .env and config files
- database schema hints from common framework patterns
- route patterns and API endpoints
- smell and console-log watchers
- abandonment score built from age and code signals

The extracted fields are written to the lightweight JSON snapshot and are not the same as the ZIP bytes or the raw source tree itself.

## 7. Export and memory-first downloads

- CSV and welcome-back exports are streamed from memory.
- Download generation avoids output/ files and does not persist those artifacts to disk.
- Time Travel prompts are generated from the in-memory repo state and the conversations linked to that repo.
- **Link conversations** runs separately from GitHub sync. It keeps chats whose behavior overlaps the repo dossier above the confidence threshold, then asks the LLM only for those candidates. Several chats can attach to one repo. Links persist in the scan snapshot by fingerprint, without the chat body. Unlink and pin override the next automatic pass.
- If a repo has no linked conversations, Time Travel says so and does not insert an unrelated chat.

## 8. Settings & operations

The live settings include:
- cache size in the 5–20 range
- chunk size
- description word budget
- Laya backend selection
- confidence threshold
- Ollama URL and model
- GitHub PAT

These settings are saved in the compact tag/settings JSON cache so they survive restart without copying the repository data itself.

## 9. Current product status

The project currently ships the following core behavior:
- GitHub repo scan that seeds every card, then analyzes changed repos in parallel
- Hash and tag-fingerprint cache with batched scan snapshots
- Percent progress, scan log, and in-place card updates
- Laya tagging for repos and conversations, with a calibration modal
- Tag editor and bulk assign or unassign
- Chat reader with heatmap, summary, source metadata, and per-tag counts
- Conversation retag independent of GitHub sync
- Behavior-based conversation linking that feeds Time Travel and supports pin/unlink flows
- Strict chat summaries with a 4-word title, single-sentence description, and language hint
- Mental-debt comment detection from ZIP content, surfaced in the repo smell/mood signal

## 10. Open Items & Immediate Action Plan (For Cursor)

The high-priority roadmap items above are now implemented and verified in the app.

**Completed fixes**
- Chat viewer and RAM-based parsing for ChatGPT/Gemini exports are working in the UI.
- Repository-to-chat matching now filters by confidence and keeps the best behavioral overlaps for Time Travel.
- Strict chat summaries and emotional-comment extraction are included in the code path that imports conversations and analyzes ZIPs.

**Optional follow-ups**
- Extend the banter detector with more domain-specific emotional phrases if the team wants a broader signal set.
- Add more structured analytics around mental debt, such as a separate chart or threshold-based alerting.