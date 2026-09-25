import os
import json
import uuid
import datetime
import threading
import requests
import re
import io
import zipfile
import html
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, render_template, request, jsonify
from github import Github
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['OUTPUT_FOLDER'] = 'output'
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)
TAGS_CACHE_PATH = DATA_DIR / 'tags.json'
SCAN_CACHE_PATH = DATA_DIR / 'scan_cache.json'


class FIFOCache:
    def __init__(self, capacity=5):
        self.capacity = capacity
        self.cache = OrderedDict()

    def set_capacity(self, new_capacity):
        self.capacity = max(1, int(new_capacity))
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
    DEFAULT_TAGS = [
        {"id": "backend", "name": "backend", "color": "#22c55e"},
        {"id": "frontend", "name": "frontend", "color": "#38bdf8"},
        {"id": "debugging", "name": "debugging", "color": "#f59e0b"},
        {"id": "architecture", "name": "architecture", "color": "#a78bfa"},
        {"id": "database", "name": "database", "color": "#f472b6"},
        {"id": "devops", "name": "devops", "color": "#f87171"},
    ]

    def __init__(self):
        self.chat_tags = ["backend", "frontend", "debugging", "architecture", "database", "devops"]
        self.repo_tags = ["backend", "frontend", "abandoned", "react", "python", "data_science"]
        self.tags = []
        self.tag_assignments = {"repos": {}, "chats": {}}
        self.conversations = []
        self.chat_files = []
        self.repos = []
        self.stats = {"total_loc": 0, "abandoned_count": 0, "secrets_found": 0, "smells_found": 0}
        self.repo_cache = FIFOCache(capacity=5)
        self.lock = threading.RLock()
        self.scan_log = []
        self.scan_in_progress = False
        self.scan_started_at = None
        self.scan_finished_at = None
        self.scan_active_futures = set()
        self.settings = {
            "cache_size": 5,
            "description_word_budget": 18,
            "chunk_size": 5,
            "confidence": 70,
            "laya_backend": "auto",
            "ollama_url": "http://localhost:11434",
            "ollama_model": "llama3.1",
        }
        self.load_tag_cache()
        self.load_scan_cache()

    def load_scan_cache(self):
        with self.lock:
            if not SCAN_CACHE_PATH.exists():
                return
            try:
                payload = json.loads(SCAN_CACHE_PATH.read_text(encoding='utf-8'))
                if isinstance(payload, dict):
                    self.repos = payload.get('repos', []) or []
                    stats = payload.get('stats', {}) or {}
                    self.stats = {"total_loc": int(stats.get('total_loc', 0)), "abandoned_count": int(stats.get('abandoned_count', 0)), "secrets_found": int(stats.get('secrets_found', 0)), "smells_found": int(stats.get('smells_found', 0))}
                    self.scan_in_progress = bool(payload.get('scan_in_progress', False))
                    self.scan_started_at = datetime.datetime.fromisoformat(payload['scan_started_at']) if payload.get('scan_started_at') else None
                    self.scan_finished_at = datetime.datetime.fromisoformat(payload['scan_finished_at']) if payload.get('scan_finished_at') else None
            except Exception:
                self.repos = []
                self.stats = {"total_loc": 0, "abandoned_count": 0, "secrets_found": 0, "smells_found": 0}

    def save_scan_cache(self):
        with self.lock:
            safe_repos = []
            for repo in self.repos:
                record = dict(repo)
                record.pop('commits', None)
                safe_repos.append(self._json_safe(record))
            payload = {
                'repos': safe_repos,
                'stats': self._json_safe(self.stats),
                'scan_in_progress': self.scan_in_progress,
                'scan_started_at': self.scan_started_at.isoformat() if self.scan_started_at else None,
                'scan_finished_at': self.scan_finished_at.isoformat() if self.scan_finished_at else None,
            }
            SCAN_CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding='utf-8')

    def load_tag_cache(self):
        with self.lock:
            if TAGS_CACHE_PATH.exists():
                try:
                    payload = json.loads(TAGS_CACHE_PATH.read_text(encoding='utf-8'))
                    if isinstance(payload, dict):
                        self.tags = payload.get('tags') or self.DEFAULT_TAGS
                        self.tag_assignments = payload.get('assignments') or {"repos": {}, "chats": {}}
                        self.settings.update({
                            key: value for key, value in (payload.get('settings') or {}).items() if key in self.settings
                        })
                        self.repo_cache.set_capacity(int(self.settings.get('cache_size', 5)))
                        return
                except Exception:
                    pass
            self.tags = list(self.DEFAULT_TAGS)
            self.tag_assignments = {"repos": {}, "chats": {}}
            self.save_tag_cache()

    def save_tag_cache(self):
        with self.lock:
            payload = {
                "tags": self.tags,
                "assignments": self.tag_assignments,
                "settings": self.settings,
            }
            TAGS_CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding='utf-8')

    @staticmethod
    def _json_safe(value):
        if isinstance(value, dict):
            return {str(k): StateManager._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [StateManager._json_safe(v) for v in value]
        if hasattr(value, 'isoformat') and callable(value.isoformat):
            return value.isoformat()
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    def get_tag_names(self):
        return [str(tag.get('name', tag.get('id', ''))).strip() for tag in self.tags if tag.get('name') or tag.get('id')]

    def reset_scan(self):
        with self.lock:
            self.repos = []
            self.stats = {"total_loc": 0, "abandoned_count": 0, "secrets_found": 0, "smells_found": 0}
            self.scan_log = []
            self.scan_in_progress = True
            self.scan_started_at = datetime.datetime.now(datetime.timezone.utc)
            self.scan_finished_at = None
            self.scan_active_futures.clear()

    def track_future(self, future):
        with self.lock:
            self.scan_active_futures.add(future)

        def _on_done(done_future):
            with self.lock:
                self.scan_active_futures.discard(done_future)
                if not self.scan_active_futures:
                    self.scan_in_progress = False
                    self.scan_finished_at = datetime.datetime.now(datetime.timezone.utc)
                    self.log("[repo-sync] background scan finished")

        future.add_done_callback(_on_done)

    def log(self, message):
        print(message)
        with self.lock:
            self.scan_log.append({"time": datetime.datetime.now(datetime.timezone.utc).isoformat(), "message": message})
            if len(self.scan_log) > 200:
                self.scan_log = self.scan_log[-200:]


state = StateManager()
executor = ThreadPoolExecutor(max_workers=4)


class LLMService:
    @staticmethod
    def clamp_description(text, word_budget=None):
        if text is None:
            return ""
        cleaned = re.sub(r"\s+", " ", str(text)).strip()
        budget = int(word_budget if word_budget is not None else state.settings.get('description_word_budget', 18))
        if budget <= 0:
            return cleaned
        words = re.findall(r"\S+", cleaned)
        if not words:
            return ""
        return " ".join(words[:budget]).strip()

    @staticmethod
    def is_running_in_docker():
        return os.path.exists('/.dockerenv') or os.environ.get('DOCKER_CONTAINER') == 'true'

    @staticmethod
    def resolve_ollama_url(url=None):
        preferred = (url or '').strip()
        candidates = []
        if LLMService.is_running_in_docker():
            candidates.extend([
                'http://host.docker.internal:11434',
                'http://gateway.docker.internal:11434',
                'http://172.17.0.1:11434',
                'http://ollama:11434'
            ])
            if preferred:
                candidates.append(preferred)
        else:
            if preferred:
                candidates.append(preferred)
            candidates.extend([
                'http://localhost:11434',
                'http://127.0.0.1:11434'
            ])

        seen = set()
        for candidate in candidates:
            norm = candidate.rstrip('/')
            if norm in seen:
                continue
            seen.add(norm)
            try:
                resp = requests.get(f"{norm}/api/tags", timeout=3)
                if resp.status_code == 200:
                    return norm
            except Exception:
                continue

        if preferred:
            return preferred.rstrip('/')
        return 'http://host.docker.internal:11434' if LLMService.is_running_in_docker() else 'http://localhost:11434'

    @staticmethod
    def list_available_models(url):
        resolved_url = LLMService.resolve_ollama_url(url)
        try:
            res = requests.get(f"{resolved_url}/api/tags", timeout=15)
            if res.status_code != 200:
                return ['llama3.1']
            payload = res.json() or {}
            models = []
            for entry in payload.get('models', []):
                if isinstance(entry, dict) and entry.get('name'):
                    models.append(entry['name'])
            if models:
                return models
            for entry in payload.get('data', []):
                if isinstance(entry, dict) and entry.get('name'):
                    models.append(entry['name'])
            return models or ['llama3.1']
        except Exception:
            return ['llama3.1']

    @staticmethod
    def fallback_for_prompt(prompt):
        lowered = (prompt or '').lower()
        if 'mood' in lowered or 'feeling' in lowered:
            return 'DONE'
        if 'resurrection effort' in lowered or 'effort' in lowered:
            return 'M'
        if 'summary' in lowered:
            return 'No code extracted.'
        return 'Unavailable'

    @staticmethod
    def keyword_overlap_scores(text, labels):
        lowered = (text or '').lower()
        label_map = {}
        if not labels:
            labels = state.get_tag_names() or ['backend', 'frontend', 'debugging']
        for label in labels:
            name = str(label).lower().strip()
            if not name:
                continue
            score = 0.0
            if name in lowered:
                score = 0.9
            else:
                tokens = set(re.findall(r'[a-z0-9]+', name))
                if tokens:
                    matches = sum(1 for token in tokens if token in lowered)
                    if matches:
                        score = min(0.85, 0.2 + (matches / max(1, len(tokens)) * 0.8))
            label_map[name] = round(score, 3)
        return label_map

    @staticmethod
    def laya_scores_for_text(text, labels=None):
        labels = labels or state.get_tag_names() or ['backend', 'frontend', 'debugging']
        scores = LLMService.keyword_overlap_scores(text, labels)
        ordered = {key: round(score, 3) for key, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True) if score > 0}
        return ordered

    @staticmethod
    def ask(prompt, url, model):
        if not url or not model:
            fallback = LLMService.fallback_for_prompt(prompt)
            print(f'[llm-fallback] missing model config: {fallback}')
            return fallback
        try:
            response = requests.post(
                f'{url}/api/generate',
                json={'model': model, 'prompt': prompt, 'stream': False},
                timeout=45,
            )
            if response.status_code != 200:
                fallback = LLMService.fallback_for_prompt(prompt)
                print(f'[llm-fallback] non-200 response ({response.status_code}): {fallback}')
                return fallback
            payload = response.json() or {}
            result = str(payload.get('response', '')).strip()
            if not result:
                fallback = LLMService.fallback_for_prompt(prompt)
                print(f'[llm-fallback] empty response: {fallback}')
                return fallback
            return result
        except Exception:
            fallback = LLMService.fallback_for_prompt(prompt)
            print(f'[llm-fallback] offline/unavailable: {fallback}')
            return fallback


