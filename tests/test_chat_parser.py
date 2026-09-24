import unittest

from app import ChatAnalyzer


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


if __name__ == "__main__":
    unittest.main()
