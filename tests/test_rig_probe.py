# SPDX-License-Identifier: MIT OR Apache-2.0
"""Tests for probe/rig_probe.py, against a fake OpenAI-compatible server on loopback.

No real model is involved: the point is that the probe reports what the server
did, and keeps "the server failed" apart from "the model did not do the task".
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "probe"))

import rig_probe  # noqa: E402


class FakeServer:
    """Serves /v1/models and /v1/chat/completions. `ctx` is the prompt-token
    ceiling above which it answers HTTP 400, like a server with a fixed context."""

    def __init__(self, ctx=10_000, tool_style="native", clip=False, fail503_over=None, tools_400=False):
        outer = self
        self.ctx, self.tool_style, self.clip, self.requests = ctx, tool_style, clip, []
        self.fail503_over, self.tools_400 = fail503_over, tools_400

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send(200, {"data": [{"id": "fake-27b"}]})

            def do_POST(self):
                n = int(self.headers["Content-Length"])
                req = json.loads(self.rfile.read(n))
                outer.requests.append(req)
                text = " ".join(m.get("content") or "" for m in req["messages"])
                prompt_tokens = max(1, len(text) // 4)
                if outer.fail503_over and prompt_tokens > outer.fail503_over:
                    return self._send(503, {"error": {"message": "overloaded"}})
                if req.get("tools") and outer.tools_400:
                    return self._send(400, {"error": {"message": "model does not support tools"}})
                if prompt_tokens > outer.ctx and outer.clip:
                    prompt_tokens = outer.ctx // 2  # Ollama-style: accept, silently cut, report the cut size
                if prompt_tokens > outer.ctx:
                    return self._send(400, {"error": {"message": "context length exceeded"}})
                if req.get("tools"):
                    if outer.tool_style == "native":
                        msg = {"role": "assistant", "content": None, "tool_calls": [{
                            "id": "c1", "type": "function", "function": {
                                "name": "get_build_status",
                                "arguments": json.dumps({"repo": "ciresnave/fuel", "branch": "main"})}}]}
                    else:  # a tool call written into the text: a FAIL on the tool side
                        msg = {"role": "assistant", "content": json.dumps(
                            {"name": "get_build_status", "arguments": {"repo": "x", "branch": "main"}})}
                else:
                    msg = {"role": "assistant", "content": "ok " * 20}
                self._send(200, {"choices": [{"message": msg, "finish_reason": "stop"}],
                                 "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 20}})

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Pure(unittest.TestCase):
    def test_filler_is_about_the_requested_size(self):
        self.assertAlmostEqual(len(rig_probe.filler(1000)) / 4, 1000, delta=50)

    def test_a_native_tool_call_passes(self):
        msg = {"content": None, "tool_calls": [{"function": {
            "name": "get_build_status", "arguments": '{"repo":"a/b","branch":"main"}'}}]}
        self.assertEqual(rig_probe.tool_verdict(msg)[0], "PASS")

    def test_a_tool_call_written_into_content_fails(self):
        msg = {"content": '{"name": "get_build_status", "arguments": {}}'}
        self.assertEqual(rig_probe.tool_verdict(msg)[0], "FAIL")

    def test_a_reply_with_no_tool_call_fails(self):
        self.assertEqual(rig_probe.tool_verdict({"content": "it is green"})[0], "FAIL")

    def test_arguments_already_parsed_into_an_object_are_accepted(self):
        msg = {"tool_calls": [{"function": {"name": "get_build_status",
                                            "arguments": {"repo": "a/b", "branch": "main"}}}]}
        self.assertEqual(rig_probe.tool_verdict(msg)[0], "PASS")

    def test_a_call_missing_a_required_argument_fails(self):
        msg = {"tool_calls": [{"function": {"name": "get_build_status", "arguments": '{"repo":"a/b"}'}}]}
        self.assertEqual(rig_probe.tool_verdict(msg)[0], "FAIL")
        msg = {"tool_calls": [{"function": {"name": "get_build_status", "arguments": "{}"}}]}
        self.assertEqual(rig_probe.tool_verdict(msg)[0], "FAIL")

    def test_malformed_arguments_fail(self):
        msg = {"tool_calls": [{"function": {"name": "get_build_status", "arguments": "{nope"}}]}
        self.assertEqual(rig_probe.tool_verdict(msg)[0], "FAIL")


class Entry(unittest.TestCase):
    def test_a_transport_stopped_ladder_exits_nonzero(self):
        from unittest import mock
        rep = {"context": {"stopped_by": "transport", "steps": []}}
        with mock.patch.object(rig_probe, "run", return_value=rep),                 mock.patch("builtins.print"):
            self.assertEqual(rig_probe.main(["--base-url", "http://x/v1"]), 1)

    def test_no_address_exits_two(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("sys.stderr"):
            self.assertEqual(rig_probe.main([]), 2)

    def test_a_one_step_ladder_says_clipping_was_not_checked(self):
        srv = FakeServer(ctx=100_000)
        self.addCleanup(srv.close)
        rep = rig_probe.run(srv.url, model=None, ladder=(1000,), timeout=10)
        self.assertIn("clipping not checked", rep["context"]["note"])


class AgainstAFakeServer(unittest.TestCase):
    def run_probe(self, **kw):
        srv = FakeServer(**kw)
        self.addCleanup(srv.close)
        return rig_probe.run(srv.url, model=None, ladder=(1000, 4000, 16000), timeout=10)

    def test_the_ladder_stops_at_the_first_context_failure_and_says_where(self):
        rep = self.run_probe(ctx=5000)
        steps = rep["context"]["steps"]
        self.assertEqual([s["target"] for s in steps], [1000, 4000, 16000])
        self.assertEqual([s["ok"] for s in steps], [True, True, False])
        self.assertEqual(rep["context"]["largest_ok_prompt_tokens"], steps[1]["prompt_tokens"])
        self.assertEqual(steps[2]["kind"], "http-400")

    def test_a_server_with_room_reaches_the_top_of_the_ladder(self):
        rep = self.run_probe(ctx=100_000)
        self.assertTrue(all(s["ok"] for s in rep["context"]["steps"]))

    def test_a_server_that_silently_clips_is_not_credited_with_the_big_prompt(self):
        """Measured on Ollama 0.40.4: a prompt over num_ctx is accepted with HTTP 200
        and `usage.prompt_tokens` is the CLIPPED size, smaller than a previous step's."""
        rep = self.run_probe(ctx=5000, clip=True)
        steps = rep["context"]["steps"]
        self.assertEqual(steps[2]["kind"], "clipped")
        self.assertFalse(steps[2]["ok"])
        self.assertEqual(rep["context"]["largest_ok_prompt_tokens"], steps[1]["prompt_tokens"])

    def test_a_step_that_fails_for_a_transport_reason_is_not_called_a_context_limit(self):
        rep = self.run_probe(ctx=100_000, fail503_over=3000)
        self.assertEqual(rep["context"]["stopped_by"], "transport")
        self.assertEqual(rep["context"]["steps"][-1]["kind"], "http-503")

    def test_a_context_overflow_and_a_clip_are_both_context_limits(self):
        self.assertEqual(self.run_probe(ctx=5000)["context"]["stopped_by"], "context-limit")
        self.assertEqual(self.run_probe(ctx=5000, clip=True)["context"]["stopped_by"], "context-limit")

    def test_a_full_ladder_stopped_by_nothing(self):
        self.assertIsNone(self.run_probe(ctx=100_000)["context"]["stopped_by"])

    def test_a_model_without_tool_support_is_a_verdict_and_decode_still_runs(self):
        rep = self.run_probe(tools_400=True)
        self.assertEqual(rep["tool_call"]["verdict"], "UNSUPPORTED")
        self.assertNotIn("transport_error", rep)
        self.assertIn("decode", rep)

    def test_a_connection_dropped_mid_request_is_a_transport_failure_not_a_traceback(self):
        import socket
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def drop():
            c, _ = srv.accept()
            c.recv(4096)
            c.close()
        threading.Thread(target=drop, daemon=True).start()
        self.addCleanup(srv.close)
        rep = rig_probe.run(f"http://127.0.0.1:{port}/v1", model=None, ladder=(1000,), timeout=10)
        self.assertEqual(rep["transport_error"]["kind"], "disconnected")

    def test_the_model_defaults_to_the_first_on_the_roster(self):
        self.assertEqual(self.run_probe()["model"], "fake-27b")

    def test_tool_call_is_scored_off_the_tool_side(self):
        self.assertEqual(self.run_probe(tool_style="native")["tool_call"]["verdict"], "PASS")
        self.assertEqual(self.run_probe(tool_style="in-content")["tool_call"]["verdict"], "FAIL")

    def test_decode_rate_is_labelled_as_including_prefill(self):
        d = self.run_probe()["decode"]
        self.assertIn("includes prefill", d["note"])
        self.assertGreater(d["completion_tokens"], 0)

    def test_an_unreachable_server_is_a_transport_failure_not_a_model_verdict(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()  # a port that was free a moment ago: nothing listens
        rep = rig_probe.run(f"http://127.0.0.1:{port}/v1", model=None, ladder=(1000,), timeout=15)  # Windows retries a refused loopback connect for ~2 s
        self.assertEqual(rep["transport_error"]["kind"], "unreachable")
        self.assertNotIn("tool_call", rep)


if __name__ == "__main__":
    unittest.main(verbosity=2)