class LayaDecisionService:
    def __init__(self, backend='auto'):
        self.backend = backend or 'auto'

    def score(self, text, labels=None):
        labels = labels or state.get_tag_names() or ['backend', 'frontend', 'debugging']
        return LLMService.laya_scores_for_text(text, labels)


class ChatAnalyzer:
    @staticmethod
    def _clean_text(value):
        if not value:
            return ''
        text = html.unescape(str(value))
        text = re.sub(r'<[^>]+>', ' ', text, flags=re.DOTALL)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    @staticmethod
    def _extract_html_text(raw_html):
        if not raw_html:
            return []
        text = re.sub(r'(?is)<script.*?</script>', ' ', raw_html)
        text = re.sub(r'(?is)<style.*?</style>', ' ', text)
        blocks = []
        for pattern in [
            r'(?is)<(?:p|li|div|article|span|pre|h[1-6]|td|tr)\b[^>]*>(.*?)</(?:p|li|div|article|span|pre|h[1-6]|td|tr)>',
            r'(?is)<title\b[^>]*>(.*?)</title>',
            r'(?is)\b(?:prompt|response|user|assistant|message|content)\b[^<]*<.*?>(.*?)</.*?>'
        ]:
            for match in re.finditer(pattern, text):
                cleaned = ChatAnalyzer._clean_text(match.group(1))
                if cleaned:
                    blocks.append(cleaned)
        if not blocks:
            blocks = [ChatAnalyzer._clean_text(re.sub(r'<[^>]+>', ' ', text, flags=re.DOTALL))]
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
                for key in ('text', 'content', 'parts', 'value', 'message', 'response', 'prompt'):
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
            if 'mapping' in data:
                found.append({'title': data.get('title', 'Imported Chat'), 'source': 'ChatGPT', 'text_parts': ChatAnalyzer._extract_from_mapping(data.get('mapping'))})
            elif 'messages' in data:
                text_parts = []
                for msg in data.get('messages', []):
                    if isinstance(msg, dict):
                        if 'text' in msg and isinstance(msg['text'], str):
                            text_parts.append(msg['text'])
                        for key in ('content', 'parts', 'value'):
                            if key in msg:
                                value = msg[key]
                                if isinstance(value, str):
                                    text_parts.append(value)
                                elif isinstance(value, list):
                                    text_parts.extend([str(v) for v in value if isinstance(v, str)])
                if text_parts:
                    found.append({'title': data.get('title', 'Imported Chat'), 'source': 'Gemini', 'text_parts': text_parts})
            else:
                for key in ('title', 'text', 'content', 'prompt', 'response', 'value'):
                    if key in data and isinstance(data[key], str):
                        found.append({'title': data.get('title', 'Imported Chat'), 'source': 'Gemini', 'text_parts': [data[key]]})
                        break
                if not found:
                    if 'conversations' in data:
                        found.extend(ChatAnalyzer._extract_from_json(data['conversations']))
                    if 'chat' in data:
                        found.extend(ChatAnalyzer._extract_from_json(data['chat']))
        return found

    @staticmethod
    def parse_and_analyze(file_data, url, model):
        extracted = []
        raw_text = (file_data or '').strip()
        if not raw_text:
            return extracted

        try:
            payload = json.loads(raw_text)
            parsed_entries = ChatAnalyzer._extract_from_json(payload)
        except Exception:
            parsed_entries = []

        if not parsed_entries and ('<html' in raw_text.lower() or '<div' in raw_text.lower() or '<p' in raw_text.lower()):
            title = re.search(r'(?is)<title\b[^>]*>(.*?)</title>', raw_text)
            title_text = ChatAnalyzer._clean_text(title.group(1)) if title else 'Gemini Export'
            parsed_entries = [{'title': title_text, 'source': 'Gemini', 'text_parts': ChatAnalyzer._extract_html_text(raw_text)}]

        if not parsed_entries:
            parsed_entries = [{'title': 'Imported Chat', 'source': 'Gemini', 'text_parts': [raw_text[:4000]]}]

        for conv in parsed_entries:
            text_parts = conv.get('text_parts', [])
            if not text_parts:
                continue
            title = conv.get('title', 'Imported Chat')
            source = conv.get('source', 'Gemini')
            full_text = ' '.join(text_parts)
            last_messages = ' '.join(text_parts[-3:])
            prompt = f"Analyze the end of this dev chat. Did the user get a solution (SOLVED), did the AI fail giving bad context (CONTEXT_LOST), or did it just end abruptly (TIMEOUT)? Reply with ONE exact word.\nChat end: {last_messages[-1000:]}"
            closure = LLMService.ask(prompt, url, model)
            if closure not in ['SOLVED', 'CONTEXT_LOST', 'TIMEOUT']:
                closure = 'TIMEOUT'
            summary = LLMService.clamp_description(full_text, state.settings.get('description_word_budget', 18))
            tags = LayaDecisionService().score(full_text)
            if not tags:
                tags = {'backend': 0.8}
            extracted.append({
                'id': str(uuid.uuid4()),
                'title': title,
                'source': source,
                'text': full_text[:3000],
                'summary': summary,
                'closure_reason': closure,
                'tags': tags,
            })
        return extracted


