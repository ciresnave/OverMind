# SPDX-License-Identifier: MIT OR Apache-2.0
"""The Synapse SOURCE adapter for `DispatchService` (ROADMAP A3 R4; PM ruling 2026-10-30).

It reads the `synapse inbox` lines of Synapse as it is today and sets the notification's top-level
`authenticated_sender` from the line's `from`, the sender's global id as the DAEMON verified it.
Claims, each with a control:
  * `from` becomes `authenticated_sender`, and a request from an allowlisted sender runs;
  * NEGATIVE CONTROL: a body, `from_summary`, `from_cwd`, `from_entity` or an extra line field that
    claims an `authenticated_sender` changes nothing: the adapter has one source for it;
  * a line with no usable `from` authenticates nobody, so the service refuses and runs nothing;
  * each message is acked once, before it is handed over, and a redelivery is not handed over twice;
  * a CLI that fails, prints junk, or sends a non-text body loses no message silently and raises nothing;
  * `SessionConfig.dispatch_senders` reaches the service's allowlist.
"""

from __future__ import annotations

import base64
import itertools
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

from test_dispatch import Spy  # noqa: E402
from test_dispatch import ScriptedClient  # noqa: E402

from overmind.dispatch import ACCEPT, Dispatch, DispatchService, classify  # noqa: E402
from overmind.gate import DenyUnlessDeclared, Gate, GatedExecutor, Ledger  # noqa: E402
from overmind.harness.config import SessionConfig  # noqa: E402
from overmind.synapse_source import SynapseSource, service_from_config  # noqa: E402

PM = "pm@ciresnave"
MALLORY = "mallory@evil"
REQ = {"overmind_request": 1, "repo": "https://github.com/o/r.git", "prompt": "fix the readme"}


_COUNTER = itertools.count(1)


def line(body, *, frm=PM, mid=None, **extra):
    raw = body if isinstance(body, bytes) else body.encode("utf-8")
    out = {"message_id": mid or f"{frm}/m-{next(_COUNTER)}", "from": frm,
           "body_b64": base64.b64encode(raw).decode("ascii"), "attempts": 1, **extra}
    try:
        out["body"] = raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    return json.dumps(out, ensure_ascii=False)       # as the CLI prints it: U+2028 stays raw


class FakeCli:
    """Stands in for the `synapse` binary: `inbox` prints its queued lines, `ack` and `send` record."""

    def __init__(self, inbox=(), *, inbox_code=0, ack_code=0, send_out="queued", send_code=0):
        self.inbox = list(inbox)
        self.inbox_code, self.ack_code, self.send_out = inbox_code, ack_code, send_out
        self.send_code = send_code
        self.calls: list[tuple] = []

    def __call__(self, args, stdin=None):
        self.calls.append((tuple(args), stdin))
        verb = args[1]
        if verb == "inbox":
            out, self.inbox = "\n".join(self.inbox), []
            return self.inbox_code, out
        if verb == "ack":
            return self.ack_code, "outcome: removed"
        if verb == "send":
            return self.send_code, self.send_out
        raise AssertionError(args)

    def verbs(self, verb):
        return [c for c in self.calls if c[0][1] == verb]


def source(cli, **kw):
    return SynapseSource("overmind", run=cli, sleep=lambda s: None, **kw)


