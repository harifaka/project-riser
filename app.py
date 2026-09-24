import os
import json
import uuid
import datetime
import requests
import re
import io
import zipfile
import html
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, jsonify, send_file
from github import Github
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['OUTPUT_FOLDER'] = 'output'
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

class FIFOCache:
    def __init__(self, capacity=5):
        self.capacity = capacity
        self.cache = OrderedDict()
    def set_capacity(self, new_capacity):
        self.capacity = new_capacity
        self._trim()
    def get(self, key):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None
    def put(self, key, value):
        self.cache[key] = value
        self.cache.move_to_end(key)
        self._trim()
    def _trim(self):
        while len(self.cache) > self.capacity:
            self.cache.popitem(last=False)

class StateManager:
    def __init__(self):
        self.chat_tags = ["backend", "frontend", "debugging", "architecture", "database", "devops"]
        self.repo_tags = ["backend", "frontend", "abandoned", "react", "python", "data_science"]
        self.conversations = []
        self.chat_files = []
        self.repos = []
        self.stats = {"total_loc": 0, "abandoned_count": 0, "secrets_found": 0, "smells_found": 0}
        self.repo_cache = FIFOCache(capacity=5)

state = StateManager()
executor = ThreadPoolExecutor(max_workers=4)

class LLMService:
    @staticmethod
    def list_available_models(url):
        if not url:
            return ["llama3.1"]
        try:
            res = requests.get(f"{url}/api/tags", timeout=15)
            if res.status_code != 200:
                return ["llama3.1"]
            payload = res.json() or {}
            models = []
            for entry in payload.get("models", []):
                if isinstance(entry, dict) and entry.get("name"):
                    models.append(entry["name"])
            if models:
                return models
            for entry in payload.get("data", []):
                if isinstance(entry, dict) and entry.get("name"):
                    models.append(entry["name"])
            return models or ["llama3.1"]
        except Exception:
            return ["llama3.1"]

    @staticmethod
    def ask(prompt, url, model):
        if not url or not model: return "LLM not configured."
        try:
            res = requests.post(f"{url}/api/generate", json={"model": model, "prompt": prompt, "stream": False}, timeout=45).json()
            return res.get("response", "").strip()
        except: return "LLM Timeout or Error."

