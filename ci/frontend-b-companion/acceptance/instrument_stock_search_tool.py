#!/usr/bin/env python3
"""Temporarily instrument one stock source file for the CI-only decision probe."""
import sys
from pathlib import Path


def main() -> None:
    source = Path(sys.argv[1])
    target = source / "codex-rs/core/src/tools/spec_plan.rs"
    original = """pub(crate) fn search_tool_enabled(turn_context: &TurnContext) -> bool {
    turn_context.model_info.supports_search_tool && namespace_tools_enabled(turn_context)
}
"""
    instrumented = """pub(crate) fn search_tool_enabled(turn_context: &TurnContext) -> bool {
    let namespace_tools = namespace_tools_enabled(turn_context);
    let enabled = turn_context.model_info.supports_search_tool && namespace_tools;
    eprintln!(
        \"FRONTEND_B_STOCK_DIAG search_tool_enabled slug={:?} supports_search_tool={} namespace_tools={} enabled={}\",
        turn_context.model_info.slug,
        turn_context.model_info.supports_search_tool,
        namespace_tools,
        enabled,
    );
    enabled
}
"""
    body = target.read_text(encoding="utf-8")
    if body.count(original) != 1:
        raise SystemExit("stock instrumentation anchor is absent or ambiguous")
    target.write_text(body.replace(original, instrumented), encoding="utf-8")


if __name__ == "__main__":
    main()
