import hashlib
import json
import os
import re
import signal
import subprocess
import threading
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from .models import Finding, Severity
from .workspace import WorkspaceReviewRequest


_PROCESS_ENV = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP")
_CRITICAL = ("remote code execution", "authentication bypass", "远程代码执行", "认证绕过")
_HIGH = (
    "command injection", "sql injection", "hardcoded secret", "path traversal",
    "命令注入", "sql 注入", "硬编码密钥", "路径遍历",
)


class OpenCodeReviewError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__("%s: %s" % (code, message))
        self.code = code


def _redact(value: str) -> str:
    patterns = (
        r"(?i)(api[_-]?key|token|secret|password)\s*[=:]\s*[^\s,;]+",
        r"(?i)bearer\s+[A-Za-z0-9._-]+",
    )
    result = value
    for pattern in patterns:
        result = re.sub(pattern, "[REDACTED]", result)
    return result


class OpenCodeReviewRunner:
    def __init__(
        self,
        command: Sequence[str],
        *,
        expected_version: str = "1.8.5",
        timeout_seconds: int = 120,
        max_output_bytes: int = 2 * 1024 * 1024,
        env_allowlist: Sequence[str] = (),
    ):
        if not command or not all(isinstance(item, str) and item for item in command):
            raise ValueError("OCR command must be a non-empty argument list")
        self.command = tuple(command)
        self.expected_version = expected_version
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.env_allowlist = tuple(env_allowlist)
        self._active_lock = threading.Lock()
        self._active: dict[str, subprocess.Popen[bytes]] = {}

    def status(self, *, check_version: bool = False) -> dict[str, Any]:
        value: dict[str, Any] = {
            "configured": True,
            "expected_version": self.expected_version,
            "ready": None,
        }
        if not check_version:
            return value
        try:
            actual = self.version()
        except OpenCodeReviewError as exc:
            value.update({"ready": False, "error": exc.code})
            return value
        value.update({"actual_version": actual, "ready": actual == self.expected_version})
        if actual != self.expected_version:
            value["error"] = "ocr_version_mismatch"
        return value

    def version(self) -> str:
        output = self._invoke([*self.command, "--version"], Path.cwd())
        match = re.search(r"\b(\d+\.\d+\.\d+)\b", output)
        if not match:
            raise OpenCodeReviewError("ocr_version_unreadable", "unable to parse OCR version")
        actual = match.group(1)
        if actual != self.expected_version:
            raise OpenCodeReviewError(
                "ocr_version_mismatch",
                "expected %s, found %s" % (self.expected_version, actual),
            )
        return actual

    def review(
        self,
        root: Path,
        request: WorkspaceReviewRequest,
        *,
        task_id: str = "",
    ) -> list[Finding]:
        self.version()
        command = [*self.command, "review", "--repo", str(root), "--format", "json", "--audience", "agent"]
        if request.target == "range":
            command.extend(["--from", request.from_ref or "", "--to", request.to_ref or ""])
        elif request.target == "commit":
            command.extend(["--commit", request.commit or ""])
        output = self._invoke(command, root, task_id=task_id)
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            raise OpenCodeReviewError("ocr_invalid_json", "OCR output is not valid JSON") from exc
        if isinstance(payload, list):
            comments = payload
        elif isinstance(payload, dict):
            comments = payload.get("comments", payload.get("findings"))
        else:
            comments = None
        if not isinstance(comments, list):
            raise OpenCodeReviewError("ocr_invalid_schema", "OCR output lacks a comments list")
        return [self._finding(item) for item in comments]

    def cancel(self, task_id: str) -> bool:
        with self._active_lock:
            process = self._active.get(task_id)
        if process is None:
            return False
        self._terminate_tree(process)
        return True

    def _invoke(self, command: Sequence[str], cwd: Path, *, task_id: str = "") -> str:
        env = {key: os.environ[key] for key in _PROCESS_ENV if key in os.environ}
        for key in self.env_allowlist:
            if key in os.environ:
                env[key] = os.environ[key]
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(
                list(command),
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **kwargs,
            )
        except OSError as exc:
            raise OpenCodeReviewError("ocr_unavailable", str(exc)) from exc
        if task_id:
            with self._active_lock:
                if task_id in self._active:
                    self._terminate_tree(process)
                    raise OpenCodeReviewError(
                        "ocr_duplicate_task", "OCR task is already running"
                    )
                self._active[task_id] = process
        try:
            try:
                stdout, stderr = process.communicate(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                self._terminate_tree(process)
                process.communicate()
                raise OpenCodeReviewError(
                    "ocr_timeout", "OCR exceeded %d seconds" % self.timeout_seconds
                ) from exc
        finally:
            if task_id:
                with self._active_lock:
                    self._active.pop(task_id, None)
        if len(stdout) + len(stderr) > self.max_output_bytes:
            raise OpenCodeReviewError("ocr_output_too_large", "OCR output exceeded the configured limit")
        if process.returncode != 0:
            detail = _redact(stderr.decode("utf-8", errors="replace").strip()[-1000:])
            raise OpenCodeReviewError("ocr_failed", detail or "OCR exited with a non-zero status")
        try:
            return stdout.decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError as exc:
            raise OpenCodeReviewError(
                "ocr_invalid_encoding", "OCR output is not valid UTF-8"
            ) from exc

    @staticmethod
    def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)

    @staticmethod
    def _finding(value: Any) -> Finding:
        if not isinstance(value, dict):
            raise OpenCodeReviewError("ocr_invalid_schema", "each OCR comment must be an object")
        path_value = str(value.get("path", "")).replace("\\", "/")
        path = PurePosixPath(path_value)
        if not path_value or path.is_absolute() or ".." in path.parts:
            raise OpenCodeReviewError("ocr_invalid_schema", "OCR returned an unsafe path")
        content = str(value.get("content", "")).strip()
        if not content:
            raise OpenCodeReviewError("ocr_invalid_schema", "OCR comment content is required")
        try:
            start = int(value.get("start_line"))
            end = int(value.get("end_line", start))
        except (TypeError, ValueError) as exc:
            raise OpenCodeReviewError("ocr_invalid_schema", "OCR line numbers must be integers") from exc
        if start <= 0 or end < start:
            raise OpenCodeReviewError("ocr_invalid_schema", "OCR line range is invalid")
        lower = content.lower()
        if any(token in lower for token in _CRITICAL):
            severity = Severity.CRITICAL
        elif any(token in lower for token in _HIGH):
            severity = Severity.HIGH
        else:
            severity = Severity.MEDIUM
        title = content.splitlines()[0][:120]
        digest = hashlib.sha256(
            (path_value + ":" + str(start) + ":" + content).encode("utf-8")
        ).hexdigest()[:12].upper()
        existing = str(value.get("existing_code", ""))
        suggestion = str(value.get("suggestion_code", ""))
        return Finding(
            rule_id="OCR-" + digest,
            severity=severity,
            title=title,
            explanation=content,
            path=path_value,
            line=start,
            evidence=existing or content,
            fix=suggestion or "Review and apply the suggested correction.",
            test="Add or update a focused regression test for this finding.",
            confidence=0.8,
            source="open-code-review",
            start_line=start,
            end_line=end,
            suggestion_code=suggestion or None,
            existing_code=existing or None,
            provenance=[{"source": "open-code-review", "adapter": "ocr-cli"}],
        )


