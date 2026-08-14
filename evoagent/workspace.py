import difflib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_ALIAS = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_REF = re.compile(r"^[A-Za-z0-9._/@{}^~:+-]{1,200}$")
_TARGETS = {"workspace", "range", "commit"}
_REVIEWERS = {"local", "ocr"}
_PROCESS_ENV = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP")


def _safe_process_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in _PROCESS_ENV if key in os.environ}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"})
    return env


@dataclass(frozen=True)
class WorkspaceReviewRequest:
    project: str
    target: str = "workspace"
    from_ref: str | None = None
    to_ref: str | None = None
    commit: str | None = None
    reviewers: tuple[str, ...] = ("local",)
    background: bool = False

    def __post_init__(self) -> None:
        if not _ALIAS.fullmatch(self.project):
            raise ValueError("project must be a configured alias")
        if self.target not in _TARGETS:
            raise ValueError("target must be workspace, range or commit")
        if not self.reviewers or len(set(self.reviewers)) != len(self.reviewers):
            raise ValueError("reviewers must be a non-empty unique list")
        unknown_reviewers = sorted(set(self.reviewers) - _REVIEWERS)
        if unknown_reviewers:
            raise ValueError("unknown reviewer: %s" % ", ".join(unknown_reviewers))
        for value in (self.from_ref, self.to_ref, self.commit):
            if value is not None and not _REF.fullmatch(value):
                raise ValueError("Git refs contain an unsupported character")
        if self.target == "workspace":
            if any((self.from_ref, self.to_ref, self.commit)):
                raise ValueError("workspace target does not accept Git refs")
        elif self.target == "range":
            if not self.from_ref or not self.to_ref or self.commit:
                raise ValueError("range target requires from_ref and to_ref only")
        elif not self.commit or self.from_ref or self.to_ref:
            raise ValueError("commit target requires commit only")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WorkspaceReviewRequest":
        allowed = {
            "project", "target", "from_ref", "to_ref", "commit", "reviewers", "background"
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError("unknown field: %s" % ", ".join(unknown))
        reviewers = value.get("reviewers", ["local"])
        if not isinstance(reviewers, list) or not all(isinstance(item, str) for item in reviewers):
            raise ValueError("reviewers must be a list of strings")
        background = value.get("background", False)
        if not isinstance(background, bool):
            raise ValueError("background must be a boolean")
        return cls(
            project=str(value.get("project", "")),
            target=str(value.get("target", "workspace")),
            from_ref=value.get("from_ref"),
            to_ref=value.get("to_ref"),
            commit=value.get("commit"),
            reviewers=tuple(reviewers),
            background=background,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "target": self.target,
            "from_ref": self.from_ref,
            "to_ref": self.to_ref,
            "commit": self.commit,
            "reviewers": list(self.reviewers),
            "background": self.background,
        }


class ProjectCatalog:
    def __init__(self, projects: dict[str, Path] | None = None):
        self._projects = dict(projects or {})

    @classmethod
    def from_file(cls, path: str | Path | None) -> "ProjectCatalog":
        if not path:
            return cls()
        source = Path(path).expanduser().resolve(strict=True)
        value = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("projects file must contain a JSON object")
        projects: dict[str, Path] = {}
        for alias, configured_path in value.items():
            if not isinstance(alias, str) or not _ALIAS.fullmatch(alias):
                raise ValueError("invalid project alias in projects file")
            if not isinstance(configured_path, str) or not configured_path:
                raise ValueError("project paths must be non-empty strings")
            raw = Path(configured_path).expanduser()
            if not raw.is_absolute():
                raw = source.parent / raw
            absolute = raw.absolute()
            resolved = raw.resolve(strict=True)
            if os.path.normcase(str(absolute)) != os.path.normcase(str(resolved)):
                raise ValueError("project path must not contain symbolic links or traversal")
            top = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=resolved,
                env=_safe_process_env(),
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if top.returncode != 0:
                raise ValueError("project path is not a Git repository: %s" % alias)
            git_root = Path(top.stdout.strip()).resolve(strict=True)
            if os.path.normcase(str(git_root)) != os.path.normcase(str(resolved)):
                raise ValueError("project path must point to the Git repository root")
            projects[alias] = resolved
        return cls(projects)

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple(sorted(self._projects))

    def resolve(self, alias: str) -> Path:
        try:
            return self._projects[alias]
        except KeyError as exc:
            raise ValueError("unknown project alias: %s" % alias) from exc


class GitWorkspaceDiffCollector:
    def __init__(self, max_diff_bytes: int, timeout_seconds: int = 30):
        self.max_diff_bytes = max_diff_bytes
        self.timeout_seconds = timeout_seconds

    def collect(self, root: Path, request: WorkspaceReviewRequest) -> str:
        if request.target == "workspace":
            self._verify_ref(root, "HEAD")
            diff = self._git(root, "diff", "--no-ext-diff", "--binary", "HEAD", "--")
            diff += self._untracked_diff(root)
        elif request.target == "range":
            from_ref = self._verify_ref(root, request.from_ref or "")
            to_ref = self._verify_ref(root, request.to_ref or "")
            diff = self._git(
                root, "diff", "--no-ext-diff", "--binary", "%s..%s" % (from_ref, to_ref), "--"
            )
        else:
            commit = self._verify_ref(root, request.commit or "")
            diff = self._git(root, "show", "--format=", "--no-ext-diff", "--binary", commit, "--")
        encoded = diff.encode("utf-8")
        if not encoded:
            raise ValueError("selected workspace target has no reviewable diff")
        if len(encoded) > self.max_diff_bytes:
            raise ValueError("workspace diff exceeds maximum size of %d bytes" % self.max_diff_bytes)
        return diff

    def _verify_ref(self, root: Path, value: str) -> str:
        return self._git(
            root, "rev-parse", "--verify", "--end-of-options", "%s^{commit}" % value
        ).strip()

    def _git(self, root: Path, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=root,
                env=_safe_process_env(),
                check=False,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Git command timed out") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()[-1000:]
            raise ValueError("Git command failed: %s" % detail)
        return result.stdout.decode("utf-8", errors="replace")

    def _untracked_diff(self, root: Path) -> str:
        raw = self._git(root, "ls-files", "--others", "--exclude-standard", "-z")
        blocks: list[str] = []
        for relative in sorted(item for item in raw.split("\0") if item):
            path = (root / relative).resolve(strict=True)
            if path.is_symlink() or root not in path.parents:
                raise ValueError("untracked file escapes the project root")
            if path.stat().st_size > self.max_diff_bytes:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            lines = text.splitlines(keepends=True)
            if not lines:
                continue
            normalized = relative.replace("\\", "/")
            patch = "".join(
                difflib.unified_diff([], lines, fromfile="/dev/null", tofile="b/" + normalized)
            )
            blocks.append(
                "diff --git a/{0} b/{0}\nnew file mode 100644\n{1}".format(normalized, patch)
            )
        return "".join(blocks)
