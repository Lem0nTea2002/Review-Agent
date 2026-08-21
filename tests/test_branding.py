from pathlib import Path
import unittest

from evoagent.config import Settings
from evoagent.report import to_markdown


ROOT = Path(__file__).resolve().parents[1]


class BrandingTests(unittest.TestCase):
    def test_public_surfaces_use_review_agent_brand(self) -> None:
        index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("<title>Review Agent", index)
        self.assertIn("REVIEW AGENT WORKSPACE", index)
        self.assertIn("· Review Agent`", app)

    def test_compatibility_identifiers_remain_stable(self) -> None:
        index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("evoagent/fix-*", index)
        self.assertIn('localStorage.getItem("evoagent_token")', app)

    def test_generated_report_and_default_app_name_use_review_agent(self) -> None:
        report = to_markdown({"repository": "demo", "findings": []})
        settings = Settings(
            host="127.0.0.1",
            port=8080,
            db_path=":memory:",
            max_diff_bytes=1024,
            max_steps=8,
            timeout_seconds=120,
            llm_base_url="",
            llm_api_key="",
            llm_model="",
            github_webhook_secret="",
            github_token="",
            auto_post_review=False,
        )

        self.assertTrue(report.startswith("# Review Agent PR Review"))
        self.assertEqual(settings.openrouter_app_name, "Review Agent")


if __name__ == "__main__":
    unittest.main()
