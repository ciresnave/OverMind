"""Tests for the MCP tool source.

⚠️ No `mcp` package needed: schema translation is pure, and invocation is tested
through a fake session. The point of these tests is that schemas written by
SOMEONE ELSE'S SERVER are handled predictably, and that wiring MCP in did not
move the gate.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.gate import DenyUnlessDeclared, Gate, GatedExecutor, Ledger  # noqa: E402
from overmind.mcp_tools import (  # noqa: E402
    McpToolSource, MAX_DESCRIPTION, audit_tools, convert_tool, convert_tools,
)


class Obj:
    """A stand-in for an SDK Tool object with arbitrary attribute names."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class TestSchemaFieldNaming(unittest.TestCase):
    """⚠️ MEASURED: the Python SDK 2.2.0 exposes `input_schema` while the wire
    field and the TS SDK use `inputSchema`. Reading only one raises
    AttributeError, which reads as a broken tool rather than a naming mismatch."""

    SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}}

    def test_snake_case_attribute(self):
        schema, issues = convert_tool(Obj(name="t", description="d", input_schema=self.SCHEMA))
        self.assertEqual(schema["function"]["parameters"]["properties"], {"a": {"type": "string"}})
        self.assertEqual(issues, [])

    def test_camel_case_attribute(self):
        schema, issues = convert_tool(Obj(name="t", description="d", inputSchema=self.SCHEMA))
        self.assertEqual(schema["function"]["parameters"]["properties"], {"a": {"type": "string"}})
        self.assertEqual(issues, [])

    def test_plain_mapping(self):
        schema, _ = convert_tool({"name": "t", "description": "d", "inputSchema": self.SCHEMA})
        self.assertEqual(schema["function"]["name"], "t")


class TestSchemaRepairsAreReported(unittest.TestCase):
    """⚠️ A silently normalised schema is a difference between what the server
    offers and what the model was told, and it surfaces later as the model
    'getting the arguments wrong'. Repairs are reported."""

    def test_missing_schema_is_repaired_and_reported(self):
        schema, issues = convert_tool(Obj(name="t", description="d"))
        self.assertEqual(schema["function"]["parameters"], {"type": "object", "properties": {}})
        self.assertTrue(any("no input schema" in i.problem for i in issues))
        self.assertTrue(all(i.repaired for i in issues))

    def test_missing_type_is_set_to_object(self):
        _, issues = convert_tool(Obj(name="t", description="d", input_schema={"properties": {}}))
        self.assertTrue(any("omitted type" in i.problem for i in issues))

    def test_wrong_type_is_flagged_as_unrepairable(self):
        _, issues = convert_tool(Obj(name="t", description="d",
                                     input_schema={"type": "array", "properties": {}}))
        self.assertTrue(any(not i.repaired for i in issues))

    def test_required_naming_absent_properties_is_dropped(self):
        """§14.1 - lenient providers accept this and strict ones reject it."""
        schema, issues = convert_tool(Obj(
            name="t", description="d",
            input_schema={"type": "object", "properties": {"a": {}}, "required": ["a", "ghost"]}))
        self.assertEqual(schema["function"]["parameters"]["required"], ["a"])
        self.assertTrue(any("ghost" in i.problem for i in issues))

    def test_overlong_description_is_truncated(self):
        _, issues = convert_tool(Obj(name="t", description="x" * (MAX_DESCRIPTION + 50),
                                     input_schema={"type": "object", "properties": {}}))
        self.assertTrue(any("truncated" in i.problem for i in issues))

    def test_missing_description_is_reported_not_hidden(self):
        _, issues = convert_tool(Obj(name="t", description="",
                                     input_schema={"type": "object", "properties": {}}))
        self.assertTrue(any("no description" in i.problem for i in issues))

    def test_unnamed_tool_is_dropped_not_guessed(self):
        schema, issues = convert_tool(Obj(description="d"))
        self.assertEqual(schema, {})
        self.assertFalse(issues[0].repaired)

    def test_convert_tools_skips_the_unusable_and_keeps_the_rest(self):
        schemas, issues = convert_tools([Obj(description="no name"),
                                         Obj(name="ok", description="d")])
        self.assertEqual([s["function"]["name"] for s in schemas], ["ok"])
        self.assertTrue(any(not i.repaired for i in issues))


class TestAudit(unittest.TestCase):
    def test_clean_set_reports_clean(self):
        report = audit_tools([Obj(name="a", description="d",
                                  input_schema={"type": "object", "properties": {}})])
        self.assertTrue(report["clean"])
        self.assertEqual(report["converted"], 1)
        self.assertEqual(report["unrepairable"], [])

    def test_unrepairable_is_separated_from_repaired(self):
        report = audit_tools([Obj(name="bad", description="d",
                                  input_schema={"type": "array"}),
                              Obj(name="fixable", description="d")])
        self.assertFalse(report["clean"])
        self.assertEqual(len(report["unrepairable"]), 1)
        self.assertEqual(report["unrepairable"][0].tool, "bad")


class FakeSession:
    """Records calls; mimics the SDK's result shape."""

    def __init__(self, result_text="done", is_error=False):
        self.calls = []
        self.result_text = result_text
        self.is_error = is_error

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return Obj(content=[Obj(text=self.result_text)], is_error=self.is_error)


def source_with(session, tools):
    """An McpToolSource wired to a fake session, without opening a subprocess."""
    import asyncio
    import threading
    src = McpToolSource(command="unused")
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    src._loop = loop
    src._session = session
    src._tools = tools
    _, src._issues = convert_tools(tools)
    return src


