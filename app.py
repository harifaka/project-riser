import os
import json
import uuid
import datetime
import hashlib
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


class ConcurrencyGate:
    def __init__(self):
        self._cond = threading.Condition()
        self._active = 0

    def __enter__(self):
        with self._cond:
            limit = max(1, int(state.settings.get('chunk_size', 5))) if 'state' in globals() else 5
            while self._active >= limit:
                self._cond.wait()
            self._active += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        with self._cond:
            self._active = max(0, self._active - 1)
            self._cond.notify()
        return False


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
        self.scan_generation = 0
        self.chat_retag_generation = 0
        self.chat_retag_in_progress = False
        self.chat_retag_done = 0
        self.chat_retag_total = 0
        self.link_generation = 0
        self.link_in_progress = False
        self.link_done = 0
        self.link_total = 0
        self._dirty_writes = 0
        self._save_timer = None
        self.work_gate = ConcurrencyGate()
        self._laya_backend_logged = None
        self.settings = {
            "cache_size": 5,
            "description_word_budget": 18,
            "chunk_size": 5,
            "confidence": 70,
            "laya_backend": "auto",
            "laya_model": "facebook/bart-large-mnli",
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
                    self.scan_in_progress = False
                    self.scan_started_at = datetime.datetime.fromisoformat(payload['scan_started_at']) if payload.get('scan_started_at') else None
                    self.scan_finished_at = datetime.datetime.fromisoformat(payload['scan_finished_at']) if payload.get('scan_finished_at') else None
                    self.recompute_stats()
            except Exception:
                self.repos = []
                self.stats = {"total_loc": 0, "abandoned_count": 0, "secrets_found": 0, "smells_found": 0}

    def _scan_payload(self):
        safe_repos = []
        for repo in self.repos:
            record = dict(repo)
            record.pop('commits', None)
            safe_repos.append(self._json_safe(record))
        return {
            'repos': safe_repos,
            'stats': self._json_safe(self.stats),
            'scan_in_progress': self.scan_in_progress,
            'scan_started_at': self.scan_started_at.isoformat() if self.scan_started_at else None,
            'scan_finished_at': self.scan_finished_at.isoformat() if self.scan_finished_at else None,
        }

    def save_scan_cache(self):
        self.flush_scan_cache()

    def flush_scan_cache(self):
        with self.lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None
            self._dirty_writes = 0
            payload = self._scan_payload()
        SCAN_CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding='utf-8')

    def note_scan_dirty(self):
        with self.lock:
            self._dirty_writes += 1
            if self._dirty_writes >= 5:
                self._dirty_writes = 0
                if self._save_timer is not None:
                    self._save_timer.cancel()
                    self._save_timer = None
                return True
            if self._save_timer is None:
                timer = threading.Timer(2.0, self.flush_scan_cache)
                timer.daemon = True
                self._save_timer = timer
                timer.start()
            return False

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

    def tag_fingerprint(self):
        names = sorted(name.lower() for name in self.get_tag_names())
        return hashlib.sha256('|'.join(names).encode('utf-8')).hexdigest()[:16]

    def recompute_stats(self):
        loc = 0
        abandoned = 0
        secrets = 0
        smells = 0
        for repo in self.repos:
            if repo.get('custom_lines') is None and repo.get('status') != 'ready':
                continue
            loc += int(repo.get('custom_lines') or 0)
            if repo.get('abandon_score') is not None and int(repo.get('abandon_score') or 0) > 70:
                abandoned += 1
            secrets += int(repo.get('secrets') or 0)
            smells += len(repo.get('smells') or [])
        self.stats = {
            'total_loc': loc,
            'abandoned_count': abandoned,
            'secrets_found': secrets,
            'smells_found': smells,
        }

    def start_scan_generation(self):
        with self.lock:
            self.scan_generation += 1
            self.scan_log = []
            self.scan_in_progress = True
            self.scan_started_at = datetime.datetime.now(datetime.timezone.utc)
            self.scan_finished_at = None
            self.scan_active_futures.clear()
            return self.scan_generation

    def is_current_scan(self, generation):
        with self.lock:
            return generation == self.scan_generation

    def is_current_link(self, generation):
        with self.lock:
            return generation == self.link_generation

    def reset_scan(self):
        self.start_scan_generation()

    def track_future(self, future, generation=None):
        with self.lock:
            if generation is not None and generation != self.scan_generation:
                return
            self.scan_active_futures.add(future)

        def _on_done(done_future):
            finish = False
            with self.lock:
                self.scan_active_futures.discard(done_future)
                if generation is not None and generation != self.scan_generation:
                    return
                if not self.scan_active_futures:
                    self.scan_in_progress = False
                    self.scan_finished_at = datetime.datetime.now(datetime.timezone.utc)
                    self.recompute_stats()
                    finish = True
            if finish:
                self.log('[repo-sync] background scan finished')
                self.flush_scan_cache()

        future.add_done_callback(_on_done)

    def log(self, message):
        print(message)
        with self.lock:
            self.scan_log.append({"time": datetime.datetime.now(datetime.timezone.utc).isoformat(), "message": message})
            if len(self.scan_log) > 200:
                self.scan_log = self.scan_log[-200:]


def coerce_datetime(value):
    if value is None or value == '':
        return None
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=datetime.timezone.utc)
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed
    return None


