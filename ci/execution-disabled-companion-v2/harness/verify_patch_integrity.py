#!/usr/bin/env python3
"""Fail closed on malformed unified-diff hunk counts and fresh-apply drift."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import tempfile


HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class NewFile:
    path: str
    declared_lines: int = 0
    added_lines: int = 0


def inspect_patch(patch: Path) -> list[NewFile]:
    lines = patch.read_text(encoding="utf-8").splitlines()
    new_files: dict[str, NewFile] = {}
    current_path: str | None = None
    current_is_new = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("diff --git a/"):
            current_path = line.split(" b/", 1)[1]
            current_is_new = False
        elif line == "new file mode 100644":
            current_is_new = True
            if current_path is None:
                raise ValueError("new file mode without path")
            new_files[current_path] = NewFile(current_path)
        elif line.startswith("@@ "):
            match = HUNK.match(line)
            if match is None:
                raise ValueError(f"invalid hunk header: {line}")
            old_expected = int(match.group(2) or 1)
            new_expected = int(match.group(4) or 1)
            old_actual = 0
            new_actual = 0
            added_actual = 0
            index += 1
            while index < len(lines):
                body = lines[index]
                if body.startswith("diff --git ") or body.startswith("@@ "):
                    index -= 1
                    break
                if body.startswith("\\ No newline at end of file"):
                    index += 1
                    continue
                if body.startswith(" "):
                    old_actual += 1
                    new_actual += 1
                elif body.startswith("-"):
                    old_actual += 1
                elif body.startswith("+"):
                    new_actual += 1
                    added_actual += 1
                else:
                    break
                index += 1
            if (old_actual, new_actual) != (old_expected, new_expected):
                raise ValueError(
                    f"hunk count mismatch for {current_path}: "
                    f"declared old/new={old_expected}/{new_expected}, "
                    f"actual={old_actual}/{new_actual}"
                )
            if current_is_new:
                if old_expected != 0:
                    raise ValueError(f"new file hunk has old lines: {current_path}")
                item = new_files[current_path]
                item.declared_lines += new_expected
                item.added_lines += added_actual
        index += 1
    for item in new_files.values():
        if item.declared_lines != item.added_lines:
            raise ValueError(
                f"new file count mismatch for {item.path}: "
                f"declared={item.declared_lines}, added={item.added_lines}"
            )
    return list(new_files.values())


def run(*args: str) -> None:
    subprocess.run(args, check=True, capture_output=True)


def verify(source_repo: Path, patch: Path) -> list[NewFile]:
    new_files = inspect_patch(patch)
    with tempfile.TemporaryDirectory(prefix="v2-patch-integrity-") as directory:
        checkout = Path(directory) / "source"
        run("git", "clone", "--quiet", "--no-hardlinks", str(source_repo), str(checkout))
        run("git", "-C", str(checkout), "checkout", "--quiet", "HEAD")
        run("git", "-C", str(checkout), "apply", "--check", str(patch.resolve()))
        run("git", "-C", str(checkout), "apply", str(patch.resolve()))
        run("git", "-C", str(checkout), "apply", "--reverse", "--check", str(patch.resolve()))
        for item in new_files:
            actual = len((checkout / item.path).read_text(encoding="utf-8").splitlines())
            if actual != item.declared_lines:
                raise ValueError(
                    f"fresh-applied new file mismatch for {item.path}: "
                    f"declared={item.declared_lines}, actual={actual}"
                )
    return new_files


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--patch", type=Path, required=True)
    args = parser.parse_args()
    for item in verify(args.source_repo, args.patch):
        print(f"NEW_FILE_COUNT_PASS {item.path} {item.declared_lines}")


if __name__ == "__main__":
    main()