class ChatAnalyzer:
    @staticmethod
    def _clean_text(value):
        if not value:
            return ""
        text = html.unescape(str(value))
        text = re.sub(r"<[^>]+>", " ", text, flags=re.DOTALL)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _extract_html_text(raw_html):
        if not raw_html:
            return []
        text = re.sub(r"(?is)<script.*?</script>", " ", raw_html)
        text = re.sub(r"(?is)<style.*?</style>", " ", text)
        blocks = []
        for pattern in [
            r"(?is)<(?:p|li|div|article|span|pre|h[1-6]|td|tr)\b[^>]*>(.*?)</(?:p|li|div|article|span|pre|h[1-6]|td|tr)>",
            r"(?is)<title\b[^>]*>(.*?)</title>",
            r"(?is)\b(?:prompt|response|user|assistant|message|content)\b[^<]*<.*?>(.*?)</.*?>"
        ]:
            for match in re.finditer(pattern, text):
                cleaned = ChatAnalyzer._clean_text(match.group(1))
                if cleaned:
                    blocks.append(cleaned)
        if not blocks:
            blocks = [ChatAnalyzer._clean_text(re.sub(r"<[^>]+>", " ", text, flags=re.DOTALL))]
        return [b for b in blocks if b]

    @staticmethod
    def _extract_from_mapping(mapping):
        parts = []

        def add_value(value):
            if isinstance(value, str):
                if value.strip():
                    parts.append(value)
            elif isinstance(value, list):
                for item in value:
                    add_value(item)
            elif isinstance(value, dict):
                for key in ("text", "content", "parts", "value", "message", "response", "prompt"):
                    if key in value:
                        add_value(value[key])

        if not isinstance(mapping, dict):
            return parts

        for node in mapping.values():
            if isinstance(node, dict):
                add_value(node)
            elif isinstance(node, str):
                add_value(node)

        return [p for p in parts if p]

    @staticmethod
    def _extract_from_json(data):
        found = []
        if isinstance(data, list):
            for item in data:
                found.extend(ChatAnalyzer._extract_from_json(item))
        elif isinstance(data, dict):
            if "mapping" in data:
                found.append({"title": data.get("title", "Imported Chat"), "source": "ChatGPT", "text_parts": ChatAnalyzer._extract_from_mapping(data.get("mapping"))})
            elif "messages" in data:
                text_parts = []
                for msg in data.get("messages", []):
                    if isinstance(msg, dict):
                        if "text" in msg and isinstance(msg["text"], str):
                            text_parts.append(msg["text"])
                        for key in ("content", "parts", "value"):
                            if key in msg:
                                value = msg[key]
                                if isinstance(value, str):
                                    text_parts.append(value)
                                elif isinstance(value, list):
                                    text_parts.extend([str(v) for v in value if isinstance(v, str)])
                if text_parts:
                    found.append({"title": data.get("title", "Imported Chat"), "source": "Gemini", "text_parts": text_parts})
            else:
                for key in ("title", "text", "content", "prompt", "response", "value"):
                    if key in data and isinstance(data[key], str):
                        found.append({"title": data.get("title", "Imported Chat"), "source": "Gemini", "text_parts": [data[key]]})
                        break
                if not found:
                    if "conversations" in data:
                        found.extend(ChatAnalyzer._extract_from_json(data["conversations"]))
                    if "chat" in data:
                        found.extend(ChatAnalyzer._extract_from_json(data["chat"]))
        return found

    @staticmethod
    def parse_and_analyze(file_data, url, model):
        extracted = []
        raw_text = (file_data or "").strip()
        if not raw_text:
            return extracted

        try:
            payload = json.loads(raw_text)
            parsed_entries = ChatAnalyzer._extract_from_json(payload)
        except Exception:
            parsed_entries = []

        if not parsed_entries and ("<html" in raw_text.lower() or "<div" in raw_text.lower() or "<p" in raw_text.lower()):
            title = re.search(r"(?is)<title\b[^>]*>(.*?)</title>", raw_text)
            title_text = ChatAnalyzer._clean_text(title.group(1)) if title else "Gemini Export"
            parsed_entries = [{"title": title_text, "source": "Gemini", "text_parts": ChatAnalyzer._extract_html_text(raw_text)}]

        if not parsed_entries:
            parsed_entries = [{"title": "Imported Chat", "source": "Gemini", "text_parts": [raw_text[:4000]]}]

        for conv in parsed_entries:
            text_parts = conv.get("text_parts", [])
            if not text_parts:
                continue
            title = conv.get("title", "Imported Chat")
            source = conv.get("source", "Gemini")
            full_text = " ".join(text_parts)
            last_messages = " ".join(text_parts[-3:])
            prompt = f"Analyze the end of this dev chat. Did the user get a solution (SOLVED), did the AI fail giving bad context (CONTEXT_LOST), or did it just end abruptly (TIMEOUT)? Reply with ONE exact word.\nChat end: {last_messages[-1000:]}"
            closure = LLMService.ask(prompt, url, model)
            if closure not in ["SOLVED", "CONTEXT_LOST", "TIMEOUT"]: closure = "TIMEOUT"
            extracted.append({
                "id": str(uuid.uuid4()), "title": title, "source": source,
                "text": full_text[:3000], "closure_reason": closure, "tags": {"backend": 0.8}
            })
        return extracted