class RepoAnalyzer:
    def __init__(self, token, ollama_url, ollama_model):
        self.headers = {'Authorization': f'token {token}'}
        self.ollama_url = ollama_url
        self.ollama_model = ollama_model

    def fetch_zip(self, repo_name):
        cached = state.repo_cache.get(repo_name)
        if cached:
            return cached
        resp = requests.get(f'https://api.github.com/repos/{repo_name}/zipball', headers=self.headers)
        if resp.status_code == 200:
            state.repo_cache.put(repo_name, resp.content)
            return resp.content
        return None

    @staticmethod
    def _detect_tech_stack(zip_bytes):
        stack = set()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            for info in z.infolist():
                if info.is_dir() or info.file_size > 200000:
                    continue
                name = info.filename.lower()
                if name.endswith('package.json'):
                    stack.add('nodejs')
                if name.endswith('requirements.txt'):
                    stack.add('python')
                if name.endswith('pyproject.toml') or name.endswith('poetry.lock'):
                    stack.add('python')
                if name.endswith('cargo.toml'):
                    stack.add('rust')
                if name.endswith('go.mod'):
                    stack.add('golang')
                if name.endswith('dockerfile'):
                    stack.add('docker')
                if name.endswith('prisma/schema.prisma'):
                    stack.add('prisma')
                if name.endswith('docker-compose.yml') or name.endswith('compose.yaml'):
                    stack.add('docker-compose')
                if 'react' in name:
                    stack.add('react')
                if 'flask' in name or 'fastapi' in name:
                    stack.add('api')
        return sorted(stack)

    def analyze(self, repo_data):
        name = repo_data['full_name']
        state.log(f'[repo-sync] starting {name}')
        zip_content = self.fetch_zip(name)
        if not zip_content:
            state.log(f'[repo-sync] skipped {name}: zip unavailable')
            return

        data = {'todos': [], 'secrets': [], 'env_vars': set(), 'db_schemas': set(), 'tech_stack': [], 'custom_lines': 0, 'smells': [], 'endpoints': []}
        snippets = []
        sec_pat = re.compile(r'(?i)(api_key|secret|password|token)\s*[:=]\s*[\'\"][a-zA-Z0-9_\-]{10,}[\'\"]')

        with zipfile.ZipFile(io.BytesIO(zip_content)) as z:
            for info in z.infolist():
                if info.is_dir() or info.file_size > 500000 or 'node_modules' in info.filename:
                    continue
                fname = info.filename
                lower_name = fname.lower()
                if lower_name.endswith(('package.json', 'requirements.txt', 'pyproject.toml', 'cargo.toml', 'go.mod', 'dockerfile', 'docker-compose.yml', 'compose.yaml')):
                    data['tech_stack'].extend(self._detect_tech_stack(zip_content))
                if lower_name.endswith(('.env', 'config.json', '.env.example')):
                    try:
                        content = z.read(info).decode('utf-8', errors='ignore')
                        for line in content.splitlines():
                            if '=' in line and not line.strip().startswith('#'):
                                key = line.split('=', 1)[0].strip()
                                if key:
                                    data['env_vars'].add(key)
                    except Exception:
                        pass
                if fname.endswith(('.py', '.js', '.ts', '.rs', '.go', '.java')):
                    try:
                        content = z.read(info).decode('utf-8', errors='ignore')
                        lines = content.split('\n')
                        data['custom_lines'] += len(lines)
                        if len(lines) > 1000:
                            data['smells'].append(f'God File: {fname.split("/")[-1]}')
                        for i, line in enumerate(lines):
                            if 'TODO:' in line or 'FIXME:' in line:
                                data['todos'].append(line.strip()[:60])
                            if sec_pat.search(line):
                                data['secrets'].append(fname.split('/')[-1])
                            if re.search(r'@app\.route|router\.(get|post|put|delete)|app\.route|app\.(get|post|put|delete)', line):
                                data['endpoints'].append(line.strip())
                            if 'console.log' in line or 'print(' in line or 'var_dump' in line:
                                data['smells'].append(f'Console log: {fname.split("/")[-1]}:{i + 1}')
                            if 'prisma' in line.lower() or 'sqlalchemy' in line.lower() or 'mongoose' in line.lower():
                                data['db_schemas'].add(fname.split('/')[-1])
                        if len(snippets) < 2 and len(lines) > 15:
                            snippets.append('\n'.join(lines[:40]))
                    except Exception:
                        pass

        commits = '\n'.join(repo_data.get('commits', []))
        mood = LLMService.ask(f"Analyze the developer's mood from these commits. Are they FRUSTRATED, BORED, or DONE? Reply one word:\n{commits}", self.ollama_url, self.ollama_model)
        tshirt = LLMService.ask(f"Estimate resurrection effort (S, M, L, XL) based on {data['custom_lines']} lines of code and {len(data['todos'])} TODOs. Reply with one letter/word only.", self.ollama_url, self.ollama_model)
        desc = LLMService.ask(f"Write a summary of at most {state.settings.get('description_word_budget', 18)} words of this code:\n{' '.join(snippets)}", self.ollama_url, self.ollama_model) if snippets else 'No code extracted.'
        desc = LLMService.clamp_description(desc, state.settings.get('description_word_budget', 18))

        days_abandoned = (datetime.datetime.now(datetime.timezone.utc) - repo_data['last_update']).days if repo_data.get('last_update') else 0
        abandon_score = min(100, max(0, int((days_abandoned / 365) * 60 + (len(data['todos']) * 5) + (len(data['smells']) * 2))))
        tags = LayaDecisionService().score(' '.join(snippets) or name)

        with state.lock:
            state.stats['total_loc'] += data['custom_lines']
            state.stats['secrets_found'] += len(data['secrets'])
            state.stats['smells_found'] += len(data['smells'])
            if abandon_score > 70:
                state.stats['abandoned_count'] += 1

            repo_match = next((repo for repo in state.repos if str(repo.get('id')) == str(repo_data['id'])), None)
            if repo_match is None:
                repo_match = {'id': repo_data['id'], 'name': name, 'full_name': name, 'status': 'ready'}
                state.repos.append(repo_match)

            repo_match.update({
                'id': repo_data['id'],
                'name': name,
                'full_name': name,
                'description': desc,
                'mood': mood,
                'tshirt': tshirt,
                'abandon_score': abandon_score,
                'days_abandoned': days_abandoned,
                'smells': data['smells'],
                'todos': data['todos'],
                'secrets': len(data['secrets']),
                'tech_stack': sorted(set(data['tech_stack'] + list(self._detect_tech_stack(zip_content)))),
                'env_vars': sorted(data['env_vars']),
                'db_schemas': sorted(data['db_schemas']),
                'endpoints': data['endpoints'],
                'updated_at': repo_data.get('last_update'),
                'status': 'ready',
                'tags': tags,
            })
            state.repos.sort(key=lambda r: (r.get('updated_at') or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), str(r.get('name', ''))), reverse=True)
            state.save_scan_cache()

        state.log(f"[repo-sync] complete {name}: loc={data['custom_lines']}, mood={mood}, effort={tshirt}, abandoned_days={days_abandoned}")


