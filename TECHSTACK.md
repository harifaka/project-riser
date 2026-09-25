\# 🛠️ Tech Stack \& Architecture: Project Riser



Project Riser utilizes a highly optimized, dual-engine AI architecture paired with a lightweight, enterprise-grade web stack. It is designed to run 100% locally, processing massive amounts of code and text entirely in-memory.



\## 🧠 The Dual-Engine AI Architecture



We separate "cheap/fast" categorization from "expensive/slow" generation to process large codebases and chat histories on consumer hardware (e.g., 12GB VRAM).



\### 1. The "System 1" Engine (1decision Model)

The rapid, semantic decision-making layer.

\*   \*\*Model:\*\* \*\*Laya\*\* (or Open Source equivalent: DeBERTa-v3 / Zero-Shot Classifier)

\*   \*\*Role:\*\* The "Categorizer". It does not generate text. It outputs structured probability scores (JSON/Boolean/Float) extremely fast (ms).

\*   \*\*App Function:\*\* Powers the \*\*AI Linker\*\* by semantically reading code snippets and chat logs, assigning tags (e.g., `frontend`, `debugging`) based on mathematical vector overlap, rather than simple keyword matching.

\*   \*\*Footprint:\*\* Very small (\~400M parameters), occupying < 2GB VRAM.



\### 2. The "System 2" Engine (Generative Model)

The analytical, synthesizing layer.

\*   \*\*Model:\*\* Local \*\*Ollama\*\* API (Recommended: \*\*Llama 3.1 8B\*\*)

\*   \*\*Role:\*\* The "Brain". It receives pre-filtered, condensed context (commit messages, snippets) from the System 1 engine.

\*   \*\*App Function:\*\* Generates the `WELCOME\_BACK.md` files, estimates \*\*T-Shirt Sizing\*\* (S, M, L, XL), determines \*\*Mood X-Ray\*\* (Frustrated/Done), and categorizes chat closure reasons (`SOLVED`, `TIMEOUT`).



\---



\## 💻 The Classic Web Stack



\### Backend \& Data Processing

\*   \*\*Language:\*\* Python 3.10+

\*   \*\*Web Framework:\*\* \*\*Flask\*\* (RESTful API architecture) + \*\*Gunicorn\*\* (WSGI HTTP Server for multi-threading).

\*   \*\*Async Processing:\*\* `concurrent.futures.ThreadPoolExecutor` for background batch jobs (analyzing downloaded repos while fetching the next one).

\*   \*\*Zero-Clone Memory Management:\*\* `io.BytesIO` and `zipfile` for extracting GitHub ZIP streams directly in RAM. `collections.OrderedDict` for the FIFO Virtual RAM Cache.

\*   \*\*Integrations:\*\* 

&#x20;   \*   `PyGithub`: For fetching repositories, branches, and commit metadata.

&#x20;   \*   `re` (Regex): For hardcoded secret hunting and API endpoint mapping.



\### Frontend (UI/UX)

\*   \*\*Core:\*\* Single Page Application (SPA) built with \*\*Vanilla JavaScript (ES6)\*\* and \*\*HTML5\*\*. No heavy frameworks (React/Vue) to keep the tool lightweight and easily hackable.

\*   \*\*Styling:\*\* Custom CSS3 with a Modern Dark Mode theme, utilizing CSS Variables, Flexbox, and CSS Grid.

\*   \*\*Libraries:\*\* `html2canvas` for generating shareable PNGs of the Graveyard Analytics dashboard.



\### Infrastructure \& Deployment

\*   \*\*Containerization:\*\* \*\*Docker\*\* and \*\*Docker Compose\*\*.

\*   \*\*Hardware Acceleration:\*\* NVIDIA Container Toolkit (GPU passthrough) to allow the Python container to utilize the host's GPU for local LLM inferencing.

\*   \*\*Tooling:\*\* The Docker image includes Node.js to prepare the environment for future native `package.json` or framework-specific deep scanning.

