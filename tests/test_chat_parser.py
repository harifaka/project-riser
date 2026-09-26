import datetime
import unittest
from unittest.mock import Mock, patch

from app import (
    SCAN_CACHE_PATH,
    TAGS_CACHE_PATH,
    ChatAnalyzer,
    ChatLinker,
    LayaDecisionService,
    LLMService,
    TimeTravelService,
    app,
    chat_overview_payload,
    content_fingerprint,
    state,
)


def _idle_submit(fn, *args, **kwargs):
    future = Mock()
    future.add_done_callback = lambda callback: None
    return future


class ChatAnalyzerTests(unittest.TestCase):
    def setUp(self):
        self._tags_backup = TAGS_CACHE_PATH.read_bytes() if TAGS_CACHE_PATH.exists() else None
        self._scan_backup = SCAN_CACHE_PATH.read_bytes() if SCAN_CACHE_PATH.exists() else None
        state.settings['laya_backend'] = 'keyword'
        state.conversations = []
        state.chat_retag_in_progress = False

    def tearDown(self):
        if self._tags_backup is None:
            TAGS_CACHE_PATH.unlink(missing_ok=True)
        else:
            TAGS_CACHE_PATH.write_bytes(self._tags_backup)
        if self._scan_backup is None:
            SCAN_CACHE_PATH.unlink(missing_ok=True)
        else:
            SCAN_CACHE_PATH.write_bytes(self._scan_backup)

    def test_parses_chatgpt_json_export(self):
        payload = [
            {
                "title": "Auth bug",
                "create_time": 1700000000,
                "mapping": {
                    "a": {
                        "message": {
                            "content": {
                                "parts": [
                                    "I need to fix the auth bug.",
                                    "The login endpoint fails for new users."
                                ]
                            }
                        }
                    },
                    "b": {
                        "message": {
                            "content": {
                                "parts": [
                                    "Use the existing session middleware and add validation."
                                ]
                            }
                        }
                    }
                }
            }
        ]

        conversations = ChatAnalyzer.parse_and_analyze(__import__('json').dumps(payload), "http://localhost:11434", "llama3.1")

        self.assertEqual(len(conversations), 1)
        self.assertEqual(conversations[0]["source"], "ChatGPT")
        self.assertIn("auth bug", conversations[0]["text"].lower())
        self.assertEqual(conversations[0]["created_on"], "2023-11-14")

    def test_parses_gemini_html_export(self):
        html = """
        <html>
          <head><title>Gemini Export</title></head>
          <body>
            <div class="chat-container">
              <article class="message user">
                <h2>Prompt</h2>
                <p>I need a fix for the deployment timeout.</p>
              </article>
              <article class="message model">
                <h2>Response</h2>
                <p>Set a longer timeout and validate the health checks.</p>
              </article>
            </div>
          </body>
        </html>
        """

        conversations = ChatAnalyzer.parse_and_analyze(html, "http://localhost:11434", "llama3.1")

        self.assertEqual(len(conversations), 1)
        self.assertEqual(conversations[0]["source"], "Gemini")
        self.assertIn("deployment timeout", conversations[0]["text"].lower())

    def test_imports_browser_folder_payload_without_file_upload(self):
        with app.test_client() as client:
            response = client.post(
                "/api/import_chat_directory",
                json={
                    "source_type": "ChatGPT",
                    "folder_name": "my-export",
                    "chats": [{
                        "title": "Browser Import",
                        "source": "ChatGPT",
                        "text": "Need a fix for auth. Use the middleware.",
                        "messages": [
                            {"role": "user", "text": "Need a fix for auth."},
                            {"role": "assistant", "text": "Use the middleware."},
                        ],
                        "created_on": "2024-06-01",
                        "closure_reason": "SOLVED",
                        "tags": {"backend": 0.9},
                        "media": [{"path": "assets/clip.mp4", "filename": "clip.mp4", "kind": "video"}],
                    }],
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(state.conversations[-1]["source_file"], "my-export")
        self.assertEqual(state.conversations[-1]["source"], "ChatGPT")

    @patch("app.requests.get")
    def test_lists_available_ollama_models(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "models": [
                {"name": "mistral:latest"},
                {"name": "llama3.1:8b"}
            ]
        }

        self.assertEqual(
            LLMService.list_available_models("http://localhost:11434"),
            ["mistral:latest", "llama3.1:8b"]
        )

    @patch("app.os.path.exists", return_value=True)
    @patch("app.requests.get")
    def test_resolves_docker_ollama_url(self, mock_get, mock_exists):
        mock_get.return_value.status_code = 200

        self.assertEqual(
            LLMService.resolve_ollama_url("http://localhost:11434"),
            "http://host.docker.internal:11434"
        )

    @patch("app.executor.submit", side_effect=_idle_submit)
    @patch("app.Github")
    def test_scan_github_lists_all_available_repos_in_order(self, mock_github, _submit):
        repo_a = Mock(
            id=2,
            full_name="octo/alpha",
            updated_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
            size=25,
        )
        repo_a.get_commits.return_value.totalCount = 0

        repo_b = Mock(
            id=1,
            full_name="octo/beta",
            updated_at=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
            size=5,
        )
        repo_b.get_commits.return_value.totalCount = 0

        user = Mock()
        user.get_repos.return_value = [repo_a, repo_b]
        mock_github.return_value.get_user.return_value = user

        with app.test_client() as client:
            response = client.post(
                "/api/github/scan",
                json={
                    "token": "abc123",
                    "ollama_url": "http://localhost:11434",
                    "ollama_model": "llama3.1",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("repos", payload)
        self.assertTrue(payload["scan_in_progress"])
        self.assertEqual(len(payload["repos"]), 2)
        self.assertEqual(payload["repos"][0]["full_name"], "octo/beta")
        self.assertEqual(payload["repos"][1]["full_name"], "octo/alpha")
        user.get_repos.assert_called_once_with(type="all", sort="updated", direction="desc")

    @patch("app.requests.post")
    def test_llm_ask_fails_safe_when_model_is_unavailable(self, mock_post):
        mock_post.side_effect = Exception("LLM offline")

        self.assertEqual(LLMService.ask("Analyze the developer's mood from these commits.", "http://localhost:11434", "llama3.1"), "DONE")
        self.assertEqual(LLMService.ask("Estimate resurrection effort (S, M, L, XL)...", "http://localhost:11434", "llama3.1"), "M")
        self.assertEqual(LLMService.ask("Write a 1-sentence summary of this code:", "http://localhost:11434", "llama3.1"), "No code extracted.")

    @patch("app.executor.submit", side_effect=_idle_submit)
    @patch("app.Github")
    def test_scan_github_seeds_repo_cards_immediately(self, mock_github, _submit):
        repo = Mock(
            id=42,
            full_name="octo/demo",
            name="demo",
            updated_at=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
            size=40,
        )
        repo.get_commits.return_value = []
        user = Mock()
        user.get_repos.return_value = [repo]
        mock_github.return_value.get_user.return_value = user

        with app.test_client() as client:
            response = client.post(
                "/api/github/scan",
                json={
                    "token": "abc123",
                    "ollama_url": "http://localhost:11434",
                    "ollama_model": "llama3.1",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["repos"][0]["status"], "pending")
        self.assertEqual(payload["repos"][0]["full_name"], "octo/demo")

    def test_description_is_clamped_to_word_budget(self):
        text = "one two three four five six seven"
        self.assertEqual(LLMService.clamp_description(text, 4), "one two three four")

    def test_settings_modal_does_not_repeat_tag_editor_controls(self):
        with app.test_client() as client:
            response = client.get('/')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn('Tag List Editor', html)
        self.assertNotIn('Add Tag', html)
        self.assertIn('Edit tags', html)

    @patch("app.executor.submit", side_effect=_idle_submit)
    @patch("app.Github")
    @patch("app.requests.get")
    def test_scan_skips_zip_work_when_disabled_and_uses_readme_metadata(self, mock_get, mock_github, _submit):
        state.settings['zip_processing_enabled'] = False
        repo = Mock(
            id=77,
            full_name="octo/skipzip",
            name="skipzip",
            updated_at=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
            pushed_at=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
            size=12,
            description="A Python API service",
        )
        repo.get_commits.return_value = []
        user = Mock()
        user.get_repos.return_value = [repo]
        mock_github.return_value.get_user.return_value = user

        readme_response = Mock(status_code=200)
        readme_response.json.return_value = {"content": "VGVlZHVkIGJvZHk=", "download_url": "https://example.com/README.md"}
        mock_get.return_value = readme_response

        with app.test_client() as client:
            response = client.post("/api/github/scan", json={"token": "abc123", "ollama_url": "http://localhost:11434", "ollama_model": "llama3.1"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["repos"][0]["full_name"], "octo/skipzip")
        self.assertTrue(payload["repos"][0]["status"] in {"pending", "ready"})

    @patch("app.executor.submit", side_effect=_idle_submit)
    @patch("app.Github")
    def test_scan_reuses_repo_when_hash_and_tags_match(self, mock_github, submit):
        pushed = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
        fingerprint = content_fingerprint(42, pushed, 40)
        state.repos = [{
            "id": 42,
            "full_name": "octo/demo",
            "name": "demo",
            "status": "ready",
            "description": "cached summary",
            "content_hash": fingerprint,
            "tag_fingerprint": state.tag_fingerprint(),
            "custom_lines": 12,
            "updated_at": pushed,
        }]
        repo = Mock(id=42, full_name="octo/demo", name="demo", updated_at=pushed, pushed_at=pushed, size=40, description="GitHub")
        user = Mock()
        user.get_repos.return_value = [repo]
        mock_github.return_value.get_user.return_value = user

        with app.test_client() as client:
            response = client.post("/api/github/scan", json={"token": "abc123", "ollama_url": "http://localhost:11434", "ollama_model": "llama3.1"})

        payload = response.get_json()
        self.assertEqual(payload["repos"][0]["status"], "ready")
        self.assertEqual(payload["cached_count"], 1)
        self.assertFalse(payload["scan_in_progress"])
        submit.assert_not_called()

    @patch("app.executor.submit", side_effect=_idle_submit)
    @patch("app.Github")
    def test_scan_reprocesses_when_tag_list_changes(self, mock_github, submit):
        pushed = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
        fingerprint = content_fingerprint(7, pushed, 3)
        state.repos = [{
            "id": 7,
            "full_name": "octo/old",
            "name": "old",
            "status": "ready",
            "content_hash": fingerprint,
            "tag_fingerprint": "stale-tags",
            "updated_at": pushed,
        }]
        repo = Mock(id=7, full_name="octo/old", name="old", updated_at=pushed, pushed_at=pushed, size=3, description="")
        user = Mock()
        user.get_repos.return_value = [repo]
        mock_github.return_value.get_user.return_value = user

        with app.test_client() as client:
            response = client.post("/api/github/scan", json={"token": "abc123"})

        payload = response.get_json()
        self.assertEqual(payload["repos"][0]["status"], "pending")
        submit.assert_called_once()

    def test_laya_keyword_scores_and_auto_falls_back(self):
        scores = LayaDecisionService(backend="keyword").score("backend service", ["backend", "healthcare"])
        self.assertGreater(scores.get("backend", 0), scores.get("healthcare", 0))

        with patch.object(LayaDecisionService, "_transformers_available", return_value=False), patch.object(LayaDecisionService, "_score_ollama", return_value=None):
            chosen = LayaDecisionService(backend="auto").score("backend service", ["backend"])
        self.assertGreater(chosen.get("backend", 0), 0)

    def test_chat_tag_histogram_uses_confidence(self):
        state.tags = [
            {"id": "backend", "name": "backend", "color": "#22c55e"},
            {"id": "healthcare", "name": "healthcare", "color": "#38bdf8"},
        ]
        state.settings["confidence"] = 50
        state.conversations = [
            {"id": "1", "tags": {"backend": 0.9}, "source": "ChatGPT", "created_on": "2024-01-01", "closure_reason": "SOLVED", "title": "A", "summary": "a"},
            {"id": "2", "tags": {"healthcare": 0.2}, "source": "Gemini", "closure_reason": "TIMEOUT", "title": "B", "summary": "b"},
        ]
        state.tag_assignments = {"repos": {}, "chats": {}}

        payload = chat_overview_payload()
        counts = {item["name"]: item["count"] for item in payload["tag_counts"]}
        self.assertEqual(counts["backend"], 1)
        self.assertEqual(counts["healthcare"], 0)
        self.assertEqual(payload["heatmap"][0]["date"], "2024-01-01")

    @patch("app.Github")
    def test_retag_scores_chats_without_github(self, mock_github):
        state.tags = [{"id": "healthcare", "name": "healthcare", "color": "#38bdf8"}]
        state.settings["laya_backend"] = "keyword"
        state.conversations = [{"id": "c1", "text": "healthcare clinic notes", "tags": {}, "title": "Clinic", "source": "ChatGPT"}]
        state.tag_assignments = {"repos": {}, "chats": {"c1": ["healthcare"]}}

        def inline(fn, *args, **kwargs):
            fn(*args, **kwargs)
            future = Mock()
            future.add_done_callback = lambda callback: None
            return future

        with patch("app.executor.submit", side_effect=inline):
            with app.test_client() as client:
                response = client.post("/api/chats/retag")

        self.assertEqual(response.status_code, 200)
        self.assertIn("healthcare", state.conversations[0]["tags"])
        self.assertIn("healthcare", state.tag_assignments["chats"]["c1"])
        mock_github.assert_not_called()


class ChatLinkerTests(unittest.TestCase):
    def setUp(self):
        self._repos = list(state.repos)
        self._conversations = list(state.conversations)
        self._confidence = state.settings.get('confidence')
        state.settings['confidence'] = 70
        state.conversations = []
        state.link_in_progress = False

    def tearDown(self):
        state.repos = self._repos
        state.conversations = self._conversations
        state.settings['confidence'] = self._confidence
        state.link_in_progress = False

    def _repo(self):
        return {
            'id': 'repo-bce',
            'name': 'bce',
            'description': 'Invoice reconciliation pays each ledger payout through a flask webhook.',
            'tech_stack': ['flask'],
            'endpoints': ['POST /invoices/reconcile'],
            'todos': ['finish invoice reconciliation webhook'],
            'tags': {'backend': 0.92},
            'status': 'ready',
            'tshirt': 'M',
            'mood': 'DONE',
            'linked_chats': [],
            'rejected_chat_fingerprints': [],
        }

    def _good_chat(self):
        return {
            'id': 'chat-invoice',
            'title': 'Unfinished payout flow',
            'summary': 'Invoice reconciliation webhook left unfinished',
            'text': 'We stopped halfway through invoice reconciliation. The flask webhook still needs to post each ledger payout.',
            'tags': {'backend': 0.9},
            'created_on': '2024-03-01',
        }

    def _name_only_chat(self):
        return {
            'id': 'chat-abc',
            'title': 'Old ABC name',
            'summary': 'Rename notes',
            'text': 'The old project name was ABC. This thread is only about sourdough hydration and starter feeding schedules.',
            'tags': {'cooking': 0.95},
            'created_on': '2024-04-01',
        }

    def test_behavior_overlap_links_without_the_repo_name(self):
        repo = self._repo()
        good = self._good_chat()
        bad = self._name_only_chat()
        called = []

        def confirmer(_repo, chat, score):
            called.append(chat['id'])
            return {'match': score, 'reason': 'This conversation describes the unfinished invoice reconciliation flow.'}

        links = ChatLinker().propose_links(repo, [good, bad], confirmer=confirmer)

        self.assertEqual(called, ['chat-invoice'])
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]['chat_id'], 'chat-invoice')
        self.assertNotIn('bce', good['text'].lower())
        self.assertGreaterEqual(links[0]['score'], 0.7)

    def test_old_name_alone_does_not_link(self):
        repo = self._repo()
        bad = self._name_only_chat()
        same_name = {
            'id': 'chat-name',
            'title': 'abc',
            'text': 'abc abc abc ideas for later',
            'tags': {},
            'summary': 'abc',
        }
        named = dict(repo)
        named['name'] = 'abc'
        score, overlap = ChatLinker.cheap_score(named, same_name)
        self.assertLess(score, 0.7)
        self.assertFalse(overlap)
        links = ChatLinker().propose_links(repo, [bad], confirmer=lambda *_args: {'match': 1, 'reason': 'should not run'})
        self.assertEqual(links, [])

    def test_rejected_fingerprint_stays_unlinked_and_pins_survive(self):
        repo = self._repo()
        good = self._good_chat()
        fingerprint = ChatLinker.chat_fingerprint(good)
        repo['rejected_chat_fingerprints'] = [fingerprint]
        repo['linked_chats'] = [{
            'chat_id': 'kept',
            'fingerprint': 'pinned-fp',
            'score': 0.4,
            'reason': 'Kept by hand.',
            'title': 'Pinned plan',
            'pinned': True,
        }]
        auto = ChatLinker().propose_links(repo, [good], confirmer=lambda *_args: {'match': 1, 'reason': 'no'})
        merged = ChatLinker.merge_links(repo['linked_chats'], auto, repo['rejected_chat_fingerprints'])
        self.assertEqual(auto, [])
        self.assertEqual([link['fingerprint'] for link in merged], ['pinned-fp'])

    def test_time_travel_quotes_linked_chats_only(self):
        good = self._good_chat()
        second = {
            'id': 'chat-retry',
            'title': 'Webhook retry',
            'summary': 'Retry the ledger payout webhook',
            'text': 'Next we should retry the ledger payout webhook before adding new screens.',
            'created_on': '2024-03-02',
            'tags': {'backend': 0.88},
        }
        stray = {
            'id': 'chat-stray',
            'title': 'Sourdough',
            'text': 'sourdough hydration ratios are unrelated',
            'summary': 'sourdough hydration',
            'tags': {},
        }
        repo = self._repo()
        repo['linked_chats'] = [
            {'chat_id': good['id'], 'fingerprint': ChatLinker.chat_fingerprint(good), 'score': 0.91, 'reason': 'Invoice plan left unfinished.', 'title': good['title'], 'pinned': False},
            {'chat_id': second['id'], 'fingerprint': ChatLinker.chat_fingerprint(second), 'score': 0.84, 'reason': 'Webhook retry was the next step.', 'title': second['title'], 'pinned': False},
        ]
        state.repos = [repo]
        state.conversations = [good, second, stray]

        prompt = TimeTravelService.generate_master_prompt(repo['id'], None)

        self.assertIn('Unfinished payout flow', prompt)
        self.assertIn('Webhook retry', prompt)
        self.assertIn('Invoice plan left unfinished.', prompt)
        self.assertIn('Webhook retry was the next step.', prompt)
        self.assertNotIn('sourdough hydration', prompt)

        repo['linked_chats'] = []
        empty = TimeTravelService.generate_master_prompt(repo['id'], None)
        self.assertIn('No relevant conversations are linked to this repository.', empty)
        self.assertNotIn('sourdough hydration', empty)


if __name__ == "__main__":
    unittest.main()
