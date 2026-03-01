# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the generic config stack engine."""

import json
import tempfile
import unittest
from pathlib import Path

import yaml

from luskctl.lib.util.config_stack import (
    ConfigScope,
    ConfigStack,
    deep_merge,
    load_json_scope,
    load_yaml_scope,
)


class DeepMergeTests(unittest.TestCase):
    """Tests for deep_merge()."""

    def test_simple_override(self) -> None:
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        self.assertEqual(deep_merge(base, override), {"a": 1, "b": 3, "c": 4})

    def test_nested_merge(self) -> None:
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"b": 3, "c": 4}}
        self.assertEqual(deep_merge(base, override), {"x": {"a": 1, "b": 3, "c": 4}})

    def test_none_deletes_key(self) -> None:
        base = {"a": 1, "b": 2, "c": 3}
        override = {"b": None}
        self.assertEqual(deep_merge(base, override), {"a": 1, "c": 3})

    def test_none_deletes_nested_key(self) -> None:
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"a": None}}
        self.assertEqual(deep_merge(base, override), {"x": {"b": 2}})

    def test_list_replacement(self) -> None:
        base = {"items": [1, 2, 3]}
        override = {"items": [4, 5]}
        self.assertEqual(deep_merge(base, override), {"items": [4, 5]})

    def test_list_inherit_splices_base(self) -> None:
        base = {"items": ["a", "b"]}
        override = {"items": ["_inherit", "c"]}
        self.assertEqual(deep_merge(base, override), {"items": ["a", "b", "c"]})

    def test_list_inherit_at_end(self) -> None:
        base = {"items": ["a", "b"]}
        override = {"items": ["c", "_inherit"]}
        self.assertEqual(deep_merge(base, override), {"items": ["c", "a", "b"]})

    def test_list_inherit_in_middle(self) -> None:
        base = {"items": ["b"]}
        override = {"items": ["a", "_inherit", "c"]}
        self.assertEqual(deep_merge(base, override), {"items": ["a", "b", "c"]})

    def test_dict_inherit_keeps_parent(self) -> None:
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"_inherit": True, "c": 3}}
        self.assertEqual(deep_merge(base, override), {"x": {"a": 1, "b": 2, "c": 3}})

    def test_dict_inherit_overlay_overrides(self) -> None:
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"_inherit": True, "b": 9, "c": 3}}
        self.assertEqual(deep_merge(base, override), {"x": {"a": 1, "b": 9, "c": 3}})

    def test_dict_without_inherit_merges_recursively(self) -> None:
        """Dicts merge recursively by default (no _inherit needed for dicts)."""
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"c": 3}}
        result = deep_merge(base, override)
        self.assertEqual(result, {"x": {"a": 1, "b": 2, "c": 3}})

    def test_bare_inherit_keeps_base_value(self) -> None:
        """Bare _inherit string keeps the base value unchanged."""
        base = {"a": 1, "b": [1, 2], "c": {"x": 1}}
        override = {"a": "_inherit", "b": "_inherit", "c": "_inherit"}
        self.assertEqual(deep_merge(base, override), {"a": 1, "b": [1, 2], "c": {"x": 1}})

    def test_bare_inherit_no_base_drops_key(self) -> None:
        """Bare _inherit with no base value drops the key."""
        base = {"a": 1}
        override = {"a": "_inherit", "b": "_inherit"}
        self.assertEqual(deep_merge(base, override), {"a": 1})

    def test_empty_base(self) -> None:
        self.assertEqual(deep_merge({}, {"a": 1}), {"a": 1})

    def test_empty_override(self) -> None:
        self.assertEqual(deep_merge({"a": 1}, {}), {"a": 1})

    def test_both_empty(self) -> None:
        self.assertEqual(deep_merge({}, {}), {})

    def test_scalar_replaces_dict(self) -> None:
        base = {"x": {"a": 1}}
        override = {"x": "flat"}
        self.assertEqual(deep_merge(base, override), {"x": "flat"})

    def test_dict_replaces_scalar(self) -> None:
        base = {"x": "flat"}
        override = {"x": {"a": 1}}
        self.assertEqual(deep_merge(base, override), {"x": {"a": 1}})

    def test_deeply_nested(self) -> None:
        base = {"a": {"b": {"c": {"d": 1, "e": 2}}}}
        override = {"a": {"b": {"c": {"e": 3, "f": 4}}}}
        expected = {"a": {"b": {"c": {"d": 1, "e": 3, "f": 4}}}}
        self.assertEqual(deep_merge(base, override), expected)


