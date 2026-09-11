# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the curated egress-set registry and its resolution."""

from __future__ import annotations

import pytest

from terok.lib.core.egress_sets import (
    EGRESS_SETS,
    OS_PACKAGES_SET,
    RECOMMENDED_SET,
    describe_egress_sets,
    resolve_egress_sets,
    selected_egress_sets,
    validate_egress_sets,
)


def test_registry_shape() -> None:
    """Every static set carries hosts; only ``os-packages`` is dynamic (empty here)."""
    for name, hosts in EGRESS_SETS.items():
        assert name == name.lower()
        if name == OS_PACKAGES_SET:
            assert hosts == ()
        else:
            assert hosts, f"static set {name} has no hosts"
            assert len(hosts) == len(set(hosts)), f"duplicate hosts in {name}"


def test_recommended_shares_the_flat_namespace_without_shadowing_a_set() -> None:
    """Users write ``recommended`` like any set name, so no concrete set may take it."""
    assert RECOMMENDED_SET not in EGRESS_SETS


@pytest.mark.parametrize("names", [None, ()], ids=["unset", "empty"])
def test_selection_without_names_grants_nothing(names: tuple[str, ...] | None) -> None:
    """Unset and an explicit empty list both grant no curated set."""
    assert selected_egress_sets(names) == ()


def test_recommended_expands_to_every_set_in_registry_order() -> None:
    assert selected_egress_sets((RECOMMENDED_SET,)) == tuple(EGRESS_SETS)


def test_recommended_includes_sets_added_to_the_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """The meta-set is not a snapshot: a set the registry gains is granted too."""
    monkeypatch.setitem(EGRESS_SETS, "new-set", ("new.example.org",))
    assert selected_egress_sets((RECOMMENDED_SET,))[-1] == "new-set"


def test_selection_drops_repeats_keeping_first_position() -> None:
    """``[recommended, python]`` names python twice; it is granted once."""
    assert selected_egress_sets((RECOMMENDED_SET, "python")) == tuple(EGRESS_SETS)
    others = tuple(n for n in EGRESS_SETS if n != "python")
    assert selected_egress_sets(("python", RECOMMENDED_SET)) == ("python", *others)


def test_explicit_selection_is_granted_as_listed() -> None:
    assert selected_egress_sets(("go", "python")) == ("go", "python")


def test_describe_renders_the_authored_value() -> None:
    """``recommended`` stays as written; unset and empty share one wording."""
    assert describe_egress_sets((RECOMMENDED_SET,)) == RECOMMENDED_SET
    assert describe_egress_sets(("python", "go")) == "python, go"
    assert describe_egress_sets(None) == describe_egress_sets(())
    assert describe_egress_sets(None).startswith("none")


def test_validate_accepts_known_recommended_and_none() -> None:
    validate_egress_sets(None)
    validate_egress_sets(())
    validate_egress_sets(tuple(EGRESS_SETS))
    validate_egress_sets((RECOMMENDED_SET, "python"))


def test_validate_rejects_unknown_with_available_names() -> None:
    """A typo fails loudly and spells out every accepted name, ``recommended`` included."""
    with pytest.raises(SystemExit, match="Available sets") as exc_info:
        validate_egress_sets(("pythn",))
    message = str(exc_info.value)
    assert RECOMMENDED_SET in message
    assert all(name in message for name in EGRESS_SETS)


def test_resolve_none_grants_no_hosts() -> None:
    """An unset ``shield.sets`` resolves to no curated hosts at all."""
    assert resolve_egress_sets(None, "deb") == ()


def test_resolve_empty_grants_no_hosts() -> None:
    assert resolve_egress_sets((), "rpm") == ()


def test_resolve_recommended_is_the_union_of_every_set() -> None:
    """``recommended`` resolves exactly what enumerating the whole registry resolves."""
    from terok.lib.integrations.executor import package_repo_hosts

    hosts = resolve_egress_sets((RECOMMENDED_SET,), "deb")
    assert hosts == resolve_egress_sets(tuple(EGRESS_SETS), "deb")
    for name, static_hosts in EGRESS_SETS.items():
        if name != OS_PACKAGES_SET:
            assert set(static_hosts) <= set(hosts)
    assert set(package_repo_hosts("deb")) <= set(hosts)


def test_resolve_subset_is_exactly_its_hosts() -> None:
    name = next(n for n in EGRESS_SETS if n != OS_PACKAGES_SET)
    assert resolve_egress_sets((name,), None) == EGRESS_SETS[name]


def test_resolve_os_packages_follows_family() -> None:
    """The dynamic set delegates to executor's family-keyed repo data."""
    from terok.lib.integrations.executor import package_repo_hosts

    assert resolve_egress_sets((OS_PACKAGES_SET,), "rpm") == package_repo_hosts("rpm")
    # Unrecognized image → the all-family union.
    assert resolve_egress_sets((OS_PACKAGES_SET,), None) == package_repo_hosts(None)
