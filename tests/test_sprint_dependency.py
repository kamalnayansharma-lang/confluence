from __future__ import annotations

import pytest

from confluence_pr_agent.jira.sprint_dependency import DependencyCycleError, topological_order


def test_independent_pages_come_back_sorted_by_id():
    order = topological_order({"p3": [], "p1": [], "p2": []})
    assert order == ["p1", "p2", "p3"]


def test_a_dependency_always_precedes_its_dependent():
    order = topological_order({"p1": [], "p2": ["p1"], "p3": ["p2"]})
    assert order.index("p1") < order.index("p2") < order.index("p3")


def test_diamond_dependency_resolves_correctly():
    # p4 depends on both p2 and p3, which both depend on p1.
    order = topological_order({"p1": [], "p2": ["p1"], "p3": ["p1"], "p4": ["p2", "p3"]})
    assert order.index("p1") < order.index("p2")
    assert order.index("p1") < order.index("p3")
    assert order.index("p2") < order.index("p4")
    assert order.index("p3") < order.index("p4")


def test_a_page_named_only_as_a_dependency_is_still_included():
    # p1 never appears as a dict key, only inside p2's depends_on list.
    order = topological_order({"p2": ["p1"]})
    assert set(order) == {"p1", "p2"}
    assert order.index("p1") < order.index("p2")


def test_direct_cycle_raises_instead_of_hanging():
    with pytest.raises(DependencyCycleError):
        topological_order({"p1": ["p2"], "p2": ["p1"]})


def test_longer_cycle_raises():
    with pytest.raises(DependencyCycleError):
        topological_order({"p1": ["p2"], "p2": ["p3"], "p3": ["p1"]})


def test_empty_input_returns_empty_order():
    assert topological_order({}) == []