class TimeTravelService:
    @staticmethod
    def best_chat_for_repo(repo):
        best = None
        best_score = -1
        repo_tags = set((repo.get('tags') or {}).keys())
        for chat in state.conversations:
            chat_tags = set((chat.get('tags') or {}).keys())
            score = len(repo_tags & chat_tags)
            if score > best_score:
                best = chat
                best_score = score
        return best

    @staticmethod
    def generate_master_prompt(repo_id, token):
        repo = next((r for r in state.repos if str(r['id']) == str(repo_id)), None)
        if not repo:
            return 'Repo not found in state.'
        best_chat = TimeTravelService.best_chat_for_repo(repo)

        zip_content = state.repo_cache.get(repo['name'])
        if not zip_content:
            resp = requests.get(f"https://api.github.com/repos/{repo['name']}/zipball", headers={'Authorization': f'token {token}'})
            if resp.status_code == 200:
                zip_content = resp.content
                state.repo_cache.put(repo['name'], zip_content)

        readme_content = 'No README found.'
        main_code = ''
        if zip_content:
            with zipfile.ZipFile(io.BytesIO(zip_content)) as z:
                for info in z.infolist():
                    if 'README.md' in info.filename.upper():
                        readme_content = z.read(info).decode('utf-8', errors='ignore')[:1000]
                    if info.filename.endswith(('main.py', 'index.js', 'app.py', 'App.js')) and not main_code:
                        main_code = z.read(info).decode('utf-8', errors='ignore')[:1000]

        chat_context = f"We were discussing: {best_chat['text'][-1000:]}\nThe AI conversation ended because: {best_chat['closure_reason']}" if best_chat else 'No prior AI conversations linked.'
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
{chr(10).join(['- ' + t for t in repo.get('todos', [])])}

