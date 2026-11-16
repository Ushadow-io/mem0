"""Graph enrichment must never read another user's Neo4j nodes or relations.

Uses a fake Neo4j session that emulates the user filter: it only returns rows
for the user_id parameter, and only if the Cypher actually contains the filter.
"""
import asyncio
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "graph_enrichment",
    Path(__file__).parent.parent / "app" / "services" / "graph_enrichment.py",
)
graph_enrichment = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(graph_enrichment)

# (owner user_id, node name) stored in the fake graph
NODES = {"alice": "Josephine", "bob": "Josephine"}


class FakeSession:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, query, params=None, **kwargs):
        params = {**(params or {}), **kwargs}
        self.log.append((query, params))
        scoped = "user_id = $user_id" in query and "user_id" in params
        if not scoped:
            # Unscoped query would leak every user's rows
            owners = list(NODES)
        else:
            owners = [params["user_id"]]
        if "type(r) as relation" in query:
            return [
                {"source": "Josephine", "relation": "KNOWS", "target": f"friend-of-{o}",
                 "source_labels": ["person"], "target_labels": ["person"], "properties": {}}
                for o in owners
            ]
        if "all_labels" in query:
            return [
                {"name": "Josephine", "all_labels": [["person"]],
                 "all_properties": [{"owner": o}], "total_mentions": 1, "first_created": 1}
                for o in owners
            ][:1] if owners else []
        return _Single(owners, params)


class _Single:
    def __init__(self, owners, params):
        self.owners = owners

    def single(self):
        if len(self.owners) != 1:
            return {"entity_labels": ["person"], "entity_props": {"owner": "LEAK"}, "relationships": []}
        return {"entity_labels": ["person"], "entity_props": {"owner": self.owners[0]}, "relationships": []}


class FakeDriver:
    def __init__(self):
        self.log = []

    def session(self):
        return FakeSession(self.log)


def _service():
    svc = graph_enrichment.GraphEnrichmentService.__new__(graph_enrichment.GraphEnrichmentService)
    svc.driver = FakeDriver()
    return svc


def test_enrich_memory_queries_are_user_scoped():
    svc = _service()
    memory = {"id": "1", "memory": "Josephine likes tea"}
    out = asyncio.run(svc.enrich_memory(memory, "alice"))
    assert len(svc.driver.log) == 2  # entity query + relationship query
    for query, params in svc.driver.log:
        assert "user_id = $user_id" in query
        assert params["user_id"] == "alice"
    rel_query = svc.driver.log[1][0]
    assert "e1.user_id = $user_id" in rel_query and "e2.user_id = $user_id" in rel_query
    assert [r["target"] for r in out["relationships"]] == ["friend-of-alice"]
    assert all(e["properties"] == {"owner": "alice"} for e in out["entities"])


def test_user_b_cannot_see_user_a_data():
    svc = _service()
    out = asyncio.run(svc.enrich_memories([{"id": "1", "memory": "Josephine"}], "bob"))
    assert all("alice" not in r["target"] for r in out[0]["relationships"])
    assert all(e["properties"] == {"owner": "bob"} for e in out[0]["entities"])


def test_get_entity_context_is_user_scoped():
    svc = _service()
    ctx = asyncio.run(svc.get_entity_context("Josephine", "bob"))
    query, params = svc.driver.log[0]
    assert "e.user_id = $user_id" in query
    assert "related.user_id = $user_id" in query
    assert params["user_id"] == "bob"
    assert ctx["properties"] == {"owner": "bob"}


def test_user_id_is_required():
    import inspect
    for fn in (graph_enrichment.GraphEnrichmentService.enrich_memory,
               graph_enrichment.GraphEnrichmentService.enrich_memories,
               graph_enrichment.GraphEnrichmentService.get_entity_context):
        assert inspect.signature(fn).parameters["user_id"].default is inspect.Parameter.empty
