# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The Markdown options reference is generated from the config schema."""

from __future__ import annotations

from typing import Any, Literal

import pytest
from pydantic import BaseModel, Field

from terok.lib.core import yaml_schema
from terok.lib.core.config_reference import (
    global_config_reference,
    project_config_reference,
    render_config_reference,
)
from terok.lib.core.yaml_schema import RawGlobalConfig, RawProjectYaml


class _Inner(BaseModel):
    depth: int = Field(default=3, description="How deep.")


class _Item(BaseModel):
    host: str = Field(description="Where.")


class _Sample(BaseModel):
    name: str = Field(description="A name.")
    flag: bool = Field(default=True, description="A flag.")
    mode: Literal["fast", "slow"] = Field(default="fast", description="A mode.")
    limit: int | None = None
    tags: list[str] = Field(default_factory=list, description="Some tags.")
    extra: dict[str, Any] = Field(default_factory=dict, description="More.")
    retries: dict[str, Any] = Field(default_factory=lambda: {"max": 3}, description="Retries.")
    hosts: list[str] = Field(default_factory=lambda: ["example"], description="Some hosts.")
    timeout: int = Field(default=0, description="A timeout.")
    inner: _Inner = Field(default_factory=_Inner)
    items: list[_Item] = Field(default_factory=list)


@pytest.fixture
def reference() -> str:
    return render_config_reference(_Sample)


def _option(reference: str, name: str) -> str:
    """Return the lines of one option, up to the next heading."""
    return reference.split(f"\n## {name}\n", 1)[1].split("\n#", 1)[0]


def _leaf_names(model: type[BaseModel]) -> set[str]:
    """Return the keys of *model* and everything nested in it that take a plain value."""
    names: set[str] = set()
    for name, field in model.model_fields.items():
        nested = [
            arg
            for arg in (field.annotation, *getattr(field.annotation, "__args__", ()))
            if isinstance(arg, type) and issubclass(arg, BaseModel)
        ]
        if nested:
            names |= _leaf_names(nested[0])
        else:
            names.add(name)
    return names


class TestRendering:
    def test_required_option(self, reference: str) -> None:
        assert "text · required" in _option(reference, "name")

    def test_boolean_default_is_spelled_as_yaml(self, reference: str) -> None:
        assert "true or false · default `true`" in _option(reference, "flag")

    def test_choices_are_listed(self, reference: str) -> None:
        assert "one of `fast`, `slow` · default `fast`" in _option(reference, "mode")

    def test_optional_value_without_a_default(self, reference: str) -> None:
        assert "integer · unset by default" in _option(reference, "limit")

    def test_empty_collections(self, reference: str) -> None:
        assert "list of text · empty by default" in _option(reference, "tags")
        assert "mapping · empty by default" in _option(reference, "extra")

    def test_filled_collections_show_their_default(self, reference: str) -> None:
        assert "mapping · default `{'max': 3}`" in _option(reference, "retries")
        assert "list of text · default `['example']`" in _option(reference, "hosts")

    def test_zero_is_a_default_and_not_empty(self, reference: str) -> None:
        assert "integer · default `0`" in _option(reference, "timeout")

    def test_missing_description_is_visible(self, reference: str) -> None:
        assert "*No description.*" in _option(reference, "limit")

    def test_nested_mappings_get_their_own_heading(self, reference: str) -> None:
        assert reference.startswith("# Top level\n")
        assert "\n# inner\n" in reference
        assert "\n## depth\n" in reference
        assert "\n## inner\n" not in reference

    def test_list_of_mappings_shows_its_items(self, reference: str) -> None:
        assert "\n# items[]\n" in reference
        assert "\n## host\n" in reference


class TestSchemaReferences:
    @pytest.mark.parametrize(
        ("render", "model"),
        [(global_config_reference, RawGlobalConfig), (project_config_reference, RawProjectYaml)],
        ids=["config.yml", "project.yml"],
    )
    def test_every_option_is_listed(self, render: Any, model: type[BaseModel]) -> None:
        reference = render()
        assert {name for name in _leaf_names(model) if f"\n## {name}\n" not in reference} == set()

    def test_every_terok_option_has_a_description(self) -> None:
        """An option defined in this repository must tell the operator what it does."""
        terok_models = [
            model
            for model in vars(yaml_schema).values()
            if isinstance(model, type)
            and issubclass(model, BaseModel)
            and model.__module__ == yaml_schema.__name__
        ]
        assert terok_models
        undocumented = [
            f"{model.__name__}.{name}"
            for model in terok_models
            for name, field in model.model_fields.items()
            if not field.description
            and not _is_section(field.annotation)
            and not _inherited(model, name)
        ]
        assert undocumented == []


def _is_section(annotation: Any) -> bool:
    """Return whether a field holds a nested mapping, which its own options describe."""
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _inherited(model: type[BaseModel], name: str) -> bool:
    """Return whether *name* comes from a base class of *model*."""
    bases = (base for base in model.__mro__[1:] if issubclass(base, BaseModel))
    return any(name in base.model_fields for base in bases)
