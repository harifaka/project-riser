# Project Riser

Project Riser is a local resurrection suite for abandoned repos and archived AI conversations. It keeps repository ZIPs, code analysis, and chat bodies in memory while persisting only the lightweight metadata needed to resume work across restarts. The app is designed for developer recovery workflows: scan GitHub repos, surface the titles immediately, then refresh cards as deeper analysis completes; link old chats to a repo; and generate a Time Travel prompt for re-entry.

## What it is

Project Riser is a privacy-first local tool for:
- syncing repository metadata from GitHub without cloning repos to disk
- scanning ZIPs in RAM and extracting smells, TODOs, env keys, endpoints, and tech stack hints
- analyzing chat exports from ChatGPT and Gemini for closure, complexity, and topic shifts
- ranking repo/chat matches through a lightweight taxonomy and confidence filter
- generating summaries, mood/effort estimates, and a resurrection prompt for the current codebase

The system deliberately avoids persisting raw ZIP bytes, full chat bodies, or generated output files in the repository. It keeps the heavy operational state in RAM and stores a compact JSON cache containing repo metadata, tag definitions, and settings.

## How it works

The app uses a dual-engine model:
- Laya / System 1: tag scoring over the user-managed taxonomy for repos and chats
- Ollama / System 2: summaries, mood, effort, and closure logic for the local analysis pipeline

Repository sync lists every repo card before any ZIP download. A content hash of `pushed_at`, size, and repo id, plus a fingerprint of the tag list, decides whether a card is reused from `data/scan_cache.json`. Only changed repos are analyzed, a few at a time, in memory. The header shows a percent complete, and a scan log records which Laya backend is in use. Cards stay in place, so Time Travel and tag selection keep working while other repos are still running. Disk writes of the analysis snapshot are batched.

## Repository graveyard

The repo graveyard includes:
- hash cache so an unchanged repo and an unchanged tag list skip reprocessing
- FIFO in-memory zip cache for active repo context
- custom vs boilerplate heuristics for code footprint analysis
- secret, TODO, smell, env, schema, and endpoint extraction from ZIP contents
- abandonment scoring based on age and code signals
- adjustable description length for repo summaries
- Time Travel prompt generation for resuming work on an abandoned project

## Chat corpus

The chat pipeline accepts ChatGPT JSON and Gemini HTML/JSON exports and keeps the original content in RAM. On import it stores a short summary, a closure label (`SOLVED`, `CONTEXT_LOST`, `TIMEOUT`), a calendar day when the export has one, and Laya scores for every current tag.

The Chat tab can be read while tagging runs. It shows a source and closure summary, a GitHub-style day heatmap, and a reverse count of how many conversations match each tag at the confidence threshold. Opening a conversation shows the summary and the stored text. **Retag conversations** rescores every loaded conversation against the current tag list and does not start a GitHub sync. Manual tag assignments stay unless that tag was removed.

## Taxonomy and linking

The app maintains a single global taxonomy. Settings opens a tag-list modal for create, rename, and delete, and a separate Laya modal for backend (`auto`, `transformers`, `ollama`, `keyword`), model id, and the confidence slider. Repo and chat cards can be multi-selected and bulk-assigned. Laya scores each conversation and each analyzed repo against that tag list. `auto` tries the optional transformers zero-shot model, then Ollama JSON scores, then keyword overlap.

**Link conversations** matches loaded chats to ready repositories by how the project behaves, not by its current name. A cheap pass keeps pairs whose Laya scores and wording overlap above the confidence threshold. Only those candidates go to the LLM, in batches of the configured chunk size, for a match score and a one-sentence reason. One repo can keep several conversations, and one conversation can match more than one repo. The results are stored on the repo as ids, scores, reasons, and a fingerprint of the title, date, and text prefix, then written with the batched scan cache. The chat body stays in memory. Unlink keeps a conversation off that repo on later runs. Pin keeps a link even if the next run would drop it.

Each repo card shows how many conversations are linked. Opening the list shows the title, reason, and score, with Open, Pin, and Unlink. Time Travel builds the past-memory section from those linked plans. If nothing is linked, the prompt says so and does not pull in an unrelated chat. GitHub sync does not start this matching pass.

## Analytics

The dashboard includes:
- Coma Index
- Mood X-Ray
- T-shirt effort sizing
- AI corpus match score
- repo-level analytics based on real tag overlap rather than a static mock heuristic

## Operations

The interface includes:
- GitHub PAT, Ollama URL, model, and RAM cache size
- a percent bar and collapsible scan log during repo sync
- a percent bar while conversation retagging is running
- Laya calibration and the tag editor, each behind its own settings button

## Setup

1. Install dependencies with `pip install -r requirements.txt`.
2. Optionally install the separate Laya model stack with `pip install -r requirements-laya.txt`.
3. Start the local Ollama service and ensure a model is available, such as `llama3.1`.
4. Run `docker-compose up --build` from the project root.
5. Open http://localhost:5000 and provide your GitHub PAT and Ollama connection details.

The application is designed to stay local. It does not clone repos to disk, does not write chat exports to uploads/, and does not dump generated analysis artifacts into output/.
