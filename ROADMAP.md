\# Local Repo Graveyard \& Chat Resurrection System

\## Product Requirements Document (PRD)



\### 1. Core Architecture \& Data Management

The system must run completely locally, processing everything in-memory without polluting the user's disk.



\#### In-Memory FIFO Cache

\*   \*\*Streaming Mechanics:\*\* The ingestion engine must stream GitHub repository ZIP files directly into a RAM buffer via memory streams (e.g., `StringIO`, `BytesIO`). Disk I/O writes are strictly prohibited during this pipeline.

\*   \*\*Cache Management:\*\* Implements a First-In, First-Out (FIFO) eviction strategy to drop the oldest repository context when the threshold is breached to minimize API calls.

\*   \*\*UI Slider Integration:\*\* A real-time UI slider dynamically controls the RAM cache allocation bounds:

&#x20;   \*   \*\*Minimum Capacity:\*\* 5 repositories

&#x20;   \*   \*\*Maximum Capacity:\*\* 20 repositories

&#x20;   \*   \*\*Behavior:\*\* Shrinking the slider past the current active cache size triggers immediate eviction of the oldest overflow units.



\#### Dynamic Tagging \& Confidence Slider

\*   \*\*CRUDS Schema:\*\* Standard Create, Read, Update, Delete, and Search operations for taxonomy management.

\*   \*\*Target Scopes:\*\* Global system tags can be cross-assigned to both structural entities: Code Repositories and Historical Chat Threads.

\*   \*\*Bulk Operations:\*\* Supports multi-selection for batch tag assignment and deletion across data grids.

\*   \*\*Confidence Threshold Slider:\*\* A global UI slider exposing a real-time 0% to 100% threshold filter. 

&#x20;   \*   Filters out AI-generated metadata, entity extractions, and semantic links whose underlying LLM confidence probability score falls below the selected value.



\#### Modular Export Engine

\*   \*\*Structured CSV Export:\*\* Allows users to pick specific fields (e.g., Repo Name, Abandonment Score, Tech Stack, Discovered Endpoints) and export the active grid view directly to a structured CSV file.

\*   \*\*WELCOME\_BACK.md Generator:\*\* Dynamically creates a downloadable markdown file tailored for each repository containing:

&#x20;   \*   Project status breakdown.

&#x20;   \*   Extracted `.env` configurations template.

&#x20;   \*   Suggested local recovery roadmap.



\---



\### 2. Universal Chat Corpus Pipeline

Processing legacy AI conversations to build a searchable knowledge base.



\#### Universal Parser (JSON \& HTML)

\*   \*\*Ingestion Format Compliance:\*\* Normalizes structural formatting mismatches across platforms.

&#x20;   \*   \*ChatGPT JSON Exports:\* Deeply nested conversation tree objects.

&#x20;   \*   \*Google Gemini Takeout Files:\* Features a native HTML converter specifically for Google Takeout Gemini exports to extract raw conversation nodes directly, completely eliminating the need for manual pre-conversion formats.

\*   \*\*Visual Platform Attribution:\*\* Every parsed interaction within the consolidated UI feed must explicitly feature a distinct visual badge/icon indicating the source system origin (e.g., ChatGPT branding vs. Gemini branding).



\#### Closure \& Intent Analysis

The pipeline must parse the terminal sequence of each historical interaction to accurately classify execution endings:

\*   `SOLVED`: The final turn contains affirmations (e.g., "Thanks, that works", "Perfect").

\*   `CONTEXT\_LOST`: The user suddenly abandoned a complex thread to start a completely disconnected baseline query within the same session.

\*   `TIMEOUT`: The conversation halted abruptly mid-execution without confirmation or explicit error resolutions.



\#### Extractive-Abstractive Summarization

A localized dual-stage linguistic parser targeting resource-constrained offline execution.

\*   \*\*Extractive Phase:\*\* Local algorithmic ranking (e.g., TextRank) isolates the top 3 most statistically significant sentences based on term density.

\*   \*\*Abstractive Phase (Ollama Integration):\*\* Passes the extracted data to a local Ollama model instance to parse, extract, and strictly output:

&#x20;   \*   A concise, exactly \*\*4-word title\*\*.

&#x20;   \*   A precise, \*\*1-sentence summary\*\*.

&#x20;   \*   An automated \*\*language classification flag\*\* (e.g., English, Spanish, Hungarian).



\#### Topic Shift \& Complexity Analysis

\*   \*\*Conversation Splitting:\*\* Computes semantic distance between consecutive prompts. If a radical structural topic shift is flagged mid-conversation, the pipeline auto-divides the log into distinct sub-chats.

\*   \*\*Complexity Categorization:\*\* Assigns metadata weights to classify threads into operational buckets:

&#x20;   \*   `Quick Question`: Minimal turns (≤ 3), straightforward debugging, or syntax lookups.

&#x20;   \*   `Deep Work`: High turn density, multi-file code blocks, or algorithmic architecture design.



\---



\### 3. Deep Code Extraction (Zero-Clone)

Extracting project context and metadata directly from the RAM buffer.



\#### Security \& Smell Hunter

Scans code structures extracted in memory for systemic anti-patterns without disk unpacking:

