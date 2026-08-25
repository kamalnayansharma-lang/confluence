"""Topological sort over a sprint's dependency graph -- turns
jira/sprint_planner.py's per-page depends_on_page_ids into a single
execution order. Isolated from the planner itself (which only proposes
edges) and from pipeline/sprint_runner.py (which only consumes the order),
so this piece's correctness -- including the cycle case -- is testable on
its own.
"""

from __future__ import annotations


class DependencyCycleError(Exception):
    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__(f"Dependency cycle detected among pages: {' -> '.join(cycle)}")


def topological_order(depends_on: dict[str, list[str]]) -> list[str]:
    """`depends_on` maps page_id -> the page_ids it depends on (must come
    first). Returns page_ids in an order where every dependency appears
    before its dependent. Deterministic given the same input (ties broken
    by page_id, ascending) so re-running Plan on an unchanged batch
    reproduces the same order rather than an arbitrary one.

    Raises DependencyCycleError rather than hanging or silently dropping
    pages when the planner (or a human editing links directly in Jira)
    produced a cycle -- a plan with a cycle can never be fully executed, so
    this must surface as an error a human fixes before Confirm, not a
    partial/undefined order.
    """
    all_pages = set(depends_on.keys())
    for deps in depends_on.values():
        all_pages.update(deps)

    # Kahn's algorithm -- indegree = number of *unprocessed* dependencies
    # still blocking a page; a page enters the ready set once that hits 0.
    indegree: dict[str, int] = {p: 0 for p in all_pages}
    dependents: dict[str, list[str]] = {p: [] for p in all_pages}
    for page_id, deps in depends_on.items():
        for dep in deps:
            dependents[dep].append(page_id)
            indegree[page_id] += 1

    ready = sorted(p for p, deg in indegree.items() if deg == 0)
    order: list[str] = []
    while ready:
        ready.sort()
        page_id = ready.pop(0)
        order.append(page_id)
        for dependent in dependents[page_id]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)

    if len(order) != len(all_pages):
        remaining = sorted(all_pages - set(order))
        raise DependencyCycleError(remaining)

    return order
