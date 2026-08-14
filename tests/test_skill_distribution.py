import hashlib
import json
from pathlib import Path
import shutil
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from evoagent.diff_parser import parse_unified_diff
from evoagent.skills import SandboxedSkillReviewer, SkillRegistry


def test_bundled_skill_manifests_match_entrypoints() -> None:
    skills_dir = Path(__file__).resolve().parents[1] / "skills"

    for manifest_path in sorted(skills_dir.glob("*/skill.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entrypoint = manifest_path.parent / manifest["entrypoint"]
        source = entrypoint.read_bytes()
        actual = hashlib.sha256(source).hexdigest()

        assert actual == manifest["sha256"], manifest["name"]
        assert b"\r\n" not in source, manifest["name"]


def test_bundled_skill_rejects_modified_entrypoint(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "skills" / "code-quality"
    target = tmp_path / "skills" / "code-quality"
    shutil.copytree(source, target)
    entrypoint = target / "skill.py"
    entrypoint.write_bytes(entrypoint.read_bytes() + b"\n# modified\n")

    with pytest.raises(ValueError, match="skill checksum mismatch: code-quality"):
        SkillRegistry(str(tmp_path / "skills")).reload()


def test_skill_subprocess_protocol_uses_utf8() -> None:
    root = Path(__file__).resolve().parents[1]
    reviewer = SandboxedSkillReviewer(
        "code-quality",
        str(root / "skills" / "code-quality" / "skill.py"),
    )
    diff = "--- a/app.py\n+++ b/app.py\n@@ -0,0 +1 @@\n+# TODO 完成校验\n"

    with patch("evoagent.skills.subprocess.run") as run:
        run.return_value = CompletedProcess([], 0, stdout="[]", stderr="")
        reviewer.review(diff, parse_unified_diff(diff))

    assert run.call_args.kwargs["encoding"] == "utf-8"