def content_fingerprint(repo_id, pushed_at, size):
    stamp = ''
    moment = coerce_datetime(pushed_at)
    if moment is not None:
        stamp = moment.isoformat()
    raw = f'{repo_id}|{stamp}|{size}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def pushed_at_of(repo):
    pushed = getattr(repo, 'pushed_at', None)
    if isinstance(pushed, datetime.datetime):
        return pushed
    updated = getattr(repo, 'updated_at', None)
    if isinstance(updated, datetime.datetime):
        return updated
    return None


def repo_sort_key(repo):
    updated = coerce_datetime(repo.get('updated_at') or repo.get('last_update')) or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    return (updated, str(repo.get('name') or ''))


def public_repo(repo):
    record = StateManager._json_safe(dict(repo))
    record.pop('commits', None)
    record['manual_tags'] = list(state.tag_assignments.get('repos', {}).get(str(repo.get('id')), []) or [])
    record['linked_chats'] = ChatLinker.public_links(repo)
    record.pop('rejected_chat_fingerprints', None)
    return record


def scan_status_payload():
    with state.lock:
        ordered = sorted(state.repos, key=repo_sort_key, reverse=True)
        total = len(ordered)
        analyzed = sum(1 for repo in ordered if repo.get('status') in ('ready', 'error'))
        cached = sum(1 for repo in ordered if repo.get('cache_hit') and repo.get('status') == 'ready')
        return {
            'status': 'started' if state.scan_in_progress else 'idle',
            'scan_in_progress': state.scan_in_progress,
            'repo_count': total,
            'total_count': total,
            'analyzed_count': analyzed,
            'cached_count': cached,
            'link_in_progress': state.link_in_progress,
            'link_done': state.link_done,
            'link_total': state.link_total,
            'repos': [public_repo(repo) for repo in ordered],
            'scan_log': list(state.scan_log[-40:]),
        }


def confidence_ratio():
    return max(0.0, min(1.0, float(state.settings.get('confidence', 70)) / 100.0))


def chat_matches_tag(chat, tag):
    manual = set(state.tag_assignments.get('chats', {}).get(str(chat.get('id')), []) or [])
    if tag.get('id') in manual or tag.get('name') in manual:
        return True
    scores = chat.get('tags') or {}
    score = scores.get(tag.get('name'), scores.get(tag.get('id'), 0))
    try:
        return float(score) >= confidence_ratio()
    except (TypeError, ValueError):
        return False


