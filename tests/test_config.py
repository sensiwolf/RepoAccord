import unittest

from repoaccord.config import Config, load_config
from repoaccord.errors import ConfigError


class TestConfig(unittest.TestCase):
    def test_defaults(self) -> None:
        for value in (None, "", " \n\t"):
            with self.subTest(value=value):
                self.assertEqual(load_config(value), Config())

    def test_full_configuration(self) -> None:
        config = load_config(
            """
version: 1
branches:
  prefixes: [work/]
actors:
  allow: [Alice, bob]
issues:
  label: triage
  template: bug.md
pull_request:
  placeholder: ISSUE_REF
trailers:
  forbid: [Signed-off-by]
auto_merge:
  enabled: true
  method: squash
"""
        )
        self.assertEqual(config.version, 1)
        self.assertEqual(config.branches.prefixes, ("work/",))
        self.assertEqual(config.actors.allow, ("Alice", "bob"))
        self.assertEqual(config.issues.label, "triage")
        self.assertEqual(config.issues.template, "bug.md")
        self.assertEqual(config.pull_request.placeholder, "ISSUE_REF")
        self.assertEqual(config.trailers.forbid, ("Signed-off-by",))
        self.assertTrue(config.auto_merge.enabled)
        self.assertEqual(config.auto_merge.method, "squash")

    def test_unknown_keys_report_their_path(self) -> None:
        for text, path in (
            ("mystery: true", "mystery"),
            ("branches:\n  mystery: true", "branches.mystery"),
            ("issues:\n  mystery: true", "issues.mystery"),
            ("auto_merge:\n  mystery: true", "auto_merge.mystery"),
        ):
            with self.subTest(path=path), self.assertRaisesRegex(ConfigError, path):
                load_config(text)

    def test_wrong_types(self) -> None:
        for text in (
            "branches: []",
            "actors: false",
            "issues: []",
            "pull_request:\n  placeholder: 2",
            "trailers:\n  forbid: one",
            "auto_merge:\n  enabled: 1",
            "version: true",
            "branches:\n  prefixes: [feat/]\nactors:\n  allow: [a, 2]",
        ):
            with self.subTest(text=text), self.assertRaises(ConfigError):
                load_config(text)

    def test_bad_version_and_method(self) -> None:
        for text in ("version: 2", "auto_merge:\n  method: fast"):
            with self.subTest(text=text), self.assertRaises(ConfigError):
                load_config(text)

    def test_duplicate_keys_at_multiple_levels(self) -> None:
        for text in (
            "version: 1\nversion: 1",
            "branches:\n  prefixes: [feat/]\n  prefixes: [fix/]",
            "auto_merge:\n  enabled: false\n  enabled: true",
        ):
            with self.subTest(text=text), self.assertRaisesRegex(ConfigError, "duplicate key"):
                load_config(text)

    def test_oversize_and_non_mapping(self) -> None:
        with self.assertRaisesRegex(ConfigError, "64 KiB"):
            load_config(" " * (64 * 1024 + 1))
        with self.assertRaisesRegex(ConfigError, "64 KiB"):
            load_config("é" * 32_769)
        for text in ("[one, two]", "42", "null"):
            with self.subTest(text=text), self.assertRaises(ConfigError):
                load_config(text)

    def test_prefix_constraints(self) -> None:
        for prefixes in (
            "[feat]",
            "[feat/ bad/]",
            "[feat/../]",
            "[/feat/]",
            "[_feat/]",
            "[]",
            "[" + ", ".join(f"p{i}/" for i in range(21)) + "]",
        ):
            text = f"branches:\n  prefixes: {prefixes}"
            with self.subTest(prefixes=prefixes), self.assertRaises(ConfigError):
                load_config(text)

    def test_issue_label_and_template_constraints(self) -> None:
        for text in (
            'issues:\n  label: "line1\\nline2"',
            "issues:\n  label: " + "x" * 51,
            "issues:\n  template: ../bug.md",
            'issues:\n  template: ""',
        ):
            with self.subTest(text=text), self.assertRaises(ConfigError):
                load_config(text)

    def test_boundary_values_and_frozen_dataclasses(self) -> None:
        config = load_config(
            "branches:\n  prefixes: [x/]\nissues:\n  label: " + "a" * 50 + "\n"
            "pull_request:\n  placeholder: " + "p" * 64
        )
        self.assertEqual(len(config.issues.label or ""), 50)
        self.assertEqual(len(config.pull_request.placeholder), 64)
        with self.assertRaises(AttributeError):
            config.version = 2  # type: ignore[misc]
