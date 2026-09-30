#!/usr/bin/env python3
"""Self-tests for fail-closed tracked plus untracked path verification."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "verify_changed_paths", HERE / "verify_changed_paths.py"
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def run(repo: Path, *args: str):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def write_expected(path: Path, values: list[str]):
    path.write_text("".join(f"{value}\n" for value in sorted(values)), encoding="utf-8")


def expect_failure(repo: Path, expected: Path, label: str):
    try:
        module.verify(repo, expected)
    except ValueError:
        return
    raise AssertionError(f"{label} unexpectedly passed")


def main():
    with tempfile.TemporaryDirectory(prefix="v2-changed-paths-") as raw:
        root = Path(raw)
        repo = root / "repo"
        repo.mkdir()
        run(repo, "init", "-q")
        run(repo, "config", "user.name", "CI Test")
        run(repo, "config", "user.email", "ci@example.invalid")
        (repo / "tracked.rs").write_text("baseline\n", encoding="utf-8")
        (repo / "Cargo.lock").write_text("baseline\n", encoding="utf-8")
        run(repo, "add", "tracked.rs", "Cargo.lock")
        run(repo, "commit", "-qm", "baseline")

        (repo / "tracked.rs").write_text("patched\n", encoding="utf-8")
        (repo / "new_policy.rs").write_text("new\n", encoding="utf-8")
        authorized = root / "authorized.txt"
        write_expected(authorized, ["new_policy.rs", "tracked.rs"])
        assert module.verify(repo, authorized) == ["new_policy.rs", "tracked.rs"]

        (repo / "rogue.txt").write_text("unauthorized\n", encoding="utf-8")
        expect_failure(repo, authorized, "extra untracked file")
        (repo / "rogue.txt").unlink()

        (repo / "new_policy.rs").unlink()
        expect_failure(repo, authorized, "missing authorized new file")
        (repo / "new_policy.rs").write_text("new\n", encoding="utf-8")

        (repo / "Cargo.lock").write_text("normalized\n", encoding="utf-8")
        normalized = root / "normalized.txt"
        write_expected(normalized, ["Cargo.lock", "new_policy.rs", "tracked.rs"])
        assert module.verify(repo, normalized) == [
            "Cargo.lock",
            "new_policy.rs",
            "tracked.rs",
        ]

    print("changed-path verifier self-tests: PASS")


if __name__ == "__main__":
    main()
