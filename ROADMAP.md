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
- The app persists repo metadata and scan snapshots to data/scan_cache.json so the saved state survives a restart.
- The app keeps the current session repo cards and analysis results in memory during use, while the JSON snapshot stays small and portable.

### Tag taxonomy

- Global tags are user-managed and shared across repos and chats.
- The app persists the taxonomy and assignment metadata in data/tags.json.
- Tag names, notes, and colors can be created or updated without storing raw repo/chat payloads.

## 2. Two-Step Sync & UI Flow

### Seamless two-step batch processing

1. The GitHub sync response immediately seeds all repo titles into the UI grid.
2. Background workers finish deep scans and update the same cards in place.
3. The status area shows progress like "Analyzing 5/50 repos..." while the grid remains stable.

This behavior is implemented with keyed repo updates rather than re-rendering the entire grid.

## 3. Laya & confidence filtering

The app uses a lightweight Laya-style score model against the current taxonomy instead of a hardcoded tag list. The confidence threshold is persisted with the tag settings and is used to filter UI visibility and match scoring.

## 4. Descriptions and summaries

- Repository description length is configurable through a word budget.
- The app clamps generated text to the configured limit before showing it in the repo grid.
- Chat analysis still supports a short summary and closure classification, and the title-generation flow remains lightweight and local.

## 5. Chat corpus pipeline

- ChatGPT JSON and Gemini HTML/JSON exports are parsed in memory.
- Platform badges and closure analysis are retained.
- Topic shifts and complexity heuristics are supported as a lightweight local pipeline.
- Historical conversations are kept in memory and may be matched against repos using tag overlap.

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
- Time Travel prompts are generated from the in-memory repo state and the best-matching chat context.

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
- GitHub repo scan and seeded repo cards
- RAM-first ZIP analysis
- Laya-style tag scoring and confidence-aware matching
- local chat parsing and closure analysis
- settings persistence and compact cache files
- repo metadata persistence in data/scan_cache.json

## Open items

- Extend the full UI to expose more advanced tag bulk operations in the browser.
- Add a richer chat modal for topic splitting and complexity editing.
- Expand the code extraction pipeline for more framework-specific heuristics and route generation.
- Add fuller export panels and richer AI Linker scoring in the front-end.



