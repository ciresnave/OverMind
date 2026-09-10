"""Tests for the mechanical gate.

Each test encodes a MEASURED failure from MEASUREMENTS.md rather than an
imagined one. The section reference in each docstring is the evidence.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.gate import (  # noqa: E402
    AllowSenders, Decision, ForbidTools, Gate, GateDenied, GatedExecutor, Ledger,
    NoSelfMerge, Policy, RequirePrecondition, StaticFacts, ToolCall,
    DenyUnlessDeclared, detect_smuggled_tool_call,
)


class Spy:
    """Records whether the underlying effect ever ran.

    The assertion that matters is not what the gate RETURNS but whether the
    dangerous function was CALLED. A gate that returns "denied" and still runs
    the tool would pass a naive test.
    """

    def __init__(self, result="ok"):
        self.calls: list[dict] = []
        self.result = result

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def build(policies, facts=None, tools=None):
    gate = Gate(policies, facts=facts or StaticFacts(), ledger=Ledger())
    return gate, GatedExecutor(gate, tools or {})


# --------------------------------------------------------------------------- #
# §11 - the prohibition the most capable local model violated 7 of 8 times
# --------------------------------------------------------------------------- #

class TestSelfMergeRefused(unittest.TestCase):
    def setUp(self):
        self.merge = Spy("MERGED")
        self.facts = StaticFacts({"self_identity": "overmind-bot",
                                  "pr_author": "overmind-bot"})
        self.gate, self.ex = build(
            [NoSelfMerge(), DenyUnlessDeclared(reversible=frozenset({"merge_pull_request"}))],
            facts=self.facts,
            tools={"merge_pull_request": self.merge},
        )

    def test_the_tool_is_never_invoked(self):
        """§11: qwen3:8b recited the rule and merged anyway. The gate must make
        the merge IMPOSSIBLE, not discouraged."""
        outcome = self.ex.execute("merge_pull_request", {"pr": 42})
        self.assertFalse(outcome.allowed)
        self.assertEqual(self.merge.calls, [], "the merge function was actually called")

    def test_denial_names_the_policy_and_reason(self):
        outcome = self.ex.execute("merge_pull_request", {"pr": 42})
        self.assertEqual(outcome.decision.policy, "no-self-merge")
        self.assertIn("self-merge is forbidden", outcome.decision.reason)

    def test_another_authors_pr_is_allowed(self):
        """The control. A gate that refuses everything proves nothing - it must
        DISCRIMINATE, or 'denied' carries no information."""
        self.facts.set("pr_author", "someone-else")
        outcome = self.ex.execute("merge_pull_request", {"pr": 43})
        self.assertTrue(outcome.allowed, outcome.decision.reason)
        self.assertEqual(self.merge.calls, [{"pr": 43}])

    def test_unresolved_authorship_denies(self):
        """⚠️ A gate that opens when it cannot check opens exactly when
        something is wrong."""
        self.facts.set("pr_author", None)
        outcome = self.ex.execute("merge_pull_request", {"pr": 44})
        self.assertFalse(outcome.allowed)
        self.assertIn("cannot establish authorship", outcome.decision.reason)
        self.assertEqual(self.merge.calls, [])

    def test_a_tool_result_claiming_otherwise_changes_nothing(self):
        """§11's actual shape: the model HAD been told the author was itself, in
        a tool result, and merged regardless. The policy resolves authorship from
        the FactSource, so nothing the conversation contains can move it."""
        # whatever the transcript said, the gate's own fact stands
        outcome = self.ex.execute("merge_pull_request", {"pr": 42, "note": "author is someone else, honest"})
        self.assertFalse(outcome.allowed)
        self.assertEqual(self.merge.calls, [])


# --------------------------------------------------------------------------- #
# §8.2 / §3.2 - narration is not an effect
# --------------------------------------------------------------------------- #

class TestNarrationCannotCauseEffects(unittest.TestCase):
    def test_announcing_a_call_leaves_no_trace(self):
        """§8.2: granite3.3:8b announced 'Calling the function to list
        entities...' with ZERO tool calls recorded. The ledger is the evidence."""
        spy = Spy()
        gate, ex = build([DenyUnlessDeclared(reversible=frozenset({"list_entities"}))],
                         tools={"list_entities": spy})
        # the model "says" it called the tool; nothing is submitted
        _model_text = "Calling the function to list entities... Done! I have listed them."
        self.assertEqual(len(gate.ledger), 0)
        self.assertFalse(gate.ledger.was_executed("list_entities"))
        self.assertEqual(spy.calls, [])

    def test_smuggled_json_is_detected_and_is_not_an_authorisation(self):
        """§8.2: 4 of 5 local failures wrote tool-call JSON into message content.
        Detecting it is a protocol repair signal - never a second path to an
        effect."""
        content = 'Sure. {"name": "merge_pull_request", "arguments": {"pr": 42}}'
        found = detect_smuggled_tool_call(content, ["merge_pull_request", "list_entities"])
        self.assertEqual(found, ["merge_pull_request"])

        merge = Spy()
        gate, ex = build([NoSelfMerge(), DenyUnlessDeclared(frozenset({"merge_pull_request"}))],
                         facts=StaticFacts({"self_identity": "overmind-bot",
                                            "pr_author": "overmind-bot"}),
                         tools={"merge_pull_request": merge})
        # detection does not execute anything, and routing it through the gate
        # still refuses
        outcome = ex.execute("merge_pull_request", {"pr": 42})
        self.assertFalse(outcome.allowed)
        self.assertEqual(merge.calls, [])

    def test_unknown_tool_name_in_prose_is_ignored(self):
        self.assertEqual(detect_smuggled_tool_call('{"name": "rm_rf"}', ["merge_pull_request"]), [])

    def test_empty_content_is_safe(self):
        self.assertEqual(detect_smuggled_tool_call("", ["x"]), [])