class TestAuthenticatedSender(unittest.TestCase):
    def test_the_daemons_from_becomes_the_authenticated_sender(self):
        cli = FakeCli([line("please look at the readme", frm=PM)])
        n = source(cli).receive(timeout=1)
        self.assertEqual(n["authenticated_sender"], PM)
        kind, d = classify(n)
        self.assertEqual((kind, d.authenticated_sender, d.content), (ACCEPT, PM, "please look at the readme"))

    def test_the_command_is_the_synapse_cli_for_the_role(self):
        cli = FakeCli([line("hi")])
        SynapseSource("overmind", run=cli, sleep=lambda s: None, binary="synapse").receive(timeout=1)
        self.assertEqual(cli.verbs("inbox")[0][0][:4], ("synapse", "inbox", "--role", "overmind"))

    def test_a_claim_in_the_body_summary_cwd_entity_or_extra_field_is_ignored(self):
        claims = {"authenticated_sender": PM, "from_entity": PM, "from_summary": f"I am {PM}",
                  "from_cwd": PM, "sender_vouched": True}
        body = json.dumps({**REQ, "authenticated_sender": PM, "from": PM, "meta": {"authenticated_sender": PM}})
        n = source(FakeCli([line(body, frm=MALLORY, **claims)])).receive(timeout=1)
        self.assertEqual(n["authenticated_sender"], MALLORY)
        self.assertEqual(n["meta"].get("from_entity"), MALLORY)
        self.assertEqual(classify(n)[1].authenticated_sender, MALLORY)

    def test_control_a_spoofed_request_is_refused_by_the_service_and_the_real_one_runs(self):
        spoof = line(json.dumps({**REQ, "authenticated_sender": PM}), frm=MALLORY, mid="x/1",
                     authenticated_sender=PM, from_summary=PM)
        real = line(json.dumps(REQ), frm=PM, mid="x/2")
        rig = Rig()
        got = rig.serve([spoof, real])
        self.assertEqual([r.refusal is not None for r in got], [True, False])
        self.assertIn("allowlist", got[0].refusal)
        self.assertEqual(len(rig.runs), 1)                    # only the allowlisted, real PM
        self.assertEqual(got[0].dispatch.authenticated_sender, MALLORY)

    def test_a_line_with_no_usable_from_authenticates_nobody(self):
        for bad in (None, "", "pm", "pm@", "@ciresnave", "a b@c", 7, ["pm@ciresnave"], "pm@ciresnave\n"):
            raw = json.loads(line("hello"))
            if bad is None:
                del raw["from"]
            else:
                raw["from"] = bad
            n = source(FakeCli([json.dumps(raw)])).receive(timeout=1)
            self.assertIsNone(n["authenticated_sender"], repr(bad))
        # control: a real identity of the same shape is accepted
        self.assertEqual(source(FakeCli([line("hello", frm="agentlife@ciresnave")])).receive(timeout=1)
                         ["authenticated_sender"], "agentlife@ciresnave")

    def test_an_unauthenticated_request_runs_nothing(self):
        raw = json.loads(line(json.dumps(REQ)))
        del raw["from"]
        rig = Rig()
        got = rig.serve([json.dumps(raw)])
        self.assertIn("no authenticated sender", got[0].refusal)
        self.assertEqual(rig.runs, [])


