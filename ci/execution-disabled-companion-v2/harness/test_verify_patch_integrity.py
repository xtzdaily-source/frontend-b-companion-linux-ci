#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile

from verify_patch_integrity import inspect_patch, verify


def run(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


with tempfile.TemporaryDirectory(prefix="v2-patch-integrity-test-") as directory:
    root = Path(directory)
    source = root / "source"
    source.mkdir()
    run("git", "init", "--quiet", cwd=source)
    run("git", "config", "user.name", "CI", cwd=source)
    run("git", "config", "user.email", "ci@example.invalid", cwd=source)
    (source / "tracked.txt").write_text("before\n", encoding="utf-8")
    run("git", "add", "tracked.txt", cwd=source)
    run("git", "commit", "--quiet", "-m", "baseline", cwd=source)
    (source / "tracked.txt").write_text("after\n", encoding="utf-8")
    (source / "new.txt").write_text("one\ntwo\n", encoding="utf-8")
    run("git", "add", "-N", "new.txt", cwd=source)
    patch = root / "good.patch"
    patch.write_text(run("git", "diff", "--binary", cwd=source), encoding="utf-8")
    files = verify(source, patch)
    assert [(item.path, item.declared_lines) for item in files] == [("new.txt", 2)]

    bad = root / "bad.patch"
    bad.write_text(
        patch.read_text(encoding="utf-8").replace("@@ -0,0 +1,2 @@", "@@ -0,0 +1,1 @@"),
        encoding="utf-8",
    )
    try:
        inspect_patch(bad)
    except ValueError as error:
        assert "hunk count mismatch" in str(error)
    else:
        raise AssertionError("stale new-file hunk metadata was accepted")

print("patch-integrity self-tests: PASS")
