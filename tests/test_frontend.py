import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
JAVASCRIPT = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "web" / "app.css").read_text(encoding="utf-8")


class _ContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.form_fields: dict[str, set[str]] = {}
        self._form_id = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        element_id = values.get("id")
        if element_id:
            self.ids.add(element_id)
        if tag == "form":
            self._form_id = element_id or ""
            self.form_fields.setdefault(self._form_id, set())
        if tag in {"input", "textarea", "select"} and self._form_id and values.get("name"):
            self.form_fields[self._form_id].add(str(values["name"]))

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._form_id = ""


class FrontendContractTests(unittest.TestCase):
    def test_existing_dom_and_form_contracts_are_preserved(self) -> None:
        parser = _ContractParser()
        parser.feed(HTML)
        required_ids = {
            "system-status", "page-title", "refresh", "logout", "stats", "recent-tasks",
            "review-form", "review-result", "all-tasks", "task-report", "create-fix",
            "reload-skills", "skill-list", "auto-evolve", "evolution-status",
            "evolution-form", "failure-list", "evolution-result", "login-overlay",
            "login-form", "login-error", "toast",
        }
        self.assertTrue(required_ids.issubset(parser.ids))
        self.assertEqual(
            {"repository", "diff", "pull_request", "async"},
            parser.form_fields["review-form"],
        )
        self.assertEqual({"skill_name", "prompt"}, parser.form_fields["evolution-form"])
        self.assertEqual(
            {"username", "password", "tenant_id"}, parser.form_fields["login-form"]
        )

    def test_review_workbench_exposes_three_panes_and_structured_results(self) -> None:
        for marker in (
            'class="workbench-pane file-pane"',
            'class="workbench-pane diff-pane"',
            'class="workbench-pane inspector-pane"',
            'data-review-tab="findings"',
            'data-review-tab="trace"',
            'data-review-tab="audit"',
        ):
            self.assertIn(marker, HTML)
        for field in ("finding.path", "finding.evidence", "finding.fix", "finding.test"):
            self.assertIn(field, JAVASCRIPT)
        self.assertIn("escapeHtml(finding.explanation", JAVASCRIPT)
        self.assertIn("escapeHtml(finding.evidence", JAVASCRIPT)

    def test_async_review_polls_until_all_terminal_states(self) -> None:
        self.assertIn('/v1/reviews${asyncQuery}', JAVASCRIPT)
        self.assertIn('/v1/tasks/${encodeURIComponent(id)}', JAVASCRIPT)
        terminal_match = re.search(r"terminalStates\s*=\s*new Set\(\[([^]]+)]\)", JAVASCRIPT)
        self.assertIsNotNone(terminal_match)
        terminal_block = terminal_match.group(1) if terminal_match else ""
        self.assertEqual({"SUCCESS", "FAILED", "CANCELLED"}, set(re.findall(r'"([A-Z]+)"', terminal_block)))
        self.assertIn("setTimeout(() => fetchReviewTask(id, true), 1200)", JAVASCRIPT)
        self.assertIn("stopReviewPolling();", JAVASCRIPT)

    def test_dark_workbench_has_readable_desktop_and_narrow_layouts(self) -> None:
        self.assertIn('font-family: Inter, "Segoe UI", "Microsoft YaHei", system-ui', CSS)
        self.assertIn('"Cascadia Code", "JetBrains Mono", Consolas, monospace', CSS)
        self.assertIn("grid-template-columns: 156px minmax(0, 1fr)", CSS)
        self.assertIn("grid-template-columns: 250px minmax(0, 1.2fr) minmax(320px, .95fr)", CSS)
        self.assertIn("grid-template-columns: 220px minmax(0, 1fr) minmax(300px, .8fr)", CSS)
        self.assertIn("font: 12px/1.65", CSS)
        self.assertIn(".workbench-pane { min-width: 0; min-height: 0", CSS)
        self.assertIn("overflow: auto", CSS)
        self.assertIn("@media (max-width: 1040px)", CSS)
        self.assertIn("@media (max-width: 720px)", CSS)
        self.assertIn("grid-template-columns: 1fr", CSS)
        self.assertIn("@media (prefers-reduced-motion: reduce)", CSS)

    def test_fix_creation_clears_stale_task_and_honours_prompt_cancel(self) -> None:
        self.assertIn(
            'selectedTask = null;\n  $("#create-fix").classList.add("hidden");',
            JAVASCRIPT,
        )
        self.assertIn("if (installationIdInput === null) return;", JAVASCRIPT)
        self.assertIn("if (rawInstallationId && !/^\\d+$/.test(rawInstallationId))", JAVASCRIPT)
        self.assertIn("encodeURIComponent(taskId)", JAVASCRIPT)


if __name__ == "__main__":
    unittest.main()
