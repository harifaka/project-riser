import unittest
from unittest.mock import patch

from app import ChatAnalyzer, LLMService


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


if __name__ == "__main__":
    unittest.main()
