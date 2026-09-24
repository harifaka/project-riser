import os
import json
import uuid
import datetime
import requests
import re
import io
import zipfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, jsonify, send_file
from github import Github

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['OUTPUT_FOLDER'] = 'output'
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

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
        self.repos = []
        self.stats = {"total_loc": 0, "abandoned_count": 0, "secrets_found": 0, "smells_found": 0}
        self.repo_cache = FIFOCache(capacity=5)

state = StateManager()
executor = ThreadPoolExecutor(max_workers=4)

class LLMService:
    @staticmethod
    def ask(prompt, url, model):
        if not url or not model: return "LLM not configured."
        try:
            res = requests.post(f"{url}/api/generate", json={"model": model, "prompt": prompt, "stream": False}, timeout=45).json()
            return res.get("response", "").strip()
        except: return "LLM Timeout or Error."

class ChatAnalyzer:
    @staticmethod
    def parse_and_analyze(file_data, url, model):
        extracted = []
        try:
            data = json.loads(file_data)
            if isinstance(data, list):
                for conv in data:
                    text_parts = []
                    title = conv.get("title", "Imported Chat")
                    source = "ChatGPT" if "mapping" in conv else "Gemini"
                    if source == "ChatGPT":
                        for node in conv.get("mapping", {}).values():
                            msg = node.get("message")
                            if msg and msg.get("content") and msg["content"].get("parts"):
                                text_parts.extend([str(p) for p in msg["content"]["parts"] if isinstance(p, str)])
                    else:
                        for msg in conv.get("messages", []):
                            if "text" in msg: text_parts.append(msg["text"])
                    if text_parts:
                        full_text = " ".join(text_parts)
                        last_messages = " ".join(text_parts[-3:])
                        prompt = f"Analyze the end of this dev chat. Did the user get a solution (SOLVED), did the AI fail giving bad context (CONTEXT_LOST), or did it just end abruptly (TIMEOUT)? Reply with ONE exact word.\nChat end: {last_messages[-1000:]}"
                        closure = LLMService.ask(prompt, url, model)
                        if closure not in ["SOLVED", "CONTEXT_LOST", "TIMEOUT"]: closure = "TIMEOUT"
                        extracted.append({
                            "id": str(uuid.uuid4()), "title": title, "source": source,
                            "text": full_text[:3000], "closure_reason": closure, "tags": {"backend": 0.8}
                        })
        except Exception as e: print(f"Chat parse error: {e}")
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
    capacity = int(request.json.get('cache_size', 5))
    state.repo_cache.set_capacity(capacity)
    return jsonify({"status": "success", "capacity": state.repo_cache.capacity})

@app.route('/api/upload_chats', methods=['POST'])
def upload_chats():
    url, model = request.form.get("ollama_url"), request.form.get("ollama_model")
    for file in request.files.getlist("file"):
        state.conversations.extend(ChatAnalyzer.parse_and_analyze(file.read().decode('utf-8', errors='ignore'), url, model))
    return jsonify({"status": "success", "count": len(state.conversations)})

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
