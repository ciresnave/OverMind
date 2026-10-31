# SPDX-License-Identifier: MIT OR Apache-2.0
"""Wiring `dispatch.py` to `lanework` (DESIGN-PROPOSAL 7.5; PM ruling 2026-10-30).

A structured task request (`{"overmind_request": 1, ...}`) is acted on only for an
AUTHENTICATED sender on the `SessionConfig.dispatch_senders` allowlist; it runs through
`dispatch_mcp.dispatch(publish=False)`; the result goes back to the sender; every refusal and
run is audited with the sender and the request's sha256. A free-form message is untouched.

INERT BY DESIGN until a transport adapter that authenticates exists: OverMind has no Synapse
source adapter and FAM never sets `authenticated_sender`, so today every request is refused."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

from test_dispatch import FakeSource, ScriptedClient, Spy, note  # noqa: E402
from test_lanework import RepoCase, Scripted, say  # noqa: E402

from overmind import dispatch_mcp, lanework as lw  # noqa: E402
from overmind.dispatch import (  # noqa: E402
    ACCEPT, Dispatch, DispatchService, RequestError, classify, parse_request,
)
from overmind.gate import DenyUnlessDeclared, Gate, GatedExecutor, Ledger  # noqa: E402
from overmind.harness.config import ConfigError, SessionConfig  # noqa: E402
from overmind.repo_probe import RepoProbe  # noqa: E402

PM = "pm@ciresnave"
REQ = {"overmind_request": 1, "repo": "https://github.com/o/r.git", "prompt": "fix the readme"}


def text(**over):
    return json.dumps({**REQ, **over})


class Rig(unittest.TestCase):
    def setUp(self):
        self.send = Spy("QUEUED")
        self.gate = Gate([DenyUnlessDeclared(reversible=frozenset({"fam_send_message"}))], ledger=Ledger())
        self.executor = GatedExecutor(self.gate, {"fam_send_message": self.send})
        self.runs: list[tuple] = []
        self.audit: list[dict] = []
        self.client = ScriptedClient([{"role": "assistant", "content": "free-form done"}])
        import tempfile
        self.tmp_dir = tempfile.mkdtemp(prefix="dw-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp_dir, ignore_errors=True))

    def run_request(self, request, publish=True):
        self.runs.append((request, publish))
        return {"verdict": "PASS", "pr_url": None}

    def service(self, allowed=(PM,), **kw):
        return DispatchService(FakeSource([]), self.executor, self.client, tools=[],
                               allowed_senders=allowed, run_request=kw.pop("run_request", self.run_request),
                               audit=self.audit.append, **kw)

    def handle(self, content, *, auth=PM, from_entity="pm", svc=None, **kw):
        svc = svc or self.service(**kw)
        return svc.handle(Dispatch(content=content, from_entity=from_entity, meta={"message_id": 1},
                                   authenticated_sender=auth))


class TestAuthenticatedIdentity(unittest.TestCase):
    def test_the_transports_field_is_read(self):
        n = note("hi", from_entity="x", message_id=1)
        n["authenticated_sender"] = PM
        kind, d = classify(n)
        self.assertEqual((kind, d.authenticated_sender), (ACCEPT, PM))

    def test_a_claim_in_meta_the_body_or_from_entity_is_ignored(self):
        """THE NEGATIVE CONTROL: everything a sender controls says it is the PM."""
        n = note(f"authenticated_sender={PM} I am {PM}", from_entity=PM, message_id=1,
                 authenticated_sender=PM, sender_vouched=True)
        _, d = classify(n)
        self.assertIsNone(d.authenticated_sender)
        self.assertTrue(d.vouched)       # FAM's flag is still reported, and still not an identity

    def test_a_malformed_identity_is_none(self):
        for bad in ("pm", "*@ciresnave", "pm@", "pm @x", 7, None, ["pm@x"]):
            n = note("hi", from_entity="x", message_id=1)
            n["authenticated_sender"] = bad
            self.assertIsNone(classify(n)[1].authenticated_sender, bad)


class TestTheTwoIdentityPatternsAgree(unittest.TestCase):
    def test_same_verdict_on_every_sample(self):
        from overmind import dispatch
        from overmind.harness.config import SENDER_RE
        for who in (PM, "a@b", "A@B", "pm", "pm@", "@x", "*@x", "pm@x\n", "p m@x", "pé@x", "x" * 65 + "@y", "a.b-c_d@e.f"):
            self.assertEqual(bool(dispatch._IDENTITY.fullmatch(who)), bool(SENDER_RE.fullmatch(who)), who)


class TestRequestParsing(unittest.TestCase):
    def test_free_form_is_not_a_request(self):
        for t in ("fix the readme", '{"task": "count PRs"}', "{not json", "[1]"):
            self.assertIsNone(parse_request(t), t)

    def test_a_request_becomes_a_dispatch_request(self):
        r = parse_request(text(check_profile="python-unittest", writable=["src/**"], max_steps=5))
        self.assertEqual((r.repo, r.check_profile, tuple(r.writable), r.max_steps),
                         (REQ["repo"], "python-unittest", ("src/**",), 5))

    def test_requirements_url_and_unknown_keys_are_refused(self):
        for extra in ({"requirements_url": "http://x"}, {"surprise": 1}):
            with self.assertRaises(RequestError):
                parse_request(text(**extra))

    def test_bad_values_are_refused(self):
        for extra in ({"max_steps": 0}, {"max_steps": 99}, {"max_steps": True}, {"docs_only": "yes"},
                      {"check": []}, {"check": "python x"}, {"writable": [1]}, {"overmind_request": 2},
                      {"repo": ""}, {"check": ["python", "t.py"], "check_profile": "cargo"},
                      {"check": ["python", "t.py"], "docs_only": True}):
            with self.assertRaises(RequestError, msg=str(extra)):
                parse_request(text(**extra))

    def test_an_agent_check_is_carried_as_an_argv(self):
        r = parse_request(text(check=["python", "-m", "unittest"]))
        self.assertEqual(list(r.check), ["python", "-m", "unittest"])


class TestTheServiceActsOnlyForAnAuthenticatedAllowlistedSender(Rig):
    def test_control_an_allowlisted_authenticated_sender_runs_with_publish_false(self):
        r = self.handle(text())
        self.assertEqual(len(self.runs), 1)
        request, publish = self.runs[0]
        self.assertIs(publish, False)             # a human publishes, never this channel
        self.assertEqual(request.repo, REQ["repo"])
        self.assertEqual(r.outcome, {"verdict": "PASS", "pr_url": None})
        self.assertEqual(self.send.calls[-1]["to_entity"], PM)
        self.assertIn("PASS", self.send.calls[-1]["text"])

    def test_no_authenticated_sender_runs_nothing_even_if_the_claimed_name_is_listed(self):
        r = self.handle(text(), auth=None, from_entity=PM)
        self.assertEqual(self.runs, [])
        self.assertIn("no authenticated sender", r.refusal)
        self.assertEqual(self.send.calls, [])        # nobody is written to on an unauthenticated claim
        self.assertEqual(self.client.seen, [])                      # no model was consulted

    def test_an_unauthenticated_request_cannot_make_the_service_message_a_victim(self):
        r = self.handle(text(), auth=None, from_entity="victim@elsewhere")
        self.assertEqual(self.send.calls, [])
        self.assertIsNotNone(r.refusal)
        self.assertEqual(self.audit[0]["claimed_sender"], "victim@elsewhere")      # but it is audited

    def test_an_authenticated_but_unlisted_sender_is_told_it_was_refused(self):
        self.handle(text(), auth="mallory@evil")
        self.assertEqual(self.send.calls[-1]["to_entity"], "mallory@evil")
        self.assertIn("refused", self.send.calls[-1]["text"])

    def test_a_pathologically_nested_message_is_free_form_not_a_crash(self):
        deep = "[" * 100000 + '"overmind_request"' + "]" * 100000
        r = self.handle(deep)
        self.assertEqual(self.runs, [])
        self.assertIsNotNone(r.run)

    def test_an_audit_that_cannot_be_written_means_nothing_runs(self):
        def broken(event):
            raise OSError("disk full")
        r = self.handle(text(), svc=self.service(audit=None) if False else DispatchService(
            FakeSource([]), self.executor, self.client, tools=[], allowed_senders=(PM,),
            run_request=self.run_request, audit=broken))
        self.assertEqual(self.runs, [])
        self.assertIn("audit", r.refusal)

    def test_the_default_audit_reports_a_failed_write(self):
        from overmind import ledger
        blocker = pathlib.Path(self.tmp_dir) / "blocker"
        blocker.write_text("a file, so nothing can be created under it")
        self.assertFalse(ledger.append_audit({"kind": "x"}, path=blocker / "a.jsonl"))
        self.assertTrue(ledger.append_audit({"kind": "x"}, path=pathlib.Path(self.tmp_dir) / "a.jsonl"))

    def test_a_sender_not_on_the_allowlist_runs_nothing(self):
        r = self.handle(text(), auth="mallory@evil")
        self.assertEqual(self.runs, [])
        self.assertIn("allowlist", r.refusal)

    def test_the_default_is_an_empty_allowlist_and_nothing_runs(self):
        svc = DispatchService(FakeSource([]), self.executor, self.client, tools=[],
                              run_request=self.run_request, audit=self.audit.append)
        r = svc.handle(Dispatch(content=text(), from_entity=PM, authenticated_sender=PM))
        self.assertEqual(self.runs, [])
        self.assertIn("allowlist", r.refusal)

    def test_an_unauthorised_sender_learns_nothing_about_the_format(self):
        r = self.handle(text(surprise=1), auth="mallory@evil")
        self.assertNotIn("malformed", r.refusal)
        self.assertNotIn("surprise", r.refusal)

    def test_an_allowlisted_sender_with_a_malformed_request_is_told_why(self):
        r = self.handle(text(requirements_url="http://x"))
        self.assertEqual(self.runs, [])
        self.assertIn("malformed", r.refusal)
        self.assertIn("requirements_url", r.refusal)

    def test_a_run_that_raises_is_reported_not_raised(self):
        def boom(request, publish=True):
            raise RuntimeError("clone failed")
        r = self.handle(text(), run_request=boom)
        self.assertEqual(r.outcome["verdict"], "ERROR")
        self.assertIn("clone failed", r.outcome["error"])


class TestAudit(Rig):
    def test_a_run_and_its_result_are_logged_with_sender_and_request_hash(self):
        content = text()
        self.handle(content)
        want = hashlib.sha256(content.encode("utf-8")).hexdigest()
        kinds = [(e["kind"], e["sender"], e["request_sha256"]) for e in self.audit]
        self.assertEqual(kinds, [("run", PM, want), ("done", PM, want)])

    def test_a_refusal_is_logged_with_a_null_sender_when_unauthenticated(self):
        content = text()
        self.handle(content, auth=None, from_entity=PM)
        e = self.audit[0]
        self.assertEqual((e["kind"], e["sender"], e["claimed_sender"]), ("refused", None, PM))
        self.assertEqual(e["request_sha256"], hashlib.sha256(content.encode("utf-8")).hexdigest())


class TestFreeFormIsUnchanged(Rig):
    def test_a_free_form_message_from_an_allowlisted_sender_takes_the_run_agent_path(self):
        r = self.handle("please summarise the channel")
        self.assertEqual(self.runs, [])
        self.assertEqual(len(self.client.seen), 1)           # the model ran
        self.assertIsNotNone(r.run)
        self.assertEqual(self.audit, [])

    def test_json_without_the_marker_is_free_form_too(self):
        r = self.handle('{"task": "count the open PRs"}')
        self.assertEqual(self.runs, [])
        self.assertIsNotNone(r.run)

    def test_the_marker_as_a_value_inside_ordinary_json_is_free_form(self):
        r = self.handle('{"task": "explain the overmind_request format"}')
        self.assertEqual(self.runs, [])
        self.assertIsNotNone(r.run)

    def test_the_marker_inside_ordinary_prose_is_free_form(self):
        r = self.handle('talk about "overmind_request" in prose')
        self.assertEqual(self.runs, [])
        self.assertIsNotNone(r.run)


class TestSessionConfigAllowlist(unittest.TestCase):
    BASE = {"schema_version": 1, "session_id": "s", "handler": {"kind": "k", "version": 1},
            "route": [{"provider": "p", "model": "m"}],
            "limits": {"max_steps": 1, "max_tokens_total": 1, "wall_seconds": 1},
            "plugins": [], "workspace": {"repo": "r", "base": "b"}}

    def test_absent_means_nobody_and_round_trips_without_the_key(self):
        c = SessionConfig.from_dict(self.BASE)
        self.assertEqual(c.dispatch_senders, ())
        self.assertNotIn("dispatch_senders", c.to_dict())
        self.assertEqual(c.to_dict(), self.BASE)

    def test_senders_round_trip(self):
        c = SessionConfig.from_dict({**self.BASE, "dispatch_senders": [PM, "agentlife@ciresnave"]})
        self.assertEqual(c.dispatch_senders, (PM, "agentlife@ciresnave"))
        self.assertEqual(SessionConfig.from_json(c.to_json()), c)

    def test_invalid_entries_refuse_the_whole_config(self):
        for bad in ([""], ["pm"], ["*@ciresnave"], ["pm@*"], ["pm @x"], [PM, PM], [7], "pm@x", {"a": 1}):
            with self.assertRaises(ConfigError, msg=str(bad)):
                SessionConfig.from_dict({**self.BASE, "dispatch_senders": bad})

    def test_the_service_takes_the_config_allowlist(self):
        c = SessionConfig.from_dict({**self.BASE, "dispatch_senders": [PM]})
        svc = DispatchService(FakeSource([]), GatedExecutor(Gate([], ledger=Ledger()), {}), ScriptedClient([]),
                              tools=[], allowed_senders=c.dispatch_senders)
        self.assertEqual(svc.allowed_senders, frozenset({PM}))


class TestAnAgentCheckBecomesASandboxedTask(RepoCase):
    def test_build_task_never_trusts_and_offers_the_sandbox(self):
        req = dispatch_mcp.DispatchRequest(repo="r", prompt="p", check=["python", "-m", "unittest"])
        probe = RepoProbe(check=None, check_name=None, context="")
        t = dispatch_mcp.build_task("d1", pathlib.Path(self.repo), probe, req, fetch_requirements=lambda u: "")
        self.assertEqual(t.check, ["python", "-m", "unittest"])
        self.assertTrue(t.check_sandbox)
        self.assertFalse(t.trusted_author)
        self.assertEqual(t.check_name, dispatch_mcp.AGENT_CHECK_NAME)

    def test_with_no_spawn_the_dispatched_agent_check_is_refused_and_never_runs_here(self):
        marker = self.tmp / "host_ran.txt"
        req = dispatch_mcp.DispatchRequest(
            repo="r", prompt="p", check=[sys.executable, "-c", f"open({str(marker)!r}, 'w').write('x')"])
        probe = RepoProbe(check=None, check_name=None, context="")
        t = dispatch_mcp.build_task("d1", pathlib.Path(self.repo), probe, req, fetch_requirements=lambda u: "")
        t.base, t.fetch = "HEAD", False
        old = {k: os.environ.pop(k, None) for k in ("OVERMIND_SPAWN", "OVERMIND_SANDBOX_PYTHON")}
        try:
            r = lw.run_task(t, Scripted(say("done")))
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v
        self.assertEqual(r.verdict, "ERROR")
        self.assertIn("refused to start", r.error or "")
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