def chat_overview_payload():
    with state.lock:
        threshold = confidence_ratio()
        day_counts = {}
        dated = 0
        sources = {}
        closures = {}
        tag_counts = []
        items = []
        for chat in state.conversations:
            source = chat.get('source') or 'Unknown'
            sources[source] = sources.get(source, 0) + 1
            closure = chat.get('closure_reason') or 'TIMEOUT'
            closures[closure] = closures.get(closure, 0) + 1
            day = chat.get('created_on')
            if day:
                dated += 1
                day_counts[day] = day_counts.get(day, 0) + 1
            visible = [tag.get('name') for tag in state.tags if chat_matches_tag(chat, tag)]
            items.append({
                'id': chat.get('id'),
                'title': chat.get('title'),
                'source': source,
                'created_on': day,
                'summary': chat.get('summary'),
                'closure_reason': closure,
                'tags': visible,
                'manual_tags': list(state.tag_assignments.get('chats', {}).get(str(chat.get('id')), []) or []),
            })
        for tag in state.tags:
            tag_counts.append({
                'id': tag.get('id'),
                'name': tag.get('name'),
                'color': tag.get('color'),
                'count': sum(1 for chat in state.conversations if chat_matches_tag(chat, tag)),
            })
        heatmap = []
        if day_counts:
            start = datetime.date.fromisoformat(min(day_counts))
            end = datetime.date.fromisoformat(max(day_counts))
            cursor = start
            while cursor <= end:
                key = cursor.isoformat()
                heatmap.append({'date': key, 'count': day_counts.get(key, 0)})
                cursor += datetime.timedelta(days=1)
        total = len(state.conversations)
        done = state.chat_retag_done
        return {
            'total': total,
            'dated': dated,
            'undated': total - dated,
            'sources': sources,
            'closures': closures,
            'heatmap': heatmap,
            'tag_counts': tag_counts,
            'chats': items,
            'confidence': state.settings.get('confidence', 70),
            'threshold': threshold,
            'retag_in_progress': state.chat_retag_in_progress,
            'retag_done': done,
            'retag_total': state.chat_retag_total,
        }


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
    _pipeline = None
    _pipeline_model = None
    _pipeline_lock = threading.Lock()

    def __init__(self, backend=None):
        self.backend = backend

    def score(self, text, labels=None):
        labels = labels or state.get_tag_names() or ['backend', 'frontend', 'debugging']
        labels = [str(label).strip() for label in labels if str(label).strip()]
        if not labels:
            return {}
        chosen = self._resolve_backend()
        if state._laya_backend_logged != chosen:
            state._laya_backend_logged = chosen
            state.log(f'[laya] using {chosen}')
        if chosen == 'transformers':
            scored = self._score_transformers(text, labels)
            if scored is not None:
                return scored
            if (self.backend or state.settings.get('laya_backend') or 'auto') != 'transformers':
                scored = self._score_ollama(text, labels)
                if scored is not None:
                    return scored
        elif chosen == 'ollama':
            scored = self._score_ollama(text, labels)
            if scored is not None:
                return scored
        return LLMService.laya_scores_for_text(text, labels)

    def _resolve_backend(self):
        backend = (self.backend or state.settings.get('laya_backend') or 'auto').strip().lower()
        if backend == 'keyword':
            return 'keyword'
        if backend == 'ollama':
            return 'ollama'
        if backend == 'transformers':
            return 'transformers'
        if self._transformers_available():
            return 'transformers'
        if state.settings.get('ollama_url') and state.settings.get('ollama_model'):
            return 'ollama'
        return 'keyword'

    @classmethod
    def _transformers_available(cls):
        try:
            import transformers  # noqa: F401
            return True
        except Exception:
            return False

    @classmethod
    def _score_transformers(cls, text, labels):
        model_id = state.settings.get('laya_model') or 'facebook/bart-large-mnli'
        try:
            with cls._pipeline_lock:
                if cls._pipeline is None or cls._pipeline_model != model_id:
                    from transformers import pipeline
                    cls._pipeline = pipeline('zero-shot-classification', model=model_id)
                    cls._pipeline_model = model_id
                classifier = cls._pipeline
            result = classifier((text or '')[:2000], candidate_labels=labels, multi_label=True)
            names = result.get('labels') or []
            scores = result.get('scores') or []
            ordered = {str(name): round(float(score), 3) for name, score in zip(names, scores) if float(score) > 0}
            return ordered
        except Exception as exc:
            state.log(f'[laya] transformers unavailable: {exc}')
            cls._pipeline = None
            cls._pipeline_model = None
            return None

    @staticmethod
    def _score_ollama(text, labels):
        label_list = ', '.join(labels)
        prompt = (
            'Score each label from 0 to 1 for how well it fits the text. '
            'Reply with a JSON object only, keys exactly the labels, values numbers.\n'
            f'Labels: {label_list}\nText:\n{(text or "")[:2000]}'
        )
        raw = LLMService.ask(prompt, state.settings.get('ollama_url'), state.settings.get('ollama_model'))
        if not raw or raw in ('Unavailable', 'No code extracted.', 'DONE', 'M'):
            return None
        match = re.search(r'\{.*\}', raw, flags=re.DOTALL)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        scored = {}
        for label in labels:
            value = payload.get(label, payload.get(label.lower()))
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                scored[label] = round(min(1.0, max(0.0, number)), 3)
        return scored or None


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
    def _coerce_day(value):
        if value is None or value == '':
            return None
        if isinstance(value, (int, float)):
            try:
                return datetime.datetime.fromtimestamp(float(value), datetime.timezone.utc).date().isoformat()
            except (OverflowError, OSError, ValueError):
                return None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if re.fullmatch(r'\d+(\.\d+)?', text):
                return ChatAnalyzer._coerce_day(float(text))
            try:
                parsed = datetime.datetime.fromisoformat(text.replace('Z', '+00:00'))
                return parsed.date().isoformat()
            except ValueError:
                match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
                return match.group(1) if match else None
        return None

    @staticmethod
    def _extract_from_json(data):
        found = []
        if isinstance(data, list):
            for item in data:
                found.extend(ChatAnalyzer._extract_from_json(item))
        elif isinstance(data, dict):
            if 'mapping' in data:
                found.append({
                    'title': data.get('title', 'Imported Chat'),
                    'source': 'ChatGPT',
                    'text_parts': ChatAnalyzer._extract_from_mapping(data.get('mapping')),
                    'created_on': ChatAnalyzer._coerce_day(data.get('create_time') or data.get('update_time')),
                })
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
                    found.append({
                        'title': data.get('title', 'Imported Chat'),
                        'source': 'Gemini',
                        'text_parts': text_parts,
                        'created_on': ChatAnalyzer._coerce_day(data.get('update_time') or data.get('updateTime') or data.get('timestamp') or data.get('create_time')),
                    })
            else:
                for key in ('title', 'text', 'content', 'prompt', 'response', 'value'):
                    if key in data and isinstance(data[key], str):
                        found.append({
                            'title': data.get('title', 'Imported Chat'),
                            'source': 'Gemini',
                            'text_parts': [data[key]],
                            'created_on': ChatAnalyzer._coerce_day(data.get('update_time') or data.get('updateTime') or data.get('timestamp') or data.get('create_time')),
                        })
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
            parsed_entries = [{'title': title_text, 'source': 'Gemini', 'text_parts': ChatAnalyzer._extract_html_text(raw_text), 'created_on': None}]

        if not parsed_entries:
            parsed_entries = [{'title': 'Imported Chat', 'source': 'Gemini', 'text_parts': [raw_text[:4000]], 'created_on': None}]

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
            extracted.append({
                'id': str(uuid.uuid4()),
                'title': title,
                'source': source,
                'text': full_text[:3000],
                'summary': summary,
                'closure_reason': closure,
                'created_on': conv.get('created_on'),
                'tags': tags,
            })
        return extracted