## 4. Your Mission
Based on the code state and the past AI conversation, please provide:
1. A brief summary of where we left off.
2. The exact, specific next step (a single task) I need to code right now to get back into the flow. Do not give me a massive list, just the next logical block.
"""
        return prompt


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/stats', methods=['GET'])
def get_stats():
    with state.lock:
        payload = dict(state.stats)
        payload['scan_in_progress'] = state.scan_in_progress
        payload['scan_started_at'] = state.scan_started_at.isoformat() if state.scan_started_at else None
        payload['scan_finished_at'] = state.scan_finished_at.isoformat() if state.scan_finished_at else None
        return jsonify(payload)


@app.route('/api/repos', methods=['GET'])
def get_repos():
    with state.lock:
        ordered = sorted(state.repos, key=lambda r: (r.get('updated_at') or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), str(r.get('name', ''))), reverse=True)
        return jsonify({
            'repos': ordered,
            'scan_in_progress': state.scan_in_progress,
            'total_count': len(ordered),
            'analyzed_count': sum(1 for r in ordered if r.get('status') == 'ready'),
        })


@app.route('/api/analytics', methods=['GET'])
def get_analytics():
    coma_index = {'Fresh': 0, 'Decomposing': 0, 'Skeleton': 0, 'Fossil': 0}
    moods = {'Frustrated': 0, 'Bored': 0, 'Done': 0, 'Other': 0}
    tshirts = {'S': 0, 'M': 0, 'L': 0, 'XL': 0}
    overlap_total = 0

    for r in state.repos:
        days = r.get('days_abandoned', 0)
        if days < 30:
            coma_index['Fresh'] += 1
        elif days < 180:
            coma_index['Decomposing'] += 1
        elif days < 365:
            coma_index['Skeleton'] += 1
        else:
            coma_index['Fossil'] += 1

        m = str(r.get('mood', '')).upper()
        if 'FRUSTRAT' in m:
            moods['Frustrated'] += 1
        elif 'BORED' in m:
            moods['Bored'] += 1
        elif 'DONE' in m:
            moods['Done'] += 1
        else:
            moods['Other'] += 1

        ts = str(r.get('tshirt', '')).upper()
        if ts in tshirts:
            tshirts[ts] += 1

        repo_tags = set(str(k).lower() for k in (r.get('tags') or {}).keys())
        if repo_tags:
            for chat in state.conversations:
                chat_tags = set(str(k).lower() for k in (chat.get('tags') or {}).keys())
                overlap_total += len(repo_tags & chat_tags)

    repo_count = max(1, len(state.repos))
    ai_potential_score = min(100, int((overlap_total / repo_count) * 25)) if state.repos else 0

    return jsonify({
        'coma_index': coma_index,
        'mood_xray': moods,
        'tshirt_cost': tshirts,
        'ai_match_score': ai_potential_score,
        'total_repos': len(state.repos),
    })


@app.route('/api/settings', methods=['POST'])
def update_settings():
    payload = request.get_json(silent=True) or {}
    if 'cache_size' in payload:
        state.settings['cache_size'] = max(5, min(20, int(payload.get('cache_size', state.settings['cache_size']))))
        state.repo_cache.set_capacity(state.settings['cache_size'])
    if 'chunk_size' in payload:
        state.settings['chunk_size'] = max(1, int(payload.get('chunk_size', state.settings['chunk_size'])))
    if 'description_word_budget' in payload:
        state.settings['description_word_budget'] = max(8, min(40, int(payload.get('description_word_budget', state.settings['description_word_budget']))))
    if 'confidence' in payload:
        state.settings['confidence'] = max(0, min(100, int(payload.get('confidence', state.settings['confidence']))))
    if 'laya_backend' in payload:
        state.settings['laya_backend'] = str(payload.get('laya_backend', state.settings['laya_backend']))
    if 'ollama_url' in payload:
        state.settings['ollama_url'] = str(payload.get('ollama_url', state.settings['ollama_url']))
    if 'ollama_model' in payload:
        state.settings['ollama_model'] = str(payload.get('ollama_model', state.settings['ollama_model']))
    state.save_tag_cache()
    return jsonify({'status': 'success', 'settings': state.settings})


@app.route('/api/ollama/resolve-url', methods=['GET'])
def resolve_ollama_url():
    return jsonify({'url': LLMService.resolve_ollama_url(request.args.get('url'))})


@app.route('/api/ollama/models', methods=['GET'])
def get_ollama_models():
    url = request.args.get('url')
    return jsonify({'models': LLMService.list_available_models(url)})


@app.route('/api/chat_files', methods=['GET'])
def list_chat_files():
    return jsonify({'files': state.chat_files})


@app.route('/api/chat_files/<path:filename>', methods=['DELETE'])
def delete_chat_file(filename):
    safe_name = secure_filename(filename)
    state.chat_files = [f for f in state.chat_files if f['name'] != safe_name]
    state.conversations = [c for c in state.conversations if c.get('source_file') != safe_name]
    return jsonify({'status': 'success', 'removed': safe_name})


@app.route('/api/upload_chats', methods=['POST'])
def upload_chats():
    url, model = request.form.get('ollama_url'), request.form.get('ollama_model')
    uploaded_count = 0
    for file in request.files.getlist('file'):
        if not file or not file.filename:
            continue
        safe_name = secure_filename(file.filename)
        file_content = file.read()
        parsed = ChatAnalyzer.parse_and_analyze(file_content.decode('utf-8', errors='ignore'), url, model)
        for chat in parsed:
            chat['source_file'] = safe_name
        state.conversations.extend(parsed)
        uploaded_count += len(parsed)
        state.chat_files = [f for f in state.chat_files if f['name'] != safe_name]
        state.chat_files.append({'name': safe_name, 'size': len(file_content), 'uploaded_at': datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')})
    return jsonify({'status': 'success', 'count': uploaded_count, 'files': state.chat_files})


@app.route('/api/github/scan', methods=['POST'])
def scan_github():
    payload = request.get_json(silent=True) or {}
    token = payload.get('token')
    if not token:
        return jsonify({'error': 'GitHub token is required.'}), 400

    try:
        g = Github(token)
        user = g.get_user()
        repos = list(user.get_repos(type='all', sort='updated', direction='desc'))
        ordered = sorted(repos, key=lambda r: (r.updated_at or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), r.full_name.lower()), reverse=True)

        state.reset_scan()
        state.log(f'[repo-sync] discovered {len(ordered)} repos via GitHub token; beginning in-memory sync')

        analyzer = RepoAnalyzer(token, payload.get('ollama_url'), payload.get('ollama_model'))
        for r in ordered:
            try:
                commit_messages = []
                for commit in list(r.get_commits()[:5]):
                    message = getattr(getattr(commit, 'commit', None), 'message', None)
                    if message:
                        commit_messages.append(message)
            except Exception as exc:
                state.log(f'[repo-sync] commit fetch failed for {r.full_name}: {exc}')
                commit_messages = []

            raw_name = getattr(r, 'name', None)
            repo_name = raw_name if isinstance(raw_name, str) and raw_name.strip() else r.full_name
            raw_description = getattr(r, 'description', None)
            repo_description = raw_description if isinstance(raw_description, str) else ''
            profile = {
                'id': r.id,
                'full_name': r.full_name,
                'name': repo_name,
                'description': repo_description or 'GitHub repository',
                'last_update': r.updated_at,
                'updated_at': r.updated_at,
                'commits': commit_messages,
                'size': getattr(r, 'size', 0),
                'status': 'pending',
                'tags': {},
            }
            state.repos.append(profile)
            state.save_scan_cache()
            future = executor.submit(analyzer.analyze, profile)
            state.track_future(future)

        return jsonify({
            'status': 'started',
            'scan_in_progress': True,
            'repo_count': len(state.repos),
            'repos': [
                {
                    'id': r.get('id'),
                    'full_name': r.get('full_name'),
                    'name': r.get('name'),
                    'description': r.get('description'),
                    'updated_at': r.get('updated_at').isoformat() if r.get('updated_at') else None,
                    'status': r.get('status', 'pending'),
                    'size': r.get('size', 0),
                }
                for r in state.repos
            ],
        })
    except Exception as e:
        state.log(f'[repo-sync] fatal error: {e}')
        return jsonify({'error': str(e), 'status': 'error'}), 500


@app.route('/api/tags', methods=['GET', 'POST'])
def tags_collection():
    if request.method == 'GET':
        query = request.args.get('q', '').strip().lower()
        tags = list(state.tags)
        if query:
            tags = [tag for tag in tags if query in str(tag.get('name', '')).lower()]
        return jsonify({'tags': tags})

    payload = request.get_json(silent=True) or {}
    name = str(payload.get('name', '')).strip()
    if not name:
        return jsonify({'error': 'Tag name is required.'}), 400
    tag_id = str(payload.get('id') or re.sub(r'\s+', '-', name.lower()))
    tag = {'id': tag_id, 'name': name, 'color': payload.get('color', '#60a5fa')}
    state.tags = [t for t in state.tags if t.get('id') != tag_id]
    state.tags.append(tag)
    state.save_tag_cache()
    return jsonify({'tag': tag, 'tags': state.tags})


@app.route('/api/tags/<tag_id>', methods=['PUT', 'DELETE'])
def tag_detail(tag_id):
    if request.method == 'DELETE':
        state.tags = [tag for tag in state.tags if str(tag.get('id')) != str(tag_id)]
        state.save_tag_cache()
        return jsonify({'status': 'success', 'deleted': tag_id})

    payload = request.get_json(silent=True) or {}
    for tag in state.tags:
        if str(tag.get('id')) == str(tag_id):
            tag['name'] = str(payload.get('name', tag.get('name'))).strip() or tag.get('name')
            tag['color'] = payload.get('color', tag.get('color'))
            state.save_tag_cache()
            return jsonify({'tag': tag})
    return jsonify({'error': 'Tag not found.'}), 404


@app.route('/api/time_travel/<repo_id>', methods=['POST'])
def time_travel(repo_id):
    token = (request.get_json(silent=True) or {}).get('token')
    prompt = TimeTravelService.generate_master_prompt(repo_id, token)
    return jsonify({'prompt': prompt})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
