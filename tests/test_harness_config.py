# SPDX-License-Identifier: MIT OR Apache-2.0
"""SessionConfig and LoadedManifest (ARCHITECTURE-LAYERS-SPEC.md §2; plan P3.1).

Pure data. The point of every refusal here is that the harness "never runs a
partial pipeline": a config it does not fully understand is refused whole, not
read as far as it makes sense. And the orchestrator's check after loading is a
state read: `compare` returns EVERY difference, so a test that passes for the
wrong reason shows up as a missing line.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.harness.config import ConfigError, SessionConfig  # noqa: E402
from overmind.harness.manifest import (  # noqa: E402
    LoadedManifest, PluginRecord, compare, source_sha256,
)

# The spec's own sample (§2), the one document that says what a config is.
SAMPLE: dict = {
    "schema_version": 1,
    "session_id": "s-1",
    "handler": {"kind": "openai-chat", "version": 1},
    "route": [{"provider": "groq", "model": "m1"}, {"provider": "ollama", "model": "m2"}],
    "limits": {"max_steps": 40, "max_tokens_total": 200000, "wall_seconds": 1800},
    "plugins": [
        {"name": "gate", "version": "1",
         "config": {"policies": ["workspace-confined", "deny-unless-declared"]}},
        {"name": "workspace-tools", "version": "1", "config": {"root": "/w", "writable": ["/w"]}},
        {"name": "ledger", "version": "1", "config": {}},
    ],
    "workspace": {"repo": "https://github.com/ciresnave/OverMind.git", "base": "main"},
}


def sample() -> dict:
    return copy.deepcopy(SAMPLE)


class TestLoadsTheSpecSample(unittest.TestCase):
    def test_the_sample_loads_and_reads_back(self):
        cfg = SessionConfig.from_dict(sample())
        self.assertEqual(cfg.session_id, "s-1")
        self.assertEqual([p.name for p in cfg.plugins], ["gate", "workspace-tools", "ledger"])
        self.assertEqual(cfg.route[1].provider, "ollama")
        self.assertEqual(cfg.limits.max_steps, 40)
        self.assertEqual(cfg.plugins[0].config["policies"][1], "deny-unless-declared")

    def test_json_text_round_trips(self):
        cfg = SessionConfig.from_json(json.dumps(sample()))
        self.assertEqual(json.loads(cfg.to_json()), sample())
        self.assertEqual(SessionConfig.from_json(cfg.to_json()), cfg)

    def test_plugin_order_is_kept(self):
        """Order is the pipeline order (§2 'Ordering'): the config must not sort it."""
        d = sample()
        d["plugins"].reverse()
        cfg = SessionConfig.from_dict(d)
        self.assertEqual([p.name for p in cfg.plugins], ["ledger", "workspace-tools", "gate"])


class TestRefusesWhatItDoesNotUnderstand(unittest.TestCase):
    def refused(self, mutate, fragment: str):
        d = sample()
        mutate(d)
        with self.assertRaises(ConfigError) as cm:
            SessionConfig.from_dict(d)
        self.assertIn(fragment, str(cm.exception))

    def test_unknown_schema_version(self):
        self.refused(lambda d: d.update(schema_version=2), "schema_version")

    def test_schema_version_true_is_not_1(self):
        # bool is an int: `true` must not pass as 1 (Task._check_types, audit 2026-10-18)
        self.refused(lambda d: d.update(schema_version=True), "schema_version")

    def test_missing_schema_version(self):
        self.refused(lambda d: d.pop("schema_version"), "schema_version")

    def test_unknown_top_level_field(self):
        self.refused(lambda d: d.update(extra=1), "extra")

    def test_unknown_nested_field(self):
        self.refused(lambda d: d["limits"].update(max_cost=3), "max_cost")

    def test_missing_field(self):
        self.refused(lambda d: d["workspace"].pop("base"), "base")

    def test_limit_that_is_a_bool(self):
        self.refused(lambda d: d["limits"].update(max_steps=True), "max_steps")

    def test_limit_that_is_a_string(self):
        self.refused(lambda d: d["limits"].update(wall_seconds="1800"), "wall_seconds")

    def test_limit_that_is_not_positive(self):
        self.refused(lambda d: d["limits"].update(max_steps=0), "max_steps")

    def test_empty_route(self):
        self.refused(lambda d: d.update(route=[]), "route")

    def test_route_entry_not_an_object(self):
        self.refused(lambda d: d.update(route=["groq"]), "route")

    def test_empty_plugin_name(self):
        self.refused(lambda d: d["plugins"][0].update(name=""), "name")

    def test_duplicate_plugin_name(self):
        self.refused(lambda d: d["plugins"].append(dict(d["plugins"][0])), "duplicate")

    def test_plugin_config_not_an_object(self):
        self.refused(lambda d: d["plugins"][0].update(config=[1]), "config")

    def test_plugins_not_a_list(self):
        self.refused(lambda d: d.update(plugins={"name": "gate"}), "plugins")

    def test_session_id_not_a_string(self):
        self.refused(lambda d: d.update(session_id=7), "session_id")

    def test_top_level_not_an_object(self):
        with self.assertRaises(ConfigError):
            SessionConfig.from_dict([sample()])  # type: ignore[arg-type]

    def test_invalid_json_text(self):
        with self.assertRaises(ConfigError):
            SessionConfig.from_json("{not json")

    def test_empty_plugin_list_is_allowed(self):
        """'Required' (every session loads the gate) is orchestrator policy
        checked against the manifest, not harness policy (§2): the loader must
        not enforce it."""
        d = sample()
        d["plugins"] = []
        self.assertEqual(SessionConfig.from_dict(d).plugins, ())


def record(name="gate", version="1", api=1, sha="a" * 64) -> PluginRecord:
    return PluginRecord(name=name, version=version, api_version=api, sha256=sha)


def matching_manifest(cfg: SessionConfig) -> LoadedManifest:
    return LoadedManifest(
        plugins=tuple(record(p.name, p.version) for p in cfg.plugins),
        tools=("read_file",), services=())


class TestCompare(unittest.TestCase):
    def setUp(self):
        self.cfg = SessionConfig.from_dict(sample())

    def test_a_matching_manifest_has_no_differences(self):
        self.assertEqual(compare(self.cfg, matching_manifest(self.cfg)), [])

    def test_a_missing_plugin_is_reported_by_name(self):
        m = matching_manifest(self.cfg)
        m = LoadedManifest(plugins=m.plugins[:-1], tools=m.tools, services=m.services)
        diffs = compare(self.cfg, m)
        self.assertTrue(any("ledger" in x and "not loaded" in x for x in diffs), diffs)

    def test_an_extra_plugin_is_reported_by_name(self):
        m = matching_manifest(self.cfg)
        m = LoadedManifest(plugins=m.plugins + (record("sneaky"),), tools=m.tools, services=m.services)
        diffs = compare(self.cfg, m)
        self.assertTrue(any("sneaky" in x and "not in the config" in x for x in diffs), diffs)

    def test_a_different_version_is_reported(self):
        m = matching_manifest(self.cfg)
        plugins = list(m.plugins)
        plugins[0] = record("gate", version="2")
        diffs = compare(self.cfg, LoadedManifest(tuple(plugins), m.tools, m.services))
        self.assertTrue(any("gate" in x and "version" in x for x in diffs), diffs)

    def test_a_different_order_is_reported(self):
        """Order is pipeline order, so a reordered load is a difference."""
        m = matching_manifest(self.cfg)
        swapped = (m.plugins[1], m.plugins[0]) + m.plugins[2:]
        diffs = compare(self.cfg, LoadedManifest(swapped, m.tools, m.services))
        self.assertTrue(any("order" in x for x in diffs), diffs)

    def test_every_difference_is_returned_not_just_the_first(self):
        m = matching_manifest(self.cfg)
        plugins = [record("gate", version="9"), m.plugins[1], record("sneaky")]
        diffs = compare(self.cfg, LoadedManifest(tuple(plugins), m.tools, m.services))
        self.assertGreaterEqual(len(diffs), 3, diffs)

    def test_a_pinned_hash_that_differs_is_reported(self):
        m = matching_manifest(self.cfg)
        diffs = compare(self.cfg, m, expected_sha256={"gate": "b" * 64})
        self.assertTrue(any("gate" in x and "sha256" in x for x in diffs), diffs)

    def test_a_pinned_hash_that_matches_is_clean(self):
        m = matching_manifest(self.cfg)
        self.assertEqual(compare(self.cfg, m, expected_sha256={"gate": "a" * 64}), [])

    def test_a_pin_for_a_plugin_that_did_not_load_is_reported(self):
        m = matching_manifest(self.cfg)
        diffs = compare(self.cfg, m, expected_sha256={"ghost": "a" * 64})
        self.assertTrue(any("ghost" in x for x in diffs), diffs)

    def test_the_manifest_does_not_trust_a_malformed_hash(self):
        with self.assertRaises(ConfigError):
            PluginRecord(name="gate", version="1", api_version=1, sha256="xyz")

    def test_api_version_must_be_an_int_not_a_bool(self):
        with self.assertRaises(ConfigError):
            PluginRecord(name="gate", version="1", api_version=True, sha256="a" * 64)  # type: ignore[arg-type]


class TestSourceHash(unittest.TestCase):
    def write(self, data: bytes) -> str:
        fd, path = tempfile.mkstemp(suffix=".py")
        self.addCleanup(os.unlink, path)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return path

    def test_known_digest(self):
        # sha256 of b"abc": a value this test did not compute with the code under test
        self.assertEqual(
            source_sha256(self.write(b"abc")),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_one_byte_changes_the_hash(self):
        self.assertNotEqual(source_sha256(self.write(b"x = 1\n")),
                            source_sha256(self.write(b"x = 2\n")))

    def test_line_endings_are_part_of_the_bytes(self):
        """The hash is of the file as it is on disk: a CRLF checkout of the same
        source hashes differently, and the orchestrator must pin per checkout."""
        self.assertNotEqual(source_sha256(self.write(b"a\nb\n")),
                            source_sha256(self.write(b"a\r\nb\r\n")))


if __name__ == "__main__":
    unittest.main()
