#!/usr/bin/env python3
"""Self-tests for the fail-closed Cargo.lock normalization policy."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("normalize_cargo_lock", HERE / "normalize_cargo_lock.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


BASELINE = '''version = 4

[[package]]
name = "workspace-a"
version = "0.0.0"
dependencies = ["external"]

[[package]]
name = "external"
version = "1.2.3"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "abc"
'''


def generated(
    *,
    local_name="workspace-a",
    local_version="0.146.0",
    external_version="1.2.3",
    external_source="registry+https://github.com/rust-lang/crates.io-index",
    checksum="abc",
    dependency="external",
) -> str:
    return f'''version = 4

[[package]]
name = "{local_name}"
version = "{local_version}"
dependencies = ["{dependency}"]

[[package]]
name = "external"
version = "{external_version}"
source = "{external_source}"
checksum = "{checksum}"
'''


def expect_failure(root: Path, text: str, label: str) -> None:
    candidate = root / f"{label}.lock"
    candidate.write_text(text, encoding="utf-8")
    try:
        module.validate(root / "baseline.lock", candidate, root / "metadata.json", "0.0.0", "0.146.0")
    except ValueError:
        return
    raise AssertionError(f"{label} was unexpectedly accepted")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="lock-normalization-test-") as raw:
        root = Path(raw)
        (root / "baseline.lock").write_text(BASELINE, encoding="utf-8")
        (root / "generated.lock").write_text(generated(), encoding="utf-8")
        metadata = {
            "workspace_members": ["path+file:///tmp/workspace-a#0.146.0"],
            "packages": [
                {"id": "path+file:///tmp/workspace-a#0.146.0", "name": "workspace-a", "version": "0.146.0"}
            ],
        }
        (root / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

        report = module.validate(
            root / "baseline.lock",
            root / "generated.lock",
            root / "metadata.json",
            "0.0.0",
            "0.146.0",
        )
        assert report["changed_workspace_packages"] == [
            {"name": "workspace-a", "from": "0.0.0", "to": "0.146.0"}
        ]

        expect_failure(root, generated(local_version="0.147.0"), "unapproved-version")
        expect_failure(root, generated(local_name="workspace-renamed"), "package-name")
        expect_failure(root, generated(external_version="1.2.4"), "external-version")
        expect_failure(
            root,
            generated(external_source="git+https://example.invalid/repo?rev=deadbeef#deadbeef"),
            "source-or-git-revision",
        )
        expect_failure(root, generated(checksum="def"), "checksum")
        expect_failure(root, generated(dependency="other"), "dependency-change")
        expect_failure(root, BASELINE, "no-change")

    print("lockfile normalization self-tests: PASS")


if __name__ == "__main__":
    main()
