#!/usr/bin/env python3
"""Verify stock 0.146.0 can resume a companion-created persistent thread."""
import importlib.util
import os
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("acceptance", HERE / "test_companion_allowlist.py")
m = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(m)
m.HERE = HERE
m.MOCK_MCP = HERE / "mock_memory_search_mcp.py"

class ReverseResume(unittest.TestCase):
    def setUp(self):
        self.httpd = m.http.server.ThreadingHTTPServer(("127.0.0.1", 0), m.ProviderHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.root = tempfile.TemporaryDirectory(prefix="frontend-b-reverse-")
        self.home = Path(self.root.name) / "codex-home"
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        m.config_for(self.home, self.url)

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.root.cleanup()

    def test_stock_resumes_companion_thread(self):
        m.APP_SERVER = Path(os.environ["COMPANION_APP_SERVER"])
        companion = m.AppServer(self.home)
        try:
            thread_id = companion.request("thread/start", {"cwd": self.root.name, "ephemeral": False})["thread"]["id"]
            companion.wait_mcp_ready(thread_id)
            m.send_turn(companion, thread_id)
        finally:
            companion.close()
        m.APP_SERVER = Path(os.environ["STOCK_APP_SERVER"])
        stock = m.AppServer(self.home)
        try:
            resumed = stock.request("thread/resume", {"threadId": thread_id})
            self.assertEqual(resumed.get("thread", {}).get("id"), thread_id)
            stock.wait_mcp_ready(thread_id)
            m.send_turn(stock, thread_id)
        finally:
            stock.close()

if __name__ == "__main__":
    unittest.main(verbosity=2)