class RepoAnalyzer:
    def __init__(self, token, ollama_url, ollama_model):
        self.token = token
        self.headers = {'Authorization': f'token {token}'}
        self.ollama_url = ollama_url
        self.ollama_model = ollama_model

    def _recent_commits(self, repo_name):
        try:
            repo = Github(self.token).get_repo(repo_name)
            messages = []
            for commit in list(repo.get_commits()[:5]):
                message = getattr(getattr(commit, 'commit', None), 'message', None)
                if message:
                    messages.append(message)
            return messages
        except Exception as exc:
            state.log(f'[repo-sync] commit fetch failed for {repo_name}: {exc}')
            return []

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

    def analyze(self, repo_data, generation=None):
        name = repo_data['full_name']
        with state.work_gate:
            try:
                if generation is not None and not state.is_current_scan(generation):
                    return
                with state.lock:
                    if generation is not None and generation != state.scan_generation:
                        return
                    match = next((repo for repo in state.repos if str(repo.get('id')) == str(repo_data.get('id'))), None)
                    if match is not None:
                        match['status'] = 'analyzing'
                state.log(f'[repo-sync] starting {name}')
                repo_data['commits'] = self._recent_commits(name)
                zip_content = self.fetch_zip(name)
                if not zip_content:
                    state.log(f'[repo-sync] skipped {name}: zip unavailable')
                    self._finish_repo(repo_data, generation, {'status': 'error', 'description': repo_data.get('description') or 'ZIP unavailable'})
                    return
                self._analyze_zip(repo_data, generation, name, zip_content)
            except Exception as exc:
                state.log(f'[repo-sync] failed {name}: {exc}')
                self._finish_repo(repo_data, generation, {'status': 'error', 'description': 'Analysis failed'})

    def _finish_repo(self, repo_data, generation, fields):
        flush_now = False
        with state.lock:
            if generation is not None and generation != state.scan_generation:
                return
            repo_match = next((repo for repo in state.repos if str(repo.get('id')) == str(repo_data.get('id'))), None)
            if repo_match is None:
                return
            repo_match.update(fields)
            state.recompute_stats()
            flush_now = state.note_scan_dirty()
        if flush_now:
            state.flush_scan_cache()

    def _analyze_zip(self, repo_data, generation, name, zip_content):

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

        last_update = coerce_datetime(repo_data.get('last_update'))
        days_abandoned = (datetime.datetime.now(datetime.timezone.utc) - last_update).days if last_update else 0
        abandon_score = min(100, max(0, int((days_abandoned / 365) * 60 + (len(data['todos']) * 5) + (len(data['smells']) * 2))))
        tags = LayaDecisionService().score(' '.join(snippets) or name)
        if generation is not None and not state.is_current_scan(generation):
            return

        self._finish_repo(repo_data, generation, {
            'id': repo_data['id'],
            'name': name,
            'full_name': name,
            'description': desc,
            'mood': mood,
            'tshirt': tshirt,
            'abandon_score': abandon_score,
            'days_abandoned': days_abandoned,
            'custom_lines': data['custom_lines'],
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
            'content_hash': repo_data.get('content_hash'),
            'tag_fingerprint': state.tag_fingerprint(),
            'cache_hit': False,
        })
        state.log(f"[repo-sync] complete {name}: loc={data['custom_lines']}, mood={mood}, effort={tshirt}, abandoned_days={days_abandoned}")


_LINK_STOPWORDS = {
    'the', 'and', 'for', 'with', 'that', 'this', 'from', 'are', 'was', 'were', 'you', 'your',
    'our', 'not', 'but', 'have', 'has', 'had', 'will', 'just', 'into', 'about', 'then', 'than',
    'them', 'they', 'what', 'when', 'where', 'which', 'while', 'would', 'could', 'should',
    'there', 'their', 'been', 'being', 'also', 'can', 'its', 'let', 'use', 'using', 'used',
}