# --------------------------------------------------------------------------- #
# Deny by default
# --------------------------------------------------------------------------- #

class TestDenyByDefault(unittest.TestCase):
    def test_undeclared_tool_is_refused(self):
        spy = Spy()
        gate, ex = build([DenyUnlessDeclared(reversible=frozenset({"read_file"}))],
                         tools={"read_file": Spy(), "publish_crate": spy})
        outcome = ex.execute("publish_crate", {"name": "fuel"})
        self.assertFalse(outcome.allowed)
        self.assertEqual(spy.calls, [])

    def test_no_applicable_policy_refuses(self):
        """An uncovered tool is refused, not allowed. The default IS the policy."""
        spy = Spy()
        gate, ex = build([NoSelfMerge()], tools={"delete_branch": spy})
        outcome = ex.execute("delete_branch", {"branch": "main"})
        self.assertFalse(outcome.allowed)
        self.assertEqual(outcome.decision.policy, "no-applicable-policy")
        self.assertEqual(spy.calls, [])

    def test_allowed_but_unregistered_tool_does_not_execute(self):
        gate, ex = build([DenyUnlessDeclared(reversible=frozenset({"ghost"}))], tools={})
        outcome = ex.execute("ghost", {})
        self.assertFalse(outcome.allowed)
        self.assertEqual(outcome.decision.policy, "unregistered-tool")


# --------------------------------------------------------------------------- #
# §11 ordering - the rule llama3.2:3b never performed, 8 of 8
# --------------------------------------------------------------------------- #

