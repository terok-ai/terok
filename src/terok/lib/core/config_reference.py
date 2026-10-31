# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Renders the config schema as Markdown, for the TUI's options reference.

The pydantic models in [`yaml_schema`][terok.lib.core.yaml_schema] hold the
options, their types, defaults and descriptions.  Every option shows up
here without a separate list to keep in step.
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import Enum
from types import NoneType, UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

_TYPE_NAMES = {str: "text", int: "integer", float: "number", bool: "true or false"}
_NO_DESCRIPTION = "*No description.*"


def render_config_reference(model: type[BaseModel]) -> str:
    """Return every option of *model* as Markdown, one heading per YAML mapping."""
    return "\n\n".join(filter(None, map(_render_mapping, _mappings(model)))) + "\n"


def global_config_reference() -> str:
    """Return the Markdown reference for ``config.yml``."""
    from .yaml_schema import RawGlobalConfig

    return render_config_reference(RawGlobalConfig)


def project_config_reference() -> str:
    """Return the Markdown reference for ``project.yml``."""
    from .yaml_schema import RawProjectYaml

    return render_config_reference(RawProjectYaml)


def _mappings(model: type[BaseModel], path: str = "") -> Iterator[tuple[str, type[BaseModel]]]:
    """Yield *model* and each mapping nested in it, with the dotted path that leads there."""
    yield path, model
    for name, field in model.model_fields.items():
        match _nested_model(field.annotation):
            case (nested, is_list):
                yield from _mappings(nested, f"{path}{name}{'[]' if is_list else ''}.")


def _render_mapping(mapping: tuple[str, type[BaseModel]]) -> str:
    """Render the options of one mapping under its heading; empty when it only holds mappings."""
    path, model = mapping
    options = [
        _render_option(name, field)
        for name, field in model.model_fields.items()
        if _nested_model(field.annotation) is None
    ]
    if not options:
        return ""
    return "\n\n".join([f"# {path.rstrip('.') or 'Top level'}", *options])


def _render_option(name: str, field: FieldInfo) -> str:
    """Render one option: its key, its type and default, and its description."""
    return (
        f"## {name}\n\n"
        f"{_type_text(field.annotation)} · {_default_text(field)}\n\n"
        f"{field.description or _NO_DESCRIPTION}"
    )


def _nested_model(annotation: Any) -> tuple[type[BaseModel], bool] | None:
    """Return the model a field holds and whether it holds a list of them, or ``None``."""
    match get_origin(annotation), get_args(annotation):
        case origin, args if origin in (Union, UnionType):
            present = [arg for arg in args if arg is not NoneType]
            return _nested_model(present[0]) if len(present) == 1 else None
        case origin, (item,) if origin is list:
            nested = _nested_model(item)
            return (nested[0], True) if nested else None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation, False
    return None


def _type_text(annotation: Any) -> str:
    """Return a short description of what a field accepts."""
    match get_origin(annotation), get_args(annotation):
        case origin, args if origin in (Union, UnionType):
            return " or ".join(_type_text(arg) for arg in args if arg is not NoneType)
        case origin, args if origin is Literal:
            return "one of " + ", ".join(f"`{arg}`" for arg in args)
        case origin, (item,) if origin is list:
            return f"list of {_type_text(item)}"
        case origin, _ if origin is dict:
            return "mapping"
    return _TYPE_NAMES.get(annotation, getattr(annotation, "__name__", str(annotation)).lower())


def _default_text(field: FieldInfo) -> str:
    """Return the default of a field as the YAML that would spell it."""
    if field.is_required():
        return "required"
    default = field.get_default(call_default_factory=True, validated_data={})
    match default:
        case None:
            return "unset by default"
        case BaseModel():
            return "default: the section's defaults"
        case bool():
            return f"default `{str(default).lower()}`"
        case Enum():
            return f"default `{default.value}`"
        case str() | list() | dict() if not default:
            return "empty by default"
        case _:
            return f"default `{default}`"