class ChatLinker:
    def __init__(self, ollama_url=None, ollama_model=None):
        self.ollama_url = ollama_url or state.settings.get('ollama_url')
        self.ollama_model = ollama_model or state.settings.get('ollama_model')

    @staticmethod
    def tokenize(text):
        return {
            token for token in re.findall(r'[a-z0-9]{3,}', (text or '').lower())
            if token not in _LINK_STOPWORDS and not token.isdigit()
        }

    @staticmethod
    def chat_fingerprint(chat):
        raw = '|'.join([
            str(chat.get('title') or ''),
            str(chat.get('created_on') or ''),
            str(chat.get('text') or '')[:400],
        ])
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]

    @staticmethod
    def readme_excerpt(repo):
        key = repo.get('name') or repo.get('full_name')
        blob = state.repo_cache.get(key) if key else None
        if not blob:
            return ''
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                for info in archive.infolist():
                    if info.is_dir() or not info.filename.upper().endswith('README.MD'):
                        continue
                    return archive.read(info).decode('utf-8', errors='ignore')[:800]
        except Exception:
            return ''
        return ''

    @staticmethod
    def dossier_body(repo):
        parts = [
            repo.get('description') or '',
            ' '.join(repo.get('tech_stack') or []),
            ' '.join(str(item) for item in (repo.get('endpoints') or [])),
            ' '.join(str(item) for item in (repo.get('todos') or [])),
            ChatLinker.readme_excerpt(repo),
        ]
        return '\n'.join(str(part) for part in parts if part)

    @staticmethod
    def _scores_above_confidence(scores):
        threshold = confidence_ratio()
        kept = {}
        for key, value in (scores or {}).items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number >= threshold:
                kept[str(key).lower()] = number
        return kept

    @staticmethod
    def laya_overlap(repo, chat):
        repo_scores = ChatLinker._scores_above_confidence(repo.get('tags'))
        chat_scores = ChatLinker._scores_above_confidence(chat.get('tags'))
        shared = set(repo_scores) & set(chat_scores)
        if not shared:
            return 0.0
        return sum(min(repo_scores[key], chat_scores[key]) for key in shared) / len(shared)

    @staticmethod
    def cheap_score(repo, chat):
        body_tokens = ChatLinker.tokenize(ChatLinker.dossier_body(repo))
        chat_tokens = ChatLinker.tokenize(' '.join([
            chat.get('title') or '',
            chat.get('summary') or '',
            chat.get('text') or '',
        ]))
        overlap = body_tokens & chat_tokens
        if body_tokens and overlap:
            word = min(1.0, len(overlap) / min(6, len(body_tokens)))
        else:
            word = 0.0
        score = (0.5 * ChatLinker.laya_overlap(repo, chat)) + (0.5 * word)
        name = (repo.get('full_name') or repo.get('name') or '').split('/')[-1]
        name_tokens = ChatLinker.tokenize(name)
        if name_tokens and name_tokens <= chat_tokens and score >= 0.2:
            score = min(1.0, score + 0.05)
        return round(score, 3), overlap

    @staticmethod
    def overlap_reason(overlap):
        words = ', '.join(sorted(overlap)[:4])
        if not words:
            return 'This conversation matches how the repository behaves, not only its name.'
        return f'This conversation follows the same unfinished work on {words}.'

    def llm_decision(self, repo, chat, cheap_score, overlap):
        prompt = (
            'Decide whether this conversation is about the same unfinished project as the repository. '
            'A shared name is not enough; match on behavior, features, and the plan that was left unfinished. '
            'Reply with JSON only: {"match": <number from 0 to 1>, "reason": "<one English sentence>"}.\n'
            f'Repository:\n{ChatLinker.dossier_body(repo)[:1500]}\n\n'
            f'Conversation title: {chat.get("title") or ""}\n'
            f'Summary: {chat.get("summary") or ""}\n'
            f'Excerpt:\n{(chat.get("text") or "")[:1200]}'
        )
        raw = LLMService.ask(prompt, self.ollama_url, self.ollama_model)
        parsed = ChatLinker.parse_decision(raw)
        if parsed is None:
            return {'match': cheap_score, 'reason': ChatLinker.overlap_reason(overlap)}
        return parsed

    @staticmethod
    def parse_decision(raw):
        match = re.search(r'\{.*\}', raw or '', flags=re.DOTALL)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except Exception:
            return None
        if not isinstance(payload, dict) or 'match' not in payload:
            return None
        try:
            score = float(payload.get('match'))
        except (TypeError, ValueError):
            return None
        reason = re.sub(r'\s+', ' ', str(payload.get('reason') or '')).strip()
        if not reason:
            reason = 'This conversation describes the same unfinished project behavior.'
        return {'match': round(min(1.0, max(0.0, score)), 3), 'reason': reason}

    def propose_links(self, repo, chats, confirmer=None):
        rejected = set(repo.get('rejected_chat_fingerprints') or [])
        candidates = []
        threshold = confidence_ratio()
        for chat in chats:
            fingerprint = ChatLinker.chat_fingerprint(chat)
            if fingerprint in rejected:
                continue
            score, overlap = ChatLinker.cheap_score(repo, chat)
            if score < threshold:
                continue
            candidates.append((chat, score, overlap, fingerprint))

        accepted = []
        chunk = max(1, int(state.settings.get('chunk_size', 5)))
        for start in range(0, len(candidates), chunk):
            batch = candidates[start:start + chunk]
            decisions = self._decide_batch(repo, batch, confirmer)
            for (chat, _score, overlap, fingerprint), decision in zip(batch, decisions):
                try:
                    match = float((decision or {}).get('match') or 0)
                except (TypeError, ValueError):
                    match = 0.0
                if match < threshold:
                    continue
                accepted.append({
                    'chat_id': chat.get('id'),
                    'fingerprint': fingerprint,
                    'score': round(match, 3),
                    'reason': (decision or {}).get('reason') or ChatLinker.overlap_reason(overlap),
                    'title': chat.get('title') or 'Conversation',
                    'pinned': False,
                })
        accepted.sort(key=lambda item: float(item.get('score') or 0), reverse=True)
        return accepted

    def _decide_batch(self, repo, batch, confirmer):
        if confirmer is not None or len(batch) <= 1:
            return [self._decide_one(repo, item, confirmer) for item in batch]
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            futures = [pool.submit(self._decide_one, repo, item, None) for item in batch]
            return [future.result() for future in futures]

    def _decide_one(self, repo, item, confirmer):
        chat, score, overlap, _fingerprint = item
        if confirmer is not None:
            return confirmer(repo, chat, score) or {'match': 0, 'reason': ''}
        return self.llm_decision(repo, chat, score, overlap)

    @staticmethod
    def merge_links(existing, auto_links, rejected):
        rejected = set(rejected or [])
        pinned = []
        pinned_fps = set()
        for link in existing or []:
            fingerprint = link.get('fingerprint')
            if not link.get('pinned') or not fingerprint or fingerprint in rejected:
                continue
            pinned.append(dict(link))
            pinned_fps.add(fingerprint)
        merged = list(pinned)
        for link in auto_links or []:
            fingerprint = link.get('fingerprint')
            if not fingerprint or fingerprint in rejected or fingerprint in pinned_fps:
                continue
            merged.append(dict(link))
        merged.sort(key=lambda item: (bool(item.get('pinned')), float(item.get('score') or 0)), reverse=True)
        return merged

    @staticmethod
    def find_chat(link):
        fingerprint = link.get('fingerprint')
        chat_id = str(link.get('chat_id') or '')
        by_id = next((chat for chat in state.conversations if str(chat.get('id')) == chat_id), None)
        if by_id is not None and ChatLinker.chat_fingerprint(by_id) == fingerprint:
            return by_id
        if fingerprint:
            matched = next((chat for chat in state.conversations if ChatLinker.chat_fingerprint(chat) == fingerprint), None)
            if matched is not None:
                link['chat_id'] = matched.get('id')
                link['title'] = matched.get('title') or link.get('title')
                return matched
        return by_id

    @staticmethod
    def public_links(repo):
        visible = []
        for link in repo.get('linked_chats') or []:
            chat = ChatLinker.find_chat(link)
            visible.append({
                'chat_id': (chat or {}).get('id') or link.get('chat_id'),
                'fingerprint': link.get('fingerprint'),
                'title': (chat or {}).get('title') or link.get('title') or 'Conversation unavailable',
                'score': link.get('score'),
                'reason': link.get('reason') or '',
                'pinned': bool(link.get('pinned')),
                'available': chat is not None,
            })
        return visible

    @staticmethod
    def rebind_loaded_chats():
        for repo in state.repos:
            for link in repo.get('linked_chats') or []:
                ChatLinker.find_chat(link)

    def run(self, generation):
        with state.lock:
            repo_ids = [repo.get('id') for repo in state.repos if repo.get('status') == 'ready']
        for repo_id in repo_ids:
            if not state.is_current_link(generation):
                return
            with state.lock:
                repo = next((item for item in state.repos if str(item.get('id')) == str(repo_id)), None)
                chats = list(state.conversations)
                if repo is None:
                    continue
                snapshot = dict(repo)
            auto_links = self.propose_links(snapshot, chats)
            flush_now = False
            with state.lock:
                if not state.is_current_link(generation):
                    return
                current = next((item for item in state.repos if str(item.get('id')) == str(repo_id)), None)
                if current is not None:
                    rejected = set(current.get('rejected_chat_fingerprints') or [])
                    current['linked_chats'] = ChatLinker.merge_links(current.get('linked_chats'), auto_links, rejected)
                    state.link_done += 1
                    flush_now = state.note_scan_dirty()
            if flush_now:
                state.flush_scan_cache()
        finished = False
        with state.lock:
            if state.is_current_link(generation):
                state.link_in_progress = False
                state.link_done = state.link_total
                finished = True
        if finished:
            state.log(f'[linker] linked conversations across {len(repo_ids)} repos')
            state.flush_scan_cache()


