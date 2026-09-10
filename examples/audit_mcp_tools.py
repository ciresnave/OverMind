"""Audit an MCP server's tool schemas before pointing a model at them.

    python examples/audit_mcp_tools.py <command> [args...]

⚠️ These schemas are authored by someone else's server and change without
notice. MEASUREMENTS.md §14.1 - the permissive majority hides the strict
minority - so a schema that four providers tolerate can be rejected by the
fifth, with an error that names a JSON path rather than the cause.

Run this FIRST. A schema problem found here is a line of output; found later it
is a model that "gets the arguments wrong" for reasons nobody can see.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.mcp_tools import McpToolSource, McpUnavailable, audit_tools


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    command, args = sys.argv[1], sys.argv[2:]

    try:
        source = McpToolSource.stdio(command, args, env=os.environ,
                                     cwd=os.environ.get("MCP_CWD")).open()
    except McpUnavailable as exc:
        print(f"could not connect: {exc}")
        return 1

    try:
        report = audit_tools(source._tools)   # noqa: SLF001 - this IS the audit tool
        print(f"server offered {report['offered']} tools; "
              f"{report['converted']} converted to OpenAI function schemas")
        exp = source.experimental_capabilities
        print(f"capabilities.experimental: {json.dumps(exp) if exp else '(none advertised)'}")

        if report["clean"]:
            # ⚠️ Say what was CHECKED, or "clean" is just an absence.
            print("\nNo repairs needed. Checked: missing schema, non-object type, "
                  "`required` naming absent properties, missing or over-long description.")
        else:
            print(f"\n{len(report['issues'])} schema issue(s):")
            for issue in report["issues"]:
                print(f"  - {issue}")

        if report["unrepairable"]:
            print(f"\n!! {len(report['unrepairable'])} NOT REPAIRABLE - do not expose these:")
            for issue in report["unrepairable"]:
                print(f"  - {issue}")

        # A per-tool view, because "20 tools" hides which one is the problem.
        print("\nper-tool argument surface:")
        for schema in source.schemas():
            fn = schema["function"]
            props = list((fn["parameters"].get("properties") or {}).keys())
            req = fn["parameters"].get("required") or []
            print(f"  {fn['name']:<28} args={props if props else '(none)'} required={req}")
        return 0 if not report["unrepairable"] else 2
    finally:
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
