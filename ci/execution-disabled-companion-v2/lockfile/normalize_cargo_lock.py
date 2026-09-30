#!/usr/bin/env python3
"""Fail-closed validation and freezing of a Cargo.lock metadata-only drift."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
from pathlib import Path


def load_lock(path: Path) -> dict:
    """Parse Cargo's deliberately small lockfile TOML subset, rejecting surprises."""
    result: dict = {"package": []}
    current = result
    pending = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if pending:
            pending += " " + line
            if pending.count("[") != pending.count("]"):
                continue
            line, pending = pending, ""
        elif "=" in line and line.split("=", 1)[1].strip().startswith("[") and line.count("[") != line.count("]"):
            pending = line
            continue
        if line == "[[package]]":
            current = {}
            result["package"].append(current)
            continue
        if line.startswith("["):
            raise ValueError(f"unsupported Cargo.lock table: {line}")
        match = re.fullmatch(r"([A-Za-z0-9_-]+)\s*=\s*(.+)", line)
        if not match:
            raise ValueError(f"unsupported Cargo.lock syntax: {line}")
        key, encoded = match.groups()
        try:
            value = ast.literal_eval(encoded)
        except (SyntaxError, ValueError) as error:
            raise ValueError(f"unsupported Cargo.lock value for {key}") from error
        if key in current:
            raise ValueError(f"duplicate Cargo.lock key: {key}")
        current[key] = value
    if pending:
        raise ValueError("unterminated Cargo.lock array")
    if not result["package"]:
        raise ValueError("Cargo.lock contained no packages")
    return result


def workspace_versions(metadata_path: Path) -> dict[str, str]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    members = set(metadata["workspace_members"])
    versions: dict[str, str] = {}
    for package in metadata["packages"]:
        if package["id"] not in members:
            continue
        name = package["name"]
        if name in versions:
            raise ValueError(f"duplicate workspace package name: {name}")
        versions[name] = package["version"]
    if not versions:
        raise ValueError("metadata contained no workspace packages")
    return versions


def package_index(lock: dict, label: str) -> dict[tuple[str, str | None], list[dict]]:
    result: dict[tuple[str, str | None], list[dict]] = {}
    for package in lock.get("package", []):
        key = (package["name"], package.get("source"))
        result.setdefault(key, []).append(package)
    for key, packages in result.items():
        if key[1] is None and len(packages) != 1:
            raise ValueError(f"{label} has ambiguous local package {key[0]!r}")
    return result


def validate(
    baseline_path: Path,
    generated_path: Path,
    metadata_path: Path,
    allowed_from: str,
    allowed_to: str,
) -> dict:
    baseline = load_lock(baseline_path)
    generated = load_lock(generated_path)
    baseline_top = {key: value for key, value in baseline.items() if key != "package"}
    generated_top = {key: value for key, value in generated.items() if key != "package"}
    if baseline_top != generated_top:
        raise ValueError("lockfile top-level metadata changed")

    base_index = package_index(baseline, "baseline")
    generated_index = package_index(generated, "generated")
    if set(base_index) != set(generated_index):
        raise ValueError("package names or sources changed")

    workspace = workspace_versions(metadata_path)
    changes: list[dict[str, str]] = []
    for key in sorted(base_index):
        before_list = base_index[key]
        after_list = generated_index[key]
        if key[1] is not None:
            if before_list != after_list:
                raise ValueError(f"external package changed: {key[0]}")
            continue
        before = before_list[0]
        after = after_list[0]
        if before == after:
            continue
        name = key[0]
        if name not in workspace:
            raise ValueError(f"non-workspace local package changed: {name}")
        if set(before) != set(after):
            raise ValueError(f"package fields changed: {name}")
        changed_fields = [field for field in before if before[field] != after[field]]
        if changed_fields != ["version"]:
            raise ValueError(f"non-version package metadata changed for {name}: {changed_fields}")
        if before.get("source") is not None or before.get("checksum") is not None:
            raise ValueError(f"version drift package was not local: {name}")
        if before["version"] != allowed_from or after["version"] != allowed_to:
            raise ValueError(
                f"unapproved workspace version drift for {name}: "
                f"{before['version']} -> {after['version']}"
            )
        if workspace[name] != allowed_to:
            raise ValueError(
                f"generated version for {name} does not match Cargo metadata: "
                f"{after['version']} != {workspace[name]}"
            )
        changes.append({"name": name, "from": before["version"], "to": after["version"]})

    if not changes:
        raise ValueError("normalization produced no approved workspace version drift")

    generated_bytes = generated_path.read_bytes()
    return {
        "schema": 1,
        "policy": {
            "allowed_fields": ["version"],
            "allowed_transition": {"from": allowed_from, "to": allowed_to},
            "local_workspace_packages_only": True,
            "external_packages_exact": True,
            "dependencies_exact": True,
        },
        "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "cargo_metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
        "normalized_sha256": hashlib.sha256(generated_bytes).hexdigest(),
        "changed_workspace_packages": changes,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--generated", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--allow-from", default="0.0.0")
    parser.add_argument("--allow-to", default="0.146.0")
    args = parser.parse_args()

    report = validate(
        args.baseline,
        args.generated,
        args.metadata,
        args.allow_from,
        args.allow_to,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.generated, args.output)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