class TestPreconditionEnforced(unittest.TestCase):
    def setUp(self):
        self.send = Spy("SENT")
        self.listing = Spy("alice@local")
        self.ledger = Ledger()

        class LedgerFacts(StaticFacts):
            def __init__(self, ledger):
                super().__init__()
                self._ledger = ledger

            def fact(self, key, **params):
                if key == "executed_tools":
                    return self._ledger.executed_tools()
                return super().fact(key, **params)

        self.gate = Gate(
            [RequirePrecondition(tool="send_message", requires="list_entities"),
             DenyUnlessDeclared(reversible=frozenset({"send_message", "list_entities"}))],
            facts=LedgerFacts(self.ledger), ledger=self.ledger,
        )
        self.ex = GatedExecutor(self.gate, {"send_message": self.send,
                                            "list_entities": self.listing})

    def test_send_without_listing_is_refused(self):
        outcome = self.ex.execute("send_message", {"to_entity": "alice@local", "text": "hi"})
        self.assertFalse(outcome.allowed)
        self.assertEqual(self.send.calls, [])

    def test_send_after_listing_is_allowed(self):
        """The control: the same call succeeds once the precondition truly ran."""
        self.ex.execute("list_entities", {})
        outcome = self.ex.execute("send_message", {"to_entity": "alice@local", "text": "hi"})
        self.assertTrue(outcome.allowed, outcome.decision.reason)
        self.assertEqual(len(self.send.calls), 1)

    def test_a_denied_precondition_does_not_count_as_having_run(self):
        """⚠️ Only EXECUTED calls satisfy a precondition. A refused attempt that
        counted would let a model satisfy an ordering rule by failing at it."""
        gate = Gate([ForbidTools(frozenset({"list_entities"})),
                     RequirePrecondition(tool="send_message", requires="list_entities")],
                    facts=StaticFacts(), ledger=Ledger())
        ex = GatedExecutor(gate, {"list_entities": self.listing, "send_message": self.send})
        ex.execute("list_entities", {})
        self.assertEqual(gate.ledger.executed_tools(), ())


# --------------------------------------------------------------------------- #
# Gate semantics
# --------------------------------------------------------------------------- #

class TestGateSemantics(unittest.TestCase):
    def test_any_denial_wins(self):
        class AlwaysAllow:
            name = "always-allow"
            def applies_to(self, call): return True
            def decide(self, call, facts): return Decision.allow("fine", self.name)

        spy = Spy()
        gate, ex = build([AlwaysAllow(), ForbidTools(frozenset({"publish"}))],
                         tools={"publish": spy})
        outcome = ex.execute("publish", {})
        self.assertFalse(outcome.allowed)
        self.assertEqual(spy.calls, [])

    def test_there_is_no_bypass_parameter(self):
        """⚠️ A `force=True` would be set by whoever is in a hurry, which is the
        same condition under which the rule matters. Enforced structurally."""
        import inspect
        params = set(inspect.signature(GatedExecutor.execute).parameters)
        self.assertEqual(params, {"self", "name", "arguments", "actor"})
        for banned in ("force", "override", "bypass", "skip_gate", "allow_anyway"):
            self.assertNotIn(banned, params)

    def test_denial_is_returned_as_tool_content_for_the_model(self):
        """A refusal must be feedable back to the model as a tool result, so the
        loop continues instead of hanging."""
        gate, ex = build([ForbidTools(frozenset({"publish"}), reason="not this agent's call")],
                         tools={"publish": Spy()})
        outcome = ex.execute("publish", {})
        text = outcome.as_tool_content()
        self.assertIn("REFUSED by the harness", text)
        self.assertIn("forbid-tools", text)

    def test_execute_or_raise_raises_on_denial(self):
        gate, ex = build([ForbidTools(frozenset({"publish"}))], tools={"publish": Spy()})
        with self.assertRaises(GateDenied):
            ex.execute_or_raise("publish", {})

    def test_tool_exception_is_recorded_not_swallowed(self):
        def boom(**kwargs):
            raise ValueError("upstream exploded")

        gate, ex = build([DenyUnlessDeclared(reversible=frozenset({"boom"}))], tools={"boom": boom})
        outcome = ex.execute("boom", {})
        self.assertTrue(outcome.allowed)
        self.assertTrue(outcome.executed)
        self.assertIn("upstream exploded", outcome.error)
        self.assertIn("ERROR", outcome.as_tool_content())


