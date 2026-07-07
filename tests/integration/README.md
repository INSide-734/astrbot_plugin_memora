# Integration Smoke Tests

`scripts/run_smoke.py` runs these targets one by one and reports per-target status plus total duration. The suite is the L1 release gate for Memora's real backend pipeline.

| Target | Coverage commitment | External dependency strategy |
|--------|---------------------|------------------------------|
| `test_pipeline_ingest.py` | Message ingestion, extraction preparation, storage handoff | Temporary test database and AstrBot mocks |
| `test_pipeline_event.py` | Event handling, recall/reflection entry points, runtime component wiring | `tests/conftest.py` AstrBot mocks |
| `test_pipeline_retrieval.py` | Document retrieval, graph retrieval, fusion/rerank main path | Local mock embedding/retriever data |
| `test_pipeline_graph.py` | Graph memory write, query, delete consistency | Temporary SQLite/FAISS-style test data |
| `test_pipeline_lifecycle.py` | Initialization, schedulers, maintenance lifecycle | Test config and mock providers |

Run:

```bash
python scripts/run_smoke.py -q
```

Passing this smoke suite means the five pipeline paths can execute in the mocked local environment. It does not replace `python -m pytest tests -q` or Dashboard build/test gates.