def merge_findings(local: list[Finding], external: list[Finding]) -> list[Finding]:
    result = list(local)
    for candidate in external:
        duplicate_index = next(
            (
                index
                for index, current in enumerate(result)
                if current.path == candidate.path
                and _overlaps(current, candidate)
                and _same_evidence(current, candidate)
            ),
            None,
        )
        if duplicate_index is None:
            result.append(candidate)
            continue
        current = result[duplicate_index]
        provenance = list(current.provenance)
        provenance.extend(item for item in candidate.provenance if item not in provenance)
        result[duplicate_index] = replace(
            current,
            suggestion_code=current.suggestion_code or candidate.suggestion_code,
            existing_code=current.existing_code or candidate.existing_code,
            provenance=provenance,
        )
    order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}
    return sorted(result, key=lambda item: (order[item.severity], item.path, item.line, item.rule_id))


def _overlaps(left: Finding, right: Finding) -> bool:
    left_start = left.start_line or left.line
    left_end = left.end_line or left.line
    right_start = right.start_line or right.line
    right_end = right.end_line or right.line
    return max(left_start, right_start) <= min(left_end, right_end)


def _same_evidence(left: Finding, right: Finding) -> bool:
    def normalize(value: str) -> str:
        return re.sub(r"\s+", " ", value.strip().lower())

    left_values = {normalize(left.evidence), normalize(left.explanation)} - {""}
    right_values = {normalize(right.evidence), normalize(right.explanation)} - {""}
    return bool(left_values & right_values)


class WorkspaceCompositeReviewer:
    def __init__(
        self,
        base_reviewer: Any,
        ocr_runner: OpenCodeReviewRunner | None,
        root: Path,
        request: WorkspaceReviewRequest,
    ):
        self.base_reviewer = base_reviewer
        self.ocr_runner = ocr_runner
        self.root = root
        self.request = request
        self.name = "workspace-" + "+".join(request.reviewers)

    def review(self, diff: str, parsed: Any) -> list[Finding]:
        return self.review_with_context("", diff, parsed)

    def review_with_context(self, task_id: str, diff: str, parsed: Any) -> list[Finding]:
        local: list[Finding] = []
        if "local" in self.request.reviewers:
            contextual = getattr(self.base_reviewer, "review_with_context", None)
            local = contextual(task_id, diff, parsed) if contextual else self.base_reviewer.review(diff, parsed)
        external: list[Finding] = []
        if "ocr" in self.request.reviewers:
            if self.ocr_runner is None:
                raise OpenCodeReviewError("ocr_unavailable", "OCR is not configured")
            external = self.ocr_runner.review(
                self.root, self.request, task_id=task_id
            )
        return merge_findings(local, external)
