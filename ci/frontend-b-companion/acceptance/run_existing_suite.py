#!/usr/bin/env python3
"""Run the vendored acceptance unchanged, with CI paths injected at import time."""
import importlib.util
import os
import sys
import unittest
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ["COMPANION_ROOT"])
SOURCE = Path(os.environ["CODEX_SOURCE"])
APP_SERVER = Path(os.environ["COMPANION_APP_SERVER"])
spec = importlib.util.spec_from_file_location("existing_acceptance", HERE / "test_companion_allowlist.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
module.ROOT = ROOT
module.SOURCE = SOURCE
module.APP_SERVER = APP_SERVER
module.HERE = HERE
module.MOCK_MCP = HERE / "mock_memory_search_mcp.py"
baseline_path = Path(os.environ["STOCK_BASELINE_PATH"])
baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
if set(baseline) != {"name", "description", "parameters"}:
    raise SystemExit("invalid stock runtime baseline evidence")
if baseline["name"] != "memory_search":
    raise SystemExit("stock baseline did not discover memory_search")
module.MEMORY_DESCRIPTION = baseline["description"]
module.STOCK_PROVIDER_PARAMETERS = baseline["parameters"]
result = unittest.TextTestRunner(verbosity=2).run(
    unittest.defaultTestLoader.loadTestsFromTestCase(module.CompanionAllowlistAcceptance)
)
sys.exit(0 if result.wasSuccessful() else 1)