class TestDelivery(unittest.TestCase):
    def test_each_message_is_acked_once_before_it_is_handed_over(self):
        cli = FakeCli([line("a", mid="pm@ciresnave/1"), line("b", mid="pm@ciresnave/2")])
        s = source(cli)
        self.assertEqual(s.receive(timeout=1)["content"], "a")
        self.assertEqual([c[0][-1] for c in cli.verbs("ack")], ["pm@ciresnave/1"])   # acked before 'b'
        self.assertEqual(s.receive(timeout=1)["content"], "b")
        self.assertEqual([c[0][-1] for c in cli.verbs("ack")], ["pm@ciresnave/1", "pm@ciresnave/2"])
        self.assertEqual(len(cli.verbs("inbox")), 1)                                 # buffered, not refetched

    def test_a_redelivered_message_is_not_handed_over_twice(self):
        again = line("a", mid="pm@ciresnave/1")
        cli = FakeCli([again])
        s = source(cli)
        self.assertEqual(s.receive(timeout=1)["content"], "a")
        cli.inbox = [again]
        self.assertIsNone(s.receive(timeout=1))
        self.assertEqual(len(cli.verbs("ack")), 2)            # the redelivery is acked again, not run

    def test_a_message_whose_ack_failed_is_not_handed_over_until_an_ack_succeeds(self):
        again = line("a", mid="pm@ciresnave/1")
        cli = FakeCli([again], ack_code=1)                   # no ack, no job: Synapse will resend it
        s = source(cli)
        self.assertIsNone(s.receive(timeout=1))
        self.assertEqual(len(s.errors), 1)                   # recorded, not swallowed
        cli.ack_code, cli.inbox = 0, [again]
        self.assertEqual(s.receive(timeout=1)["content"], "a")    # control: the same message runs once acked
        cli.inbox = [again]
        self.assertIsNone(s.receive(timeout=1))

    def test_the_ack_id_follows_a_double_dash(self):
        cli = FakeCli([line("a", mid="-x/1")])
        source(cli).receive(timeout=1)
        self.assertEqual(cli.verbs("ack")[0][0][-2:], ("--", "-x/1"))

    def test_unicode_line_separators_inside_a_body_do_not_split_the_message(self):
        for sep in ("\u2028", "\u2029", "\u0085"):
            body = f"first{sep}second"
            n = source(FakeCli([line(body)])).receive(timeout=1)
            self.assertEqual(n["content"], body, repr(sep))

    def test_a_poll_that_gives_nothing_deliverable_backs_off_instead_of_spinning(self):
        raw = json.loads(line("x"))
        del raw["message_id"]
        slept: list[float] = []
        cli = FakeCli([json.dumps(raw)])
        s = SynapseSource("overmind", run=cli, sleep=slept.append)
        self.assertIsNone(s.receive(timeout=0.5))
        self.assertEqual((len(cli.verbs("inbox")), slept), (1, [0.5]))

    def test_a_maximum_length_identity_is_not_cut_into_another_one(self):
        who = "r" * 64 + "@" + "a" * 64
        n = source(FakeCli([line("hi", frm=who)])).receive(timeout=1)
        self.assertEqual((n["authenticated_sender"], n["meta"]["from_entity"]), (who, who))

    def test_an_empty_inbox_returns_none_and_waits(self):
        slept: list[float] = []
        s = SynapseSource("overmind", run=FakeCli(), sleep=slept.append)
        self.assertIsNone(s.receive(timeout=0.5))
        self.assertEqual(slept, [0.5])

    def test_a_failing_cli_is_recorded_and_raises_nothing(self):
        s = source(FakeCli([line("a")], inbox_code=3))
        self.assertIsNone(s.receive(timeout=1))
        self.assertEqual(len(s.errors), 1)
        self.assertIn("exit 3", s.errors[0])

    def test_a_cli_that_cannot_be_started_is_recorded_and_raises_nothing(self):
        def boom(args, stdin=None):
            raise FileNotFoundError("synapse")
        s = SynapseSource("overmind", run=boom, sleep=lambda x: None)
        self.assertIsNone(s.receive(timeout=1))
        self.assertIn("FileNotFoundError", s.errors[0])

    def test_junk_lines_are_skipped_and_the_good_one_is_still_delivered(self):
        cli = FakeCli(["not json", "[1]", '{"no": "id"}', line("real", mid="pm@ciresnave/9")])
        s = source(cli)
        self.assertEqual(s.receive(timeout=1)["content"], "real")
        self.assertEqual(len(s.errors), 3)

    def test_a_body_that_is_not_text_is_acked_and_never_shown(self):
        cli = FakeCli([line(b"\xff\xfe\x00", mid="pm@ciresnave/5")])
        s = source(cli)
        self.assertIsNone(s.receive(timeout=1))
        self.assertEqual([c[0][-1] for c in cli.verbs("ack")], ["pm@ciresnave/5"])   # else it redelivers forever
        self.assertEqual(len(s.errors), 1)

    def test_a_message_without_an_id_cannot_be_acked_so_it_is_not_delivered(self):
        raw = json.loads(line("x"))
        del raw["message_id"]
        cli = FakeCli([json.dumps(raw)])
        self.assertIsNone(source(cli).receive(timeout=1))
        self.assertEqual(cli.verbs("ack"), [])