class TestInvocation(unittest.TestCase):
    def setUp(self):
        self.session = FakeSession("QUEUED for bob")
        self.tools = [Obj(name="fam_send_message", description="send",
                          input_schema={"type": "object",
                                        "properties": {"to_entity": {"type": "string"},
                                                       "text": {"type": "string"}},
                                        "required": ["text"]})]
        self.src = source_with(self.session, self.tools)

    def tearDown(self):
        self.src.close()

    def test_call_flattens_content_to_text(self):
        out = self.src.call("fam_send_message", to_entity="bob", text="hi")
        self.assertEqual(out, "QUEUED for bob")
        self.assertEqual(self.session.calls, [("fam_send_message", {"to_entity": "bob", "text": "hi"})])

    def test_error_results_are_returned_as_text_not_raised(self):
        """⚠️ The gate already separates refusal from failure, and the model has
        to be able to read either."""
        src = source_with(FakeSession("no such entity", is_error=True), self.tools)
        try:
            self.assertTrue(src.call("fam_send_message", text="x").startswith("ERROR:"))
        finally:
            src.close()

    def test_callables_are_a_plain_mapping_for_the_executor(self):
        callables = self.src.callables()
        self.assertEqual(list(callables), ["fam_send_message"])
        self.assertTrue(callable(callables["fam_send_message"]))

    def test_schemas_can_be_narrowed(self):
        self.assertEqual(self.src.schemas(only=[]), [])
        self.assertEqual(len(self.src.schemas()), 1)


class TestGateBoundaryDidNotMove(unittest.TestCase):
    """⚠️ THE PROPERTY THAT MAKES THE HARNESS SAFE IS *WHERE* THE GATE SITS.
    Wiring MCP in must not relocate it to anything a model can write to."""

    def setUp(self):
        self.session = FakeSession("sent")
        self.tools = [Obj(name="fam_send_message", description="send",
                          input_schema={"type": "object", "properties": {}}),
                      Obj(name="fam_kick_member", description="kick",
                          input_schema={"type": "object", "properties": {}})]
        self.src = source_with(self.session, self.tools)

    def tearDown(self):
        self.src.close()

    def test_a_denied_mcp_tool_never_reaches_the_server(self):
        gate = Gate([DenyUnlessDeclared(reversible=frozenset({"fam_send_message"}))],
                    ledger=Ledger())
        ex = GatedExecutor(gate, self.src.callables())
        outcome = ex.execute("fam_kick_member", {"channel_id": "c", "target_entity": "e"})
        self.assertFalse(outcome.allowed)
        self.assertEqual(self.session.calls, [], "a denied call still reached the MCP server")

    def test_an_allowed_mcp_tool_does_reach_the_server(self):
        """The control - a gate that blocks everything proves nothing."""
        gate = Gate([DenyUnlessDeclared(reversible=frozenset({"fam_send_message"}))],
                    ledger=Ledger())
        ex = GatedExecutor(gate, self.src.callables())
        outcome = ex.execute("fam_send_message", {"text": "hi"})
        self.assertTrue(outcome.allowed, outcome.decision.reason)
        self.assertEqual(len(self.session.calls), 1)

    def test_gate_module_needs_no_mcp_awareness(self):
        """`gate.py` must not have grown an MCP concept. If it had, the decision
        point would be entangled with the transport."""
        import overmind.gate as gate_module
        with open(gate_module.__file__, encoding="utf-8") as fh:
            source = fh.read().lower()
        for term in ("mcp", "clientsession", "stdio", "jsonrpc"):
            self.assertNotIn(term, source, f"gate.py mentions {term!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestToolArgumentNameCollision(unittest.TestCase):
    """🔴 A tool whose own argument is called `name` - `fam_create_channel(name=)`
    is exactly one - collided with `call`'s positional parameter and raised
    "got multiple values for argument 'name'" on EVERY invocation.

    ⚠️ The failure was INVISIBLE in the agent loop: the exception was caught,
    recorded, and handed back to the model as the tool's result, so the model
    saw a broken tool and retried. MEASUREMENTS.md §19 attributed that retrying
    to a multi-turn limitation of the MODEL. It was mine.
    """

    def setUp(self):
        self.session = FakeSession("channel created")
        self.tools = [Obj(name="fam_create_channel", description="create",
                          input_schema={"type": "object",
                                        "properties": {"name": {"type": "string"}},
                                        "required": ["name"]})]
        self.src = source_with(self.session, self.tools)

    def tearDown(self):
        self.src.close()

    def test_a_tool_argument_called_name_does_not_collide(self):
        out = self.src.call("fam_create_channel", name="general")
        self.assertEqual(out, "channel created")
        self.assertEqual(self.session.calls, [("fam_create_channel", {"name": "general"})])

    def test_the_bound_callable_passes_name_through(self):
        """The path the agent loop actually takes."""
        invoke = self.src.callables()["fam_create_channel"]
        self.assertEqual(invoke(name="general"), "channel created")
        self.assertEqual(self.session.calls[-1][1], {"name": "general"})

    def test_other_reserved_looking_arguments_also_survive(self):
        for arg in ("name", "self", "arguments"):
            with self.subTest(arg=arg):
                self.src.call("fam_create_channel", **{arg: "x"})
                self.assertEqual(self.session.calls[-1][1], {arg: "x"})
