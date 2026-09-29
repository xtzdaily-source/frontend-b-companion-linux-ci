#!/usr/bin/env python3
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix="wrapper-test-") as tmp:
    root = Path(tmp)
    wrapper = root / "codex-compat-wrapper"
    shutil.copy2(HERE / "codex-compat-wrapper", wrapper)
    binary = root / "codex-app-server"
    binary.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\"\n", encoding="utf-8")
    binary.chmod(0o755); wrapper.chmod(0o755)
    good = subprocess.run([str(wrapper), "app-server", "--stdio", "--strict-config", "-c", "analytics.enabled=false"], text=True, capture_output=True)
    assert good.returncode == 0, good.stderr
    assert good.stdout == "--listen stdio:// --strict-config -c analytics.enabled=false\n"
    for argv in ([], ["app-server"], ["app-server", "--stdio", "--help"], ["exec", "--stdio", "--strict-config", "-c", "analytics.enabled=false"]):
        bad = subprocess.run([str(wrapper), *argv], text=True, capture_output=True)
        assert bad.returncode == 64, (argv, bad.returncode)