class RepoAnalyzer:
    def __init__(self, token, ollama_url, ollama_model):
        self.headers = {"Authorization": f"token {token}"}
        self.ollama_url = ollama_url
        self.ollama_model = ollama_model

    def fetch_zip(self, repo_name):
        cached = state.repo_cache.get(repo_name)
        if cached: return cached
        resp = requests.get(f"https://api.github.com/repos/{repo_name}/zipball", headers=self.headers)
        if resp.status_code == 200:
            state.repo_cache.put(repo_name, resp.content)
            return resp.content
        return None

    def analyze(self, repo_data):
        name = repo_data['full_name']
        zip_content = self.fetch_zip(name)
        if not zip_content: return
        
        data = {"todos": [], "secrets": [], "env_vars": set(), "db_schemas": set(), "tech_stack": [], "custom_lines": 0, "smells": []}
        snippets = []
        sec_pat = re.compile(r"(?i)(api_key|secret|password|token)\s*[:=]\s*['\"][a-zA-Z0-9_\-]{10,}['\"]")
        
        with zipfile.ZipFile(io.BytesIO(zip_content)) as z:
            for info in z.infolist():
                if info.is_dir() or info.file_size > 500000 or "node_modules" in info.filename: continue
                fname = info.filename
                if fname.endswith((".py", ".js", ".ts", ".rs", ".go")):
                    try:
                        content = z.read(info).decode('utf-8', errors='ignore')
                        lines = content.split('\n')
                        data["custom_lines"] += len(lines)
                        if len(lines) > 1000: data["smells"].append(f"God File: {fname.split('/')[-1]}")
                        for i, line in enumerate(lines):
                            if "TODO:" in line or "FIXME:" in line: data["todos"].append(line.strip()[:60])
                            if sec_pat.search(line): data["secrets"].append(fname.split('/')[-1])
                        if len(snippets) < 2 and len(lines) > 15: snippets.append("\n".join(lines[:40]))
                    except: pass

        state.stats["total_loc"] += data["custom_lines"]
        state.stats["secrets_found"] += len(data["secrets"])
        state.stats["smells_found"] += len(data["smells"])

        commits = "\n".join(repo_data['commits'])
        mood = LLMService.ask(f"Analyze the developer's mood from these commits. Are they FRUSTRATED, BORED, or DONE? Reply one word:\n{commits}", self.ollama_url, self.ollama_model)
        tshirt = LLMService.ask(f"Estimate resurrection effort (S, M, L, XL) based on {data['custom_lines']} lines of code and {len(data['todos'])} TODOs. Reply with one letter/word only.", self.ollama_url, self.ollama_model)
        desc = LLMService.ask(f"Write a 1-sentence summary of this code:\n{' '.join(snippets)}", self.ollama_url, self.ollama_model) if snippets else "No code extracted."
        
        days_abandoned = (datetime.datetime.now(datetime.timezone.utc) - repo_data['last_update']).days if repo_data['last_update'] else 0
        abandon_score = min(100, int((days_abandoned / 365) * 100))
        if abandon_score > 70: state.stats["abandoned_count"] += 1

        profile = {
            "id": repo_data['id'], "name": name, "description": desc, "mood": mood, "tshirt": tshirt,
            "abandon_score": abandon_score, "days_abandoned": days_abandoned, "smells": data["smells"], 
            "todos": data["todos"], "secrets": len(data["secrets"]), "tech_stack": list(set(data["tech_stack"])), 
            "tags": {"backend": 0.9} 
        }
        state.repos.append(profile)

class TimeTravelService:
    @staticmethod
    def generate_master_prompt(repo_id, token):
        repo = next((r for r in state.repos if str(r["id"]) == str(repo_id)), None)
        if not repo: return "Repo not found in state."
        best_chat = state.conversations[0] if state.conversations else None
        
        zip_content = state.repo_cache.get(repo["name"])
        if not zip_content:
            resp = requests.get(f"https://api.github.com/repos/{repo['name']}/zipball", headers={"Authorization": f"token {token}"})
            if resp.status_code == 200:
                zip_content = resp.content
                state.repo_cache.put(repo["name"], zip_content)
                
        readme_content = "No README found."
        main_code = ""
        if zip_content:
            with zipfile.ZipFile(io.BytesIO(zip_content)) as z:
                for info in z.infolist():
                    if "README.md" in info.filename.upper(): readme_content = z.read(info).decode('utf-8', errors='ignore')[:1000]
                    if info.filename.endswith(("main.py", "index.js", "app.py", "App.js")) and not main_code:
                        main_code = z.read(info).decode('utf-8', errors='ignore')[:1000]

        chat_context = f"We were discussing: {best_chat['text'][-1000:]}\nThe AI conversation ended because: {best_chat['closure_reason']}" if best_chat else "No prior AI conversations linked."
        prompt = f"""# TIME TRAVEL MASTER PROMPT
**Role:** You are a senior AI coding assistant. We are resuming development on an abandoned project. You must act as if no time has passed.

## 1. Project Context
**Project Name:** {repo['name']}
**Current Assessment:** Effort is {repo['tshirt']}, developer left feeling {repo['mood']}.
**Description:** {repo['description']}

## 2. Past AI Memory
{chat_context}

## 3. Current Architecture & Code State
**README Snippet:**
```
{readme_content}
```
**Main Entry Point Snippet:**
```
{main_code}
```
**Remaining TODOs:**
{chr(10).join(['- ' + t for t in repo['todos']])}

## 4. Your Mission
Based on the code state and the past AI conversation, please provide:
1. A brief summary of where we left off.
2. The exact, specific next step (a single task) I need to code right now to get back into the flow. Do not give me a massive list, just the next logical block.
"""
        return prompt

@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/stats', methods=['GET'])
def get_stats(): return jsonify(state.stats)

@app.route('/api/repos', methods=['GET'])
def get_repos(): return jsonify({"repos": state.repos})

