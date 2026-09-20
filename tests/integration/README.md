# Integration Smoke Tests

`scripts/run_smoke.py` runs these targets one by one and reports per-target status plus total duration. It is a smoke check over the mocked local pipeline, not the repository release gate; that gate is `python scripts/check_all.py`, run from the repository root.

| Target | Coverage commitment | External dependency strategy |
|--------|---------------------|------------------------------|
| `test_pipeline_ingest.py` | Message ingestion, extraction preparation, storage handoff | Temporary test database and AstrBot mocks |
| `test_pipeline_event.py` | Event handling, recall/reflection entry points, runtime component wiring | `tests/conftest.py` AstrBot mocks |
| `test_pipeline_retrieval.py` | Document retrieval, graph retrieval, and fusion main path | Local mock embedding/retriever data |
| `test_pipeline_graph.py` | Graph memory write, query, delete consistency | Temporary SQLite/FAISS-style test data |
| `test_pipeline_lifecycle.py` | Initialization, schedulers, maintenance lifecycle | Test config and mock providers |

Run:

```bash
python scripts/run_smoke.py -q
```

Passing this smoke suite means the five pipeline paths can execute in the mocked local environment. It does not replace `python -m pytest tests -q` or Dashboard build/test gates.