class TimeTravelService:
    @staticmethod
    def linked_memory(repo):
        links = repo.get('linked_chats') or []
        if not links:
            return 'No relevant conversations are linked to this repository.'
        blocks = []
        for link in links:
            chat = ChatLinker.find_chat(link)
            title = (chat or {}).get('title') or link.get('title') or 'Conversation'
            created = (chat or {}).get('created_on') or 'undated'
            summary = (chat or {}).get('summary') or ''
            excerpt = ((chat or {}).get('text') or '')[:600]
            if chat is None:
                excerpt = 'The conversation text is not loaded in this session.'
            blocks.append(
                f"### {title}\n"
                f"Date: {created}\n"
                f"Why it matches: {link.get('reason') or ''}\n"
                f"Summary: {summary}\n"
                f"Plan excerpt:\n{excerpt}"
            )
        return '\n\n'.join(blocks)

    @staticmethod
    def generate_master_prompt(repo_id, token):
        repo = next((r for r in state.repos if str(r['id']) == str(repo_id)), None)
        if not repo:
            return 'Repo not found in state.'
        chat_context = TimeTravelService.linked_memory(repo)

        zip_content = state.repo_cache.get(repo['name'])
        if not zip_content and token:
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
Based on the code state and the linked conversations, please provide:
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
        state.recompute_stats()
        payload = dict(state.stats)
        payload['scan_in_progress'] = state.scan_in_progress
        payload['scan_started_at'] = state.scan_started_at.isoformat() if state.scan_started_at else None
        payload['scan_finished_at'] = state.scan_finished_at.isoformat() if state.scan_finished_at else None
        payload['scan_log'] = list(state.scan_log[-40:])
        return jsonify(payload)


@app.route('/api/repos', methods=['GET'])
def get_repos():
    return jsonify(scan_status_payload())


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


