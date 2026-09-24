import datetime
import unittest
from unittest.mock import Mock, patch

from app import ChatAnalyzer, LLMService, app


class ChatAnalyzerTests(unittest.TestCase):
    def test_parses_chatgpt_json_export(self):
        payload = [
            {
                "title": "Auth bug",
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

    @patch("app.Github")
    def test_scan_github_lists_all_available_repos_in_order(self, mock_github):
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


if __name__ == "__main__":
    unittest.main()