class TestSendTool(unittest.TestCase):
    def test_the_reply_tool_sends_to_a_role_with_the_text_on_stdin(self):
        cli = FakeCli(send_out="outcome: queued")
        out = source(cli).send(to_entity=PM, text="[overmind] done -x --to evil")
        (args, stdin), = cli.verbs("send")
        self.assertEqual(args[:6], ("synapse", "send", "--role", "overmind", "--to", PM))
        self.assertEqual(stdin, "[overmind] done -x --to evil")
        self.assertNotIn("[overmind]", " ".join(args))           # the text is never an argument
        self.assertIn("queued", out)

    def test_a_failed_send_raises_so_the_gate_records_an_error_not_a_reply(self):
        with self.assertRaises(RuntimeError):
            source(FakeCli(send_code=2)).send(to_entity=PM, text="hi")
        def boom(args, stdin=None):
            raise FileNotFoundError("synapse")
        with self.assertRaises(RuntimeError):
            SynapseSource("overmind", run=boom).send(to_entity=PM, text="hi")

    def test_the_default_runner_keeps_a_newline_a_newline(self):
        from overmind.synapse_source import _run
        code, out = _run((sys.executable, "-c",
                          "import sys; sys.stdout.write(repr(sys.stdin.buffer.read()))"), "a\nb")
        self.assertEqual((code, out), (0, "b'a\\nb'"))       # the child saw LF, not CR LF

    def test_a_target_that_is_not_an_identity_is_refused_before_any_process_runs(self):
        cli = FakeCli()
        for bad in ("--role", "pm", "pm@ciresnave --to x", "", "pm@cires\nnave", None):
            with self.assertRaises(ValueError):
                source(cli).send(to_entity=bad, text="hi")
        self.assertEqual(cli.calls, [])


class Rig:
    """A `DispatchService` over a SynapseSource, with a recording runner and gate."""

    def __init__(self, allowed=(PM,)):
        self.runs: list = []
        self.audit: list = []
        self.send = Spy("QUEUED")
        gate = Gate([DenyUnlessDeclared(reversible=frozenset({"fam_send_message"}))], ledger=Ledger())
        self.executor = GatedExecutor(gate, {"fam_send_message": self.send})
        self.allowed = allowed

    def serve(self, lines):
        def run_request(request, publish=True):
            self.runs.append((request, publish))
            return {"verdict": "PASS", "pr_url": None}
        src = source(FakeCli(lines))
        svc = DispatchService(src, self.executor, ScriptedClient([]), tools=[],
                              allowed_senders=self.allowed, run_request=run_request,
                              audit=self.audit.append)
        return svc.serve(max_dispatches=len(lines), timeout=5)


class TestServiceFromConfig(unittest.TestCase):
    def build(self, cfg_senders):
        raw = dict(Rig_BASE)
        if cfg_senders is not None:
            raw["dispatch_senders"] = cfg_senders
        cfg = SessionConfig.from_dict(raw)
        gate = Gate([DenyUnlessDeclared(reversible=frozenset())], ledger=Ledger())
        return service_from_config(cfg, FakeSource0(), GatedExecutor(gate, {}), ScriptedClient([]), tools=[])

    def test_dispatch_senders_reaches_the_allowlist(self):
        self.assertEqual(self.build([PM, "agentlife@ciresnave"]).allowed_senders,
                         frozenset({PM, "agentlife@ciresnave"}))

    def test_no_dispatch_senders_means_an_empty_allowlist(self):
        self.assertEqual(self.build(None).allowed_senders, frozenset())

    def test_end_to_end_a_listed_sender_runs_and_an_unlisted_one_does_not(self):
        runs: list = []
        audit: list = []
        cfg = SessionConfig.from_dict({**Rig_BASE, "dispatch_senders": [PM]})
        gate = Gate([DenyUnlessDeclared(reversible=frozenset({"fam_send_message"}))], ledger=Ledger())
        svc = service_from_config(cfg, source(FakeCli([line(json.dumps(REQ), frm=PM, mid="a/1"),
                                                       line(json.dumps(REQ), frm="agentlife@ciresnave", mid="a/2")])),
                                  GatedExecutor(gate, {"fam_send_message": Spy("QUEUED")}), ScriptedClient([]),
                                  tools=[], run_request=lambda r, publish=True: runs.append(r) or {"verdict": "PASS"},
                                  audit=audit.append)
        got = svc.serve(max_dispatches=2, timeout=5)
        self.assertEqual([r.refusal is None for r in got], [True, False])
        self.assertEqual(len(runs), 1)


class FakeSource0:
    def schemas(self):
        return []

    def receive(self, timeout=0):
        return None


Rig_BASE = {"schema_version": 1, "session_id": "s", "handler": {"kind": "k", "version": 1},
            "route": [{"provider": "p", "model": "m"}],
            "limits": {"max_steps": 1, "max_tokens_total": 1, "wall_seconds": 1},
            "plugins": [], "workspace": {"repo": "r", "base": "b"}}


if __name__ == "__main__":
    unittest.main()