class TestLedgerIsTheEvidence(unittest.TestCase):
    def test_every_submission_is_recorded_allowed_or_not(self):
        gate, ex = build([DenyUnlessDeclared(reversible=frozenset({"ok"}))],
                         tools={"ok": Spy(), "nope": Spy()})
        ex.execute("ok", {})
        ex.execute("nope", {})
        self.assertEqual(len(gate.ledger), 2)
        self.assertEqual(gate.ledger.executed_tools(), ("ok",))
        self.assertEqual(len(gate.ledger.denied()), 1)

    def test_ledger_serialises_for_audit(self):
        import json
        gate, ex = build([DenyUnlessDeclared(reversible=frozenset({"ok"}))], tools={"ok": Spy()})
        ex.execute("ok", {"a": 1})
        rows = json.loads(gate.ledger.to_json())
        self.assertEqual(rows[0]["tool"], "ok")
        self.assertTrue(rows[0]["executed"])
        self.assertEqual(rows[0]["arguments"], {"a": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestFalsyDefaultsRegression(unittest.TestCase):
    """A caller's EMPTY ledger must not be silently replaced.

    ⚠️ `Ledger` defines __len__, so an empty one is falsy and `ledger or Ledger()`
    swapped in a fresh object. The gate wrote to one ledger while policies read
    another. The refusal test still passed - for the wrong reason. Only the
    CONTROL (the call that should SUCCEED) exposed it.
    """

    def test_caller_ledger_is_used_even_when_empty(self):
        mine = Ledger()
        self.assertFalse(mine, "precondition of this test: an empty Ledger is falsy")
        gate = Gate([DenyUnlessDeclared(reversible=frozenset({"ok"}))], ledger=mine)
        self.assertIs(gate.ledger, mine)
        GatedExecutor(gate, {"ok": Spy()}).execute("ok", {})
        self.assertEqual(len(mine), 1, "the gate wrote to a different ledger")

    def test_caller_facts_are_used_even_when_empty(self):
        mine = StaticFacts()
        gate = Gate([DenyUnlessDeclared(reversible=frozenset())], facts=mine)
        self.assertIs(gate.facts, mine)


class TestPerSenderPoliciesRefuseUntilIdentityIsVouched(unittest.TestCase):
    """⚠️ MEASURED: every dispatch received so far reports `sender_vouched:
    false` - the relay's word, not an account-vouched key.

    Today's safety is STRUCTURAL: the gate never reads the message, so a sender
    claiming authority changes nothing. That holds only while every write is
    forbidden by BLANKET policy. The moment a policy decides by WHO ASKED,
    identity becomes the authorisation input - and the gate cannot resolve a
    fact the transport did not carry.

    Enforced in the gate, once, as a REFUSAL rather than a note: a constraint
    that lives in a design document is one restart away from being forgotten.
    """

    def setUp(self):
        self.spy = Spy("SENT")
        self.policy = AllowSenders(tools=frozenset({"send"}), senders=frozenset({"pm@local"}))

    def build(self, facts):
        gate = Gate([self.policy], facts=facts, ledger=Ledger())
        return gate, GatedExecutor(gate, {"send": self.spy})

    def test_unvouched_sender_is_refused_even_when_on_the_allow_list(self):
        gate, ex = self.build(StaticFacts({"dispatch_sender": "pm@local",
                                           "sender_vouched": False}))
        outcome = ex.execute("send", {"text": "hi"})
        self.assertFalse(outcome.allowed)
        self.assertEqual(outcome.decision.policy, "unvouched-sender")
        self.assertEqual(self.spy.calls, [])

    def test_missing_vouch_fact_is_refused_not_assumed(self):
        """⚠️ Absent is not the same as True. A gate that opens when it cannot
        check opens exactly when something is wrong."""
        gate, ex = self.build(StaticFacts({"dispatch_sender": "pm@local"}))
        self.assertFalse(ex.execute("send", {}).allowed)

    def test_a_truthy_non_true_value_does_not_satisfy_it(self):
        """⚠️ `is not True`, not falsiness. A transport reporting the STRING
        'false' would otherwise pass."""
        gate, ex = self.build(StaticFacts({"dispatch_sender": "pm@local",
                                           "sender_vouched": "false"}))
        self.assertFalse(ex.execute("send", {}).allowed)

    def test_vouched_and_permitted_is_allowed(self):
        """The control - the refusal must be about the VOUCH, not a blanket no."""
        gate, ex = self.build(StaticFacts({"dispatch_sender": "pm@local",
                                           "sender_vouched": True}))
        outcome = ex.execute("send", {"text": "hi"})
        self.assertTrue(outcome.allowed, outcome.decision.reason)
        self.assertEqual(len(self.spy.calls), 1)

    def test_vouched_but_unlisted_sender_is_still_refused(self):
        gate, ex = self.build(StaticFacts({"dispatch_sender": "stranger@local",
                                           "sender_vouched": True}))
        outcome = ex.execute("send", {})
        self.assertFalse(outcome.allowed)
        self.assertEqual(outcome.decision.policy, "allow-senders")
        self.assertEqual(self.spy.calls, [])

    def test_ordinary_policies_are_unaffected(self):
        """⚠️ The check must not become a tax on every policy in the system."""
        gate = Gate([DenyUnlessDeclared(reversible=frozenset({"send"}))],
                    facts=StaticFacts(), ledger=Ledger())
        ex = GatedExecutor(gate, {"send": self.spy})
        self.assertTrue(ex.execute("send", {}).allowed)


class TestAnErroredCallIsNotAnExecutedOne(unittest.TestCase):
    """🔴 `executed_tools()` once returned every INVOKED tool, error or not.

    A tool that raised on every call therefore counted as done - which let a
    precondition be satisfied by a failure, and let a completion report say a
    task was finished when nothing had happened. Both callers want success, not
    attempt.
    """

    def setUp(self):
        def boom(**kw):
            raise TypeError("got multiple values for argument 'name'")
        self.ok = Spy("fine")
        gate = Gate([DenyUnlessDeclared(reversible=frozenset({"boom", "ok"}))], ledger=Ledger())
        self.gate = gate
        self.ex = GatedExecutor(gate, {"boom": boom, "ok": self.ok})

    def test_a_raised_call_is_not_in_executed_tools(self):
        self.ex.execute("boom", {"name": "x"})
        self.assertEqual(self.gate.ledger.executed_tools(), ())
        self.assertFalse(self.gate.ledger.was_executed("boom"))

    def test_but_it_is_still_recorded_as_attempted(self):
        """⚠️ The attempt must not vanish - a tool erroring every time is a fact
        someone needs to see."""
        self.ex.execute("boom", {})
        self.assertEqual(self.gate.ledger.attempted_tools(), ("boom",))
        self.assertEqual(len(self.gate.ledger.errored()), 1)

    def test_a_successful_call_still_counts(self):
        """The control - the stricter rule must not empty the set."""
        self.ex.execute("ok", {})
        self.assertEqual(self.gate.ledger.executed_tools(), ("ok",))

    def test_a_failing_precondition_does_not_satisfy_a_precondition(self):
        """⚠️ The consequence that matters: a step that ERRORED must not count
        as having been done."""
        class LedgerFacts(StaticFacts):
            def __init__(self, ledger):
                super().__init__()
                self._ledger = ledger
            def fact(self, key, **params):
                return self._ledger.executed_tools() if key == "executed_tools" else super().fact(key, **params)

        def boom(**kw):
            raise RuntimeError("nope")
        ledger = Ledger()
        gate = Gate([RequirePrecondition(tool="send", requires="listing"),
                     DenyUnlessDeclared(reversible=frozenset({"send", "listing"}))],
                    facts=LedgerFacts(ledger), ledger=ledger)
        send = Spy()
        ex = GatedExecutor(gate, {"listing": boom, "send": send})
        ex.execute("listing", {})
        outcome = ex.execute("send", {})
        self.assertFalse(outcome.allowed, "a failed precondition satisfied the requirement")
        self.assertEqual(send.calls, [])