@app.route('/api/settings', methods=['GET', 'POST'])
def update_settings():
    if request.method == 'GET':
        return jsonify({'settings': state.settings, 'tags': state.tags})
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
        backend = str(payload.get('laya_backend', state.settings['laya_backend'])).strip().lower()
        if backend not in ('auto', 'transformers', 'ollama', 'keyword'):
            backend = 'auto'
        state.settings['laya_backend'] = backend
        state._laya_backend_logged = None
    if 'laya_model' in payload:
        model_id = str(payload.get('laya_model') or '').strip()
        if model_id:
            state.settings['laya_model'] = model_id
            LayaDecisionService._pipeline = None
            LayaDecisionService._pipeline_model = None
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
        ChatLinker.rebind_loaded_chats()
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

        generation = state.start_scan_generation()
        state.log(f'[repo-sync] discovered {len(ordered)} repos via GitHub token; beginning in-memory sync')

        analyzer = RepoAnalyzer(token, payload.get('ollama_url'), payload.get('ollama_model'))
        tag_fp = state.tag_fingerprint()
        previous = {str(repo.get('id')): repo for repo in state.repos}
        profiles = []
        dirty = []
        for r in ordered:
            raw_name = getattr(r, 'name', None)
            repo_name = raw_name if isinstance(raw_name, str) and raw_name.strip() else r.full_name
            raw_description = getattr(r, 'description', None)
            repo_description = raw_description if isinstance(raw_description, str) else ''
            size = getattr(r, 'size', 0)
            if not isinstance(size, int):
                size = 0
            pushed = pushed_at_of(r)
            fingerprint = content_fingerprint(r.id, pushed, size)
            cached = previous.get(str(r.id))
            cache_hit = bool(
                cached
                and cached.get('status') == 'ready'
                and cached.get('content_hash') == fingerprint
                and cached.get('tag_fingerprint') == tag_fp
            )
            if cache_hit:
                profile = dict(cached)
                profile.update({
                    'full_name': r.full_name,
                    'name': repo_name,
                    'updated_at': r.updated_at,
                    'last_update': r.updated_at,
                    'size': size,
                    'status': 'ready',
                    'content_hash': fingerprint,
                    'tag_fingerprint': tag_fp,
                    'cache_hit': True,
                })
            else:
                profile = {
                    'id': r.id,
                    'full_name': r.full_name,
                    'name': repo_name,
                    'description': (cached or {}).get('description') or repo_description or 'GitHub repository',
                    'last_update': r.updated_at,
                    'updated_at': r.updated_at,
                    'size': size,
                    'status': 'pending',
                    'tags': (cached or {}).get('tags') or {},
                    'content_hash': fingerprint,
                    'tag_fingerprint': tag_fp,
                    'cache_hit': False,
                    'custom_lines': (cached or {}).get('custom_lines'),
                    'abandon_score': (cached or {}).get('abandon_score'),
                    'secrets': (cached or {}).get('secrets'),
                    'smells': (cached or {}).get('smells'),
                    'mood': (cached or {}).get('mood'),
                    'tshirt': (cached or {}).get('tshirt'),
                    'linked_chats': list((cached or {}).get('linked_chats') or []),
                    'rejected_chat_fingerprints': list((cached or {}).get('rejected_chat_fingerprints') or []),
                }
                dirty.append(profile)
            profiles.append(profile)

        with state.lock:
            state.repos = profiles
            state.recompute_stats()
        state.flush_scan_cache()

        for profile in dirty:
            future = executor.submit(analyzer.analyze, profile, generation)
            state.track_future(future, generation)

        if not dirty:
            with state.lock:
                state.scan_in_progress = False
                state.scan_finished_at = datetime.datetime.now(datetime.timezone.utc)
            state.log('[repo-sync] every repo matched the content hash and tag list')

        return jsonify(scan_status_payload())
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


@app.route('/api/tags/assign', methods=['POST'])
def assign_tags():
    payload = request.get_json(silent=True) or {}
    action = str(payload.get('action') or 'assign').strip().lower()
    tag_ids = [str(tag_id) for tag_id in (payload.get('tag_ids') or []) if str(tag_id).strip()]
    known = {str(tag.get('id')) for tag in state.tags}
    tag_ids = [tag_id for tag_id in tag_ids if tag_id in known]
    if action not in ('assign', 'unassign'):
        return jsonify({'error': 'Action must be assign or unassign.'}), 400
    for bucket, raw_ids in (('repos', payload.get('repo_ids') or []), ('chats', payload.get('chat_ids') or [])):
        for raw_id in raw_ids:
            key = str(raw_id)
            current = set(state.tag_assignments.setdefault(bucket, {}).get(key, []) or [])
            if action == 'assign':
                current.update(tag_ids)
            else:
                current.difference_update(tag_ids)
            state.tag_assignments[bucket][key] = sorted(current)
    state.save_tag_cache()
    return jsonify({'status': 'success', 'assignments': state.tag_assignments})


@app.route('/api/tags/<tag_id>', methods=['PUT', 'DELETE'])
def tag_detail(tag_id):
    if request.method == 'DELETE':
        state.tags = [tag for tag in state.tags if str(tag.get('id')) != str(tag_id)]
        for bucket in ('repos', 'chats'):
            for key, assigned in list(state.tag_assignments.get(bucket, {}).items()):
                state.tag_assignments[bucket][key] = [item for item in assigned if str(item) != str(tag_id)]
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


