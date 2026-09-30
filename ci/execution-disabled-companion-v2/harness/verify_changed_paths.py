#!/usr/bin/env python3
"""Enumerate and verify tracked plus untracked source changes fail-closed."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess


def git_paths(repo: Path, *args: str) -> set[str]:
    output = subprocess.run(
        ["git", "-C", str(repo), *args, "-z", "--"],
        check=True,
        capture_output=True,
    ).stdout
    return {
        item.decode("utf-8")
        for item in output.split(b"\0")
        if item
    }


def changed_paths(repo: Path) -> list[str]:
    tracked = git_paths(repo, "diff", "--name-only")
    untracked = git_paths(repo, "ls-files", "--others", "--exclude-standard")
    return sorted(tracked | untracked)


def expected_paths(path: Path) -> list[str]:
    values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    if any(not value for value in values):
        raise ValueError("expected path file contains a blank line")
    if values != sorted(set(values)):
        raise ValueError("expected paths must already be unique and sorted")
    return values


def verify(repo: Path, expected_file: Path) -> list[str]:
    actual = changed_paths(repo)
    expected = expected_paths(expected_file)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        unexpected = sorted(set(actual) - set(expected))
        raise ValueError(
            f"changed path mismatch; missing={missing}; unexpected={unexpected}"
        )
    return actual


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--expect", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    actual = verify(args.repo, args.expect)
    args.output.write_text("".join(f"{path}\n" for path in actual), encoding="utf-8")


if __name__ == "__main__":
    main()