\*   \*\*Secret Scanner:\*\* High-entropy regex string matching for hardcoded API keys, bearer tokens, and private cryptographic certificates.

\*   \*\*God Classes Detector:\*\* Identifies structural code units violating single-responsibility metrics (e.g., files exceeding 1000+ lines of code or components containing too many distinct methods).

\*   \*\*Console Log Auditor:\*\* Aggregates instances of production-unfriendly tracking elements (e.g., excessive `console.log`, `print`, `var\_dump`).

\*   \*\*Abandonment Score (0-100):\*\* A multi-factor mathematical equation calculating the decay index based on git commit timestamps, unresolved TODO counts, dependency aging, and overall structural code smell density.



\#### Entity \& Endpoint Mapper

\*   \*\*Environment Template Extraction:\*\* Aggregates variable keys from local configurations (`.env`, `config.json`) and generates a safe, clean boilerplate template with all sensitive credential values blanked out.

\*   \*\*Database Schema Detection:\*\* Interrogates code configurations to map internal entity schemas (e.g., parsing Prisma, Mongoose, or SQLAlchemy initialization patterns).

\*   \*\*API Endpoint Ledger:\*\* Evaluates routing strings and annotations (e.g., `@app.route`, `router.get`, `app.post`) to generate a complete visual directory of all exposed network endpoints.



\#### Dependency \& Boilerplate Check

\*   \*\*Tech Stack Inventory:\*\* Parses package files (`package.json`, `requirements.txt`, `Cargo.toml`) to extract core framework definitions.

\*   \*\*Version Decay Alerts:\*\* Cross-references versions against a local vulnerability database to highlight out-of-date or deeply deprecated dependencies.

\*   \*\*Custom-to-Boilerplate Ratio:\*\* Computes total lines of custom application logic against standard framework configurations and auto-generated boilerplate patterns to map true proprietary codebase footprint.



\---



\### 4. Resurrection Tools \& Analytics

Connecting the repository graveyard with the chat corpus to facilitate project revival.



\#### The AI Linker

\*   \*\*System Modal:\*\* A diagnostic modal overlay that analyzes a selected, dead repository and cross-references its metadata tags against the entire parsed historical chat corpus.

\*   \*\*Semantic Matching:\*\* Ranks historical conversations based on overlapping tag density and context relevance, listing suggested matching threads to fill in missing development gaps.



\#### Time Travel Master Prompt

Generates a consolidated, production-ready, copy-pasteable context initialization markdown prompt block. This prompt aggregates:

1\.  \*\*Past Decisions:\*\* Summaries of context paths chosen in historical chats.

2\.  \*\*Current Architecture:\*\* The parsed entity mapping and technology footprint.

3\.  \*\*Inline TODOs:\*\* Extracted code comments signaling incomplete features.

4\.  \*\*Next Logical Step:\*\* An LLM-inferred developmental objective detailing exactly what action the developer should execute next to resume production effectively.



\#### Graveyard Analytics

A consolidated, responsive, dashboard layout featuring high-level metrics optimized for rapid visual review and screenshots:



| Metric | Scope / Scale | Functional Goal |

| :--- | :--- | :--- |

| \*\*Coma Index\*\* | Linear Range: `Fresh Dead` → `Fossil` | Measures time elapsed since the project's last active code state update. |

| \*\*Mood X-Ray\*\* | Categorical: `Frustrated` \\| `Bored` \\| `Done` | Uses sentiment analysis on past chats to diagnose \*why\* the user quit. |

| \*\*Resurrection Cost\*\* | T-shirt Sizes: `S`, `M`, `L`, `XL` | Estimates resource commitment needed based on size, dependency decay, and smells. |



\---



\### 5. Advanced UX, Batch Processing \& Configuration

Ensuring a smooth, enterprise-grade user experience during heavy local LLM processing.



\#### Seamless Two-Step Batch Processing

\*   \*\*Decoupled Orchestration:\*\* Separates metadata discovery from computation-heavy deep analysis pipelines.

\*   \*\*Step 1 (Instant Ingestion):\*\* Rapidly fetches, populates, and persists the raw shell list of available repositories immediately to the UI grid.

\*   \*\*Step 2 (Background Deep Scan):\*\* Spawns worker threads to execute heavy memory ZIP extractions, regex parsing, and local Ollama analysis loops in configurable background chunks.

\*   \*\*Asynchronous UI Updates:\*\* The interface updates individual repository data cards reactively using smooth polling hooks or live WebSockets. Screen jumping, interface locking, or grid layout flickering during live updates is strictly prohibited.



\#### Real-Time Progress \& Activity Logging

\*   \*\*Global Status Bars:\*\* Provides persistent visual tracking progress bars for batch processing operations (e.g., displaying message strings like `"Analyzing 5/50 repos..."`).

\*   \*\*Collapsible UI Terminal Log:\*\* Renders an optional, embedded terminal view tracking live execution logs. The terminal surfaces background operational statuses, active processing queues, parsing exceptions, and LLM timeout warnings.



\#### Granular Settings Panel

An expanded modal layout providing absolute control over local system constraints:

\*   \*\*Chunk Tuning:\*\* Configures batch chunk capacities to adjust concurrent queue thresholds.



