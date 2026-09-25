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

Repository sync is a two-step flow. The GitHub scan returns all repo titles immediately, and each card updates in place as background workers finish their ZIP analysis, Laya tags, and Ollama summaries. This keeps the UI responsive while the deeper scan continues.

## Repository graveyard

The repo graveyard includes:
- FIFO in-memory zip cache for active repo context
- custom vs boilerplate heuristics for code footprint analysis
- secret, TODO, smell, env, schema, and endpoint extraction from ZIP contents
- abandonment scoring based on age and code signals
- adjustable description length for repo summaries
- Time Travel prompt generation for resuming work on an abandoned project

## Chat corpus

The chat pipeline accepts ChatGPT JSON and Gemini HTML/JSON exports, keeps the original content in RAM, and surfaces:
- file-level metadata and platform badges
- closure classification: SOLVED, CONTEXT_LOST, TIMEOUT
- extractive plus LLM summary generation
- four-word title and sentence summary patterns for chat threads
- topic-shift awareness and complexity buckets

## Taxonomy and linking

The app maintains a single global taxonomy that can be created, renamed, deleted, and bulk-assigned across repos and chats. Laya scores are computed against the user-managed tag set, and the confidence slider filters what appears in the UI and AI Linker recommendations.

## Analytics

The dashboard includes:
- Coma Index
- Mood X-Ray
- T-shirt effort sizing
- AI corpus match score
- repo-level analytics based on real tag overlap rather than a static mock heuristic

## Operations

The interface includes:
- GitHub PAT and Ollama settings
- cache size and chunk-size tuning
- description word budget configuration
- Laya backend selection and confidence threshold
- scan log and status updates
- export helpers that build files in memory and stream downloads without writing to output/

## Setup

1. Install dependencies with `pip install -r requirements.txt`.
2. Optionally install the separate Laya model stack with `pip install -r requirements-laya.txt`.
3. Start the local Ollama service and ensure a model is available, such as `llama3.1`.
4. Run `docker-compose up --build` from the project root.
5. Open http://localhost:5000 and provide your GitHub PAT and Ollama connection details.

The application is designed to stay local. It does not clone repos to disk, does not write chat exports to uploads/, and does not dump generated analysis artifacts into output/.
