import json
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from evoagent.config import Settings
from evoagent.api import ApiHandler
from evoagent.ocr import OpenCodeReviewError, OpenCodeReviewRunner
from evoagent.service import ReviewService
from evoagent.workspace import (
    GitWorkspaceDiffCollector,
    ProjectCatalog,
    WorkspaceReviewRequest,
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "EvoAgent Test")
    _git(root, "config", "user.email", "evoagent@example.invalid")
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-m", "initial")
    return root


def _settings(db_path: Path, projects_file: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8080,
        db_path=str(db_path),
        max_diff_bytes=100_000,
        max_steps=8,
        timeout_seconds=10,
        llm_base_url="",
        llm_api_key="",
        llm_model="",
        github_webhook_secret="",
        github_token="",
        auto_post_review=False,
        projects_file=str(projects_file),
    )


def _fake_ocr(
    tmp_path: Path,
    body: str,
    *,
    exit_code: int = 0,
    version: str = "1.8.5",
) -> list[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "fake_ocr.py"
    script.write_text(
        "import sys\n"
        "if '--version' in sys.argv:\n"
        f"    print({version!r})\n"
        "    raise SystemExit(0)\n"
        f"print({body!r})\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


def test_workspace_request_is_strict_and_target_specific() -> None:
    request = WorkspaceReviewRequest.from_dict(
        {"project": "rook", "target": "range", "from_ref": "main", "to_ref": "HEAD"}
    )
    assert request.reviewers == ("local",)
    with pytest.raises(ValueError, match="unknown field"):
        WorkspaceReviewRequest.from_dict(
            {"project": "rook", "target": "workspace", "path": "C:/escape"}
        )
    with pytest.raises(ValueError, match="requires from_ref and to_ref"):
        WorkspaceReviewRequest.from_dict({"project": "rook", "target": "range"})


def test_project_catalog_and_diff_collector_keep_workspace_read_only(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    projects = tmp_path / "projects.json"
    projects.write_text(json.dumps({"rook": str(root)}), encoding="utf-8")
    catalog = ProjectCatalog.from_file(projects)
    assert catalog.resolve("rook") == root.resolve()
    with pytest.raises(ValueError, match="unknown project alias"):
        catalog.resolve("missing")

    (root / "app.py").write_text("value = eval(raw)\n", encoding="utf-8")
    (root / "new.py").write_text("# TODO validate\n", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in (root / "app.py", root / "new.py")}
    diff = GitWorkspaceDiffCollector(max_diff_bytes=100_000).collect(
        root, WorkspaceReviewRequest(project="rook", target="workspace")
    )
    after = {path.name: path.read_bytes() for path in (root / "app.py", root / "new.py")}
    assert before == after
    assert "+value = eval(raw)" in diff
    assert "+++ b/new.py" in diff


def test_project_catalog_rejects_traversal_and_symbolic_link(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    traversal = tmp_path / "projects-traversal.json"
    traversal.write_text(
        json.dumps({"rook": str(root / ".." / root.name)}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="symbolic links or traversal"):
        ProjectCatalog.from_file(traversal)

    link = tmp_path / "repository-link"
    try:
        link.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("当前 Windows 环境不允许创建测试符号链接")
    symlink = tmp_path / "projects-symlink.json"
    symlink.write_text(json.dumps({"rook": str(link)}), encoding="utf-8")
    with pytest.raises(ValueError, match="symbolic links or traversal"):
        ProjectCatalog.from_file(symlink)


def test_ocr_runner_normalizes_json_and_reports_stable_errors(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    payload = json.dumps(
        {
            "comments": [
                {
                    "path": "app.py",
                    "content": "Hardcoded secret can leak credentials.",
                    "start_line": 3,
                    "end_line": 3,
                    "suggestion_code": "token = os.environ['TOKEN']",
                    "existing_code": "token = 'secret'",
                }
            ]
        }
    )
    runner = OpenCodeReviewRunner(
        _fake_ocr(tmp_path, payload), expected_version="1.8.5", timeout_seconds=5
    )
    assert runner.status(check_version=True)["ready"] is True
    findings = runner.review(root, WorkspaceReviewRequest(project="rook", target="workspace"))
    assert findings[0].source == "open-code-review"
    assert findings[0].severity.value == "high"
    assert findings[0].suggestion_code == "token = os.environ['TOKEN']"

    invalid = OpenCodeReviewRunner(
        _fake_ocr(tmp_path / "invalid", "not-json"), expected_version="1.8.5"
    )
    with pytest.raises(OpenCodeReviewError) as exc_info:
        invalid.review(root, WorkspaceReviewRequest(project="rook", target="workspace"))
    assert exc_info.value.code == "ocr_invalid_json"


@pytest.mark.parametrize(
    ("runner", "expected_code"),
    [
        (
            lambda path: OpenCodeReviewRunner(
                _fake_ocr(path, "{}", version="1.8.4"), expected_version="1.8.5"
            ),
            "ocr_version_mismatch",
        ),
        (
            lambda path: OpenCodeReviewRunner(
                _fake_ocr(path, "{}", exit_code=7), expected_version="1.8.5"
            ),
            "ocr_failed",
        ),
        (
            lambda path: OpenCodeReviewRunner(
                _fake_ocr(path, "x" * 256),
                expected_version="1.8.5",
                max_output_bytes=64,
            ),
            "ocr_output_too_large",
        ),
    ],
)
def test_ocr_runner_fails_closed_with_stable_codes(
    tmp_path: Path,
    runner,
    expected_code: str,
) -> None:
    root = _repository(tmp_path)
    with pytest.raises(OpenCodeReviewError) as exc_info:
        runner(tmp_path / "ocr").review(
            root,
            WorkspaceReviewRequest(project="rook", target="workspace"),
        )
    assert exc_info.value.code == expected_code


def test_workspace_request_rejects_invalid_git_ref(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    with pytest.raises(ValueError, match="unsupported character"):
        WorkspaceReviewRequest(
            project="rook",
            target="range",
            from_ref="main; Remove-Item *",
            to_ref="HEAD",
        )
    with pytest.raises(ValueError, match="Git command failed"):
        GitWorkspaceDiffCollector(max_diff_bytes=100_000).collect(
            root,
            WorkspaceReviewRequest(
                project="rook",
                target="range",
                from_ref="missing-ref",
                to_ref="HEAD",
            ),
        )


def test_workspace_review_merges_local_and_ocr_findings(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "app.py").write_text("value = eval(raw)\n# TODO validate\n", encoding="utf-8")
    projects = tmp_path / "projects.json"
    projects.write_text(json.dumps({"rook": str(root)}), encoding="utf-8")
    payload = json.dumps(
        {
            "comments": [
                {
                    "path": "app.py",
                    "content": "TODO leaves validation incomplete.",
                    "start_line": 2,
                    "end_line": 2,
                }
            ]
        }
    )
    runner = OpenCodeReviewRunner(_fake_ocr(tmp_path, payload), expected_version="1.8.5")
    service = ReviewService(
        _settings(tmp_path / "evoagent.db", projects),
        project_catalog=ProjectCatalog.from_file(projects),
        ocr_runner=runner,
    )
    result = service.create_workspace_review(
        WorkspaceReviewRequest(
            project="rook", target="workspace", reviewers=("local", "ocr")
        )
    )
    service.queue.close()
    assert result["state"] == "SUCCESS"
    sources = {item["source"] for item in result["report"]["findings"]}
    assert sources == {"local-rules", "open-code-review"}
    assert _git(root, "status", "--short") == "M app.py"


def test_workspace_review_http_api_is_strict_and_read_only(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "app.py").write_text("value = eval(raw)\n", encoding="utf-8")
    projects = tmp_path / "projects.json"
    projects.write_text(json.dumps({"rook": str(root)}), encoding="utf-8")
    runner = OpenCodeReviewRunner(
        _fake_ocr(tmp_path, json.dumps({"comments": []})), expected_version="1.8.5"
    )
    settings = _settings(tmp_path / "api.db", projects)
    service = ReviewService(
        settings,
        project_catalog=ProjectCatalog.from_file(projects),
        ocr_runner=runner,
    )
    handler = type("TestApiHandler", (ApiHandler,), {"service": service, "settings": settings})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base_url + "/health", timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
        assert health["workspace_projects"] == ["rook"]
        assert health["llm_provider"] == "local"

        body = json.dumps(
            {
                "project": "rook",
                "target": "workspace",
                "reviewers": ["local", "ocr"],
                "background": False,
            }
        ).encode("utf-8")
        request = Request(
            base_url + "/v1/reviews/workspace",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
        assert result["state"] == "SUCCESS"
        assert _git(root, "status", "--short") == "M app.py"

        with urlopen(base_url + "/v1/reviewers/ocr/status", timeout=5) as response:
            status = json.loads(response.read().decode("utf-8"))
        assert status["ready"] is True
        assert status["actual_version"] == "1.8.5"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.queue.close()


def test_workspace_ocr_cancellation_stops_process_tree(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "app.py").write_text("value = eval(raw)\n", encoding="utf-8")
    projects = tmp_path / "projects.json"
    projects.write_text(json.dumps({"rook": str(root)}), encoding="utf-8")
    script = tmp_path / "slow_ocr.py"
    script.write_text(
        "import sys, time\n"
        "if '--version' in sys.argv:\n"
        "    print('1.8.5')\n"
        "    raise SystemExit(0)\n"
        "time.sleep(30)\n"
        "print('{\"comments\": []}')\n",
        encoding="utf-8",
    )
    runner = OpenCodeReviewRunner(
        [sys.executable, str(script)], expected_version="1.8.5", timeout_seconds=60
    )
    service = ReviewService(
        _settings(tmp_path / "cancel.db", projects),
        project_catalog=ProjectCatalog.from_file(projects),
        ocr_runner=runner,
    )
    result = service.enqueue_workspace_review(
        WorkspaceReviewRequest(
            project="rook",
            target="workspace",
            reviewers=("ocr",),
            background=True,
        )
    )
    task_id = result["task_id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        task = service.store.get(task_id)
        if task and task["state"] == "REVIEWING":
            break
        time.sleep(0.02)
    assert service.cancel_task(task_id) is True
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        task = service.store.get(task_id)
        if task and task["state"] in {"CANCELLED", "FAILED", "SUCCESS"}:
            break
        time.sleep(0.02)
    service.queue.close()
    assert task["state"] == "CANCELLED"