@app.route('/api/analytics', methods=['GET'])
def get_analytics():
    # Kóma-Index
    coma_index = {"Fresh": 0, "Decomposing": 0, "Skeleton": 0, "Fossil": 0}
    moods = {"Frustrated": 0, "Bored": 0, "Done": 0, "Other": 0}
    tshirts = {"S": 0, "M": 0, "L": 0, "XL": 0}
    ai_potential = 0

    for r in state.repos:
        # Coma
        days = r.get('days_abandoned', 0)
        if days < 30: coma_index["Fresh"] += 1
        elif days < 180: coma_index["Decomposing"] += 1
        elif days < 365: coma_index["Skeleton"] += 1
        else: coma_index["Fossil"] += 1
        
        # Mood
        m = str(r.get('mood', '')).upper()
        if "FRUSTRAT" in m: moods["Frustrated"] += 1
        elif "BORED" in m: moods["Bored"] += 1
        elif "DONE" in m: moods["Done"] += 1
        else: moods["Other"] += 1
        
        # T-Shirt
        ts = str(r.get('tshirt', '')).upper()
        if ts in tshirts: tshirts[ts] += 1
        
        # Mock AI Potential based on available chats
        ai_potential += min(100, len(state.conversations) * 10)

    ai_potential_score = (ai_potential / max(1, len(state.repos))) if state.repos else 0

    return jsonify({
        "coma_index": coma_index,
        "mood_xray": moods,
        "tshirt_cost": tshirts,
        "ai_match_score": min(100, int(ai_potential_score)),
        "total_repos": len(state.repos)
    })

@app.route('/api/settings', methods=['POST'])
def update_settings():
    payload = request.get_json(silent=True) or {}
    capacity = int(payload.get('cache_size', state.repo_cache.capacity))
    state.repo_cache.set_capacity(capacity)
    return jsonify({
        "status": "success",
        "capacity": state.repo_cache.capacity,
        "ollama_url": payload.get('ollama_url'),
        "ollama_model": payload.get('ollama_model')
    })

@app.route('/api/ollama/models', methods=['GET'])
def get_ollama_models():
    url = request.args.get('url') or 'http://localhost:11434'
    return jsonify({"models": LLMService.list_available_models(url)})

def _chat_file_entry(filename):
    return {"name": filename, "size": os.path.getsize(os.path.join(app.config['UPLOAD_FOLDER'], filename)), "uploaded_at": datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}

@app.route('/api/chat_files', methods=['GET'])
def list_chat_files():
    return jsonify({"files": state.chat_files})

@app.route('/api/chat_files/<path:filename>', methods=['DELETE'])
def delete_chat_file(filename):
    safe_name = secure_filename(filename)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
    if os.path.exists(file_path):
        os.remove(file_path)
    state.chat_files = [f for f in state.chat_files if f["name"] != safe_name]
    state.conversations = [c for c in state.conversations if c.get("source_file") != safe_name]
    return jsonify({"status": "success", "removed": safe_name})

@app.route('/api/upload_chats', methods=['POST'])
def upload_chats():
    url, model = request.form.get("ollama_url"), request.form.get("ollama_model")
    uploaded_count = 0
    for file in request.files.getlist("file"):
        if not file or not file.filename:
            continue
        original_name = secure_filename(file.filename)
        unique_name = original_name
        counter = 1
        while os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], unique_name)):
            stem, ext = os.path.splitext(original_name)
            unique_name = f"{stem}_{counter}{ext}"
            counter += 1
        file_content = file.read()
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
        with open(file_path, 'wb') as uploaded_file:
            uploaded_file.write(file_content)
        parsed = ChatAnalyzer.parse_and_analyze(file_content.decode('utf-8', errors='ignore'), url, model)
        for chat in parsed:
            chat["source_file"] = unique_name
        state.conversations.extend(parsed)
        uploaded_count += len(parsed)
        state.chat_files = [f for f in state.chat_files if f["name"] != unique_name]
        state.chat_files.append(_chat_file_entry(unique_name))
    return jsonify({"status": "success", "count": uploaded_count, "files": state.chat_files})

@app.route('/api/github/scan', methods=['POST'])
def scan_github():
    data = request.json
    g = Github(data.get("token"))
    try:
        repos = g.get_user().get_repos(type="owner", sort="updated", direction="desc")[:8]
        state.repos = []
        analyzer = RepoAnalyzer(data.get("token"), data.get("ollama_url"), data.get("ollama_model"))
        for r in repos:
            meta = {"id": r.id, "full_name": r.full_name, "last_update": r.updated_at, "commits": [c.commit.message for c in r.get_commits()[:5]] if r.get_commits().totalCount > 0 else []}
            executor.submit(analyzer.analyze, meta)
        return jsonify({"status": "started"})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/api/time_travel/<repo_id>', methods=['POST'])
def time_travel(repo_id):
    token = request.json.get("token")
    prompt = TimeTravelService.generate_master_prompt(repo_id, token)
    return jsonify({"prompt": prompt})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