class ConfigStackTests(unittest.TestCase):
    """Tests for ConfigScope and ConfigStack."""

    def test_single_scope(self) -> None:
        stack = ConfigStack()
        stack.push(ConfigScope("base", None, {"a": 1}))
        self.assertEqual(stack.resolve(), {"a": 1})

    def test_multi_level_chaining(self) -> None:
        stack = ConfigStack()
        stack.push(ConfigScope("global", None, {"a": 1, "b": 1}))
        stack.push(ConfigScope("project", None, {"b": 2, "c": 2}))
        stack.push(ConfigScope("cli", None, {"c": 3, "d": 3}))
        self.assertEqual(stack.resolve(), {"a": 1, "b": 2, "c": 3, "d": 3})

    def test_section_resolution(self) -> None:
        stack = ConfigStack()
        stack.push(ConfigScope("global", None, {"agent": {"model": "haiku"}, "other": 1}))
        stack.push(ConfigScope("project", None, {"agent": {"model": "sonnet", "turns": 5}}))
        result = stack.resolve_section("agent")
        self.assertEqual(result, {"model": "sonnet", "turns": 5})

    def test_section_resolution_missing_section(self) -> None:
        stack = ConfigStack()
        stack.push(ConfigScope("global", None, {"other": 1}))
        self.assertEqual(stack.resolve_section("agent"), {})

    def test_empty_stack(self) -> None:
        stack = ConfigStack()
        self.assertEqual(stack.resolve(), {})

    def test_scope_with_none_deletion(self) -> None:
        stack = ConfigStack()
        stack.push(ConfigScope("base", None, {"a": 1, "b": 2}))
        stack.push(ConfigScope("override", None, {"b": None}))
        self.assertEqual(stack.resolve(), {"a": 1})

    def test_scopes_property(self) -> None:
        stack = ConfigStack()
        s1 = ConfigScope("a", None, {})
        s2 = ConfigScope("b", None, {})
        stack.push(s1)
        stack.push(s2)
        self.assertEqual(stack.scopes, [s1, s2])

    def test_scopes_property_is_copy(self) -> None:
        stack = ConfigStack()
        stack.push(ConfigScope("a", None, {}))
        scopes = stack.scopes
        scopes.append(ConfigScope("b", None, {}))
        self.assertEqual(len(stack.scopes), 1)


class LoaderTests(unittest.TestCase):
    """Tests for YAML/JSON scope loaders."""

    def test_load_yaml_scope(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.yml"
            p.write_text(yaml.dump({"key": "value"}), encoding="utf-8")
            scope = load_yaml_scope("test", p)
            self.assertEqual(scope.level, "test")
            self.assertEqual(scope.source, p)
            self.assertEqual(scope.data, {"key": "value"})

    def test_load_json_scope(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.json"
            p.write_text(json.dumps({"key": "value"}), encoding="utf-8")
            scope = load_json_scope("test", p)
            self.assertEqual(scope.level, "test")
            self.assertEqual(scope.data, {"key": "value"})

    def test_load_yaml_missing_file(self) -> None:
        scope = load_yaml_scope("missing", Path("/nonexistent/config.yml"))
        self.assertEqual(scope.data, {})

    def test_load_json_missing_file(self) -> None:
        scope = load_json_scope("missing", Path("/nonexistent/config.json"))
        self.assertEqual(scope.data, {})

    def test_load_yaml_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "empty.yml"
            p.write_text("", encoding="utf-8")
            scope = load_yaml_scope("empty", p)
            self.assertEqual(scope.data, {})

    def test_load_json_empty_object(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "empty.json"
            p.write_text("{}", encoding="utf-8")
            scope = load_json_scope("empty", p)
            self.assertEqual(scope.data, {})