def _retag_one(chat_id, generation):
    with state.work_gate:
        with state.lock:
            if generation != state.chat_retag_generation:
                return
            chat = next((item for item in state.conversations if str(item.get('id')) == str(chat_id)), None)
            text = (chat or {}).get('text') or ''
        if chat is None:
            return
        tags = LayaDecisionService().score(text)
        finished = False
        with state.lock:
            if generation != state.chat_retag_generation:
                return
            chat['tags'] = tags
            valid = {str(tag.get('id')) for tag in state.tags}
            manual = state.tag_assignments.setdefault('chats', {}).get(str(chat_id), []) or []
            state.tag_assignments['chats'][str(chat_id)] = [item for item in manual if str(item) in valid]
            state.chat_retag_done += 1
            finished = state.chat_retag_done >= state.chat_retag_total
            if finished:
                state.chat_retag_in_progress = False
        if finished:
            state.save_tag_cache()
            state.log(f'[laya] retagged {state.chat_retag_total} conversations')


@app.route('/api/chats/overview', methods=['GET'])
def chats_overview():
    return jsonify(chat_overview_payload())


@app.route('/api/chats/retag', methods=['POST'])
def retag_chats():
    with state.lock:
        state.chat_retag_generation += 1
        generation = state.chat_retag_generation
        chat_ids = [chat.get('id') for chat in state.conversations]
        state.chat_retag_total = len(chat_ids)
        state.chat_retag_done = 0
        state.chat_retag_in_progress = bool(chat_ids)
    if not chat_ids:
        return jsonify(chat_overview_payload())
    for chat_id in chat_ids:
        executor.submit(_retag_one, chat_id, generation)
    payload = chat_overview_payload()
    payload['status'] = 'started'
    return jsonify(payload)


@app.route('/api/chats/<chat_id>', methods=['GET'])
def chat_detail(chat_id):
    chat = next((item for item in state.conversations if str(item.get('id')) == str(chat_id)), None)
    if chat is None:
        return jsonify({'error': 'Chat not found.'}), 404
    visible = [tag.get('name') for tag in state.tags if chat_matches_tag(chat, tag)]
    return jsonify({
        'id': chat.get('id'),
        'title': chat.get('title'),
        'source': chat.get('source'),
        'created_on': chat.get('created_on'),
        'summary': chat.get('summary'),
        'closure_reason': chat.get('closure_reason'),
        'text': chat.get('text'),
        'tags': chat.get('tags') or {},
        'visible_tags': visible,
        'manual_tags': list(state.tag_assignments.get('chats', {}).get(str(chat_id), []) or []),
    })


def _repo_by_id(repo_id):
    return next((repo for repo in state.repos if str(repo.get('id')) == str(repo_id)), None)


def _link_in_repo(repo, fingerprint):
    return next((link for link in (repo.get('linked_chats') or []) if link.get('fingerprint') == fingerprint), None)


@app.route('/api/link_chats', methods=['POST'])
def link_chats():
    payload = request.get_json(silent=True) or {}
    with state.lock:
        ready = [repo for repo in state.repos if repo.get('status') == 'ready']
        if state.link_in_progress:
            return jsonify(scan_status_payload())
        if not state.conversations:
            return jsonify({'error': 'Import conversations before linking.'}), 400
        if not ready:
            return jsonify({'error': 'No ready repositories to link.'}), 400
        state.link_generation += 1
        generation = state.link_generation
        state.link_total = len(ready)
        state.link_done = 0
        state.link_in_progress = True
    state.log(f'[linker] matching conversations to {len(ready)} repos')
    linker = ChatLinker(payload.get('ollama_url'), payload.get('ollama_model'))
    executor.submit(linker.run, generation)
    payload_out = scan_status_payload()
    payload_out['status'] = 'started'
    return jsonify(payload_out)


@app.route('/api/repos/<repo_id>/links/<fingerprint>', methods=['POST'])
def update_repo_link(repo_id, fingerprint):
    payload = request.get_json(silent=True) or {}
    action = str(payload.get('action') or '').strip().lower()
    if action not in ('unlink', 'pin', 'unpin'):
        return jsonify({'error': 'Action must be unlink, pin, or unpin.'}), 400
    flush_now = False
    with state.lock:
        repo = _repo_by_id(repo_id)
        if repo is None:
            return jsonify({'error': 'Repo not found.'}), 404
        links = list(repo.get('linked_chats') or [])
        link = _link_in_repo(repo, fingerprint)
        if link is None:
            return jsonify({'error': 'Link not found.'}), 404
        if action == 'unlink':
            rejected = set(repo.get('rejected_chat_fingerprints') or [])
            rejected.add(fingerprint)
            repo['rejected_chat_fingerprints'] = sorted(rejected)
            repo['linked_chats'] = [item for item in links if item.get('fingerprint') != fingerprint]
        else:
            link['pinned'] = action == 'pin'
        flush_now = state.note_scan_dirty()
    if flush_now:
        state.flush_scan_cache()
    else:
        state.flush_scan_cache()
    return jsonify({'status': 'success', 'repo': public_repo(_repo_by_id(repo_id))})


@app.route('/api/time_travel/<repo_id>', methods=['POST'])
def time_travel(repo_id):
    token = (request.get_json(silent=True) or {}).get('token')
    prompt = TimeTravelService.generate_master_prompt(repo_id, token)
    return jsonify({'prompt': prompt})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
