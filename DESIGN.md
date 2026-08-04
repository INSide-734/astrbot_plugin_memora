# Memora Architecture and Design Contract

## Purpose

This document is the repository-level architecture contract for Memora. It consolidates
stable boundaries that are already implemented and links implementation work back to the
module documentation, tests, and feature-specific design records. It is not a replacement
for detailed feature specifications and does not introduce a second adaptive-injection spec.

Memora turns chat events into durable `MemoryAtom` records and retrieves the useful subset
for later requests while keeping storage, prompt cost, privacy, and runtime failure isolated.

## System boundaries

```text
AstrBot event
  -> MemoraPlugin / PluginInitializer
  -> EventHandler
       -> ConversationManager
       -> MemoryProcessor
       -> RecallHandler
       -> ReflectionHandler
  -> MemoryEngine
       -> SQLite stores
       -> BM25 + vector retrieval
       -> FAISS / graph indexes
  -> PluginPageApi
       -> Dashboard bridge
```

`main.py` registers the plugin and hooks. `PluginInitializer` constructs components in
dependency order and owns partial-initialization cleanup. `EventHandler` coordinates message
events but delegates storage, recall, extraction, reflection, and scheduled work to bounded
components. Shutdown is idempotent and closes producers before their stores.

## Memory model and lifecycle

`MemoryAtom` is the durable unit shared by extraction, retrieval, lifecycle management, and
diagnostics. An atom carries typed memory content and allowlisted metadata; indexes are
derived data and can be rebuilt from durable storage.

The normal data path is:

1. Extract normalized message content.
2. Maintain the conversation/session lifecycle.
3. Generate or update MemoryAtom data through the configured processor.
4. Persist through SQLite-backed stores and the coordinated write boundary.
5. Update full-text, vector, and graph-derived indexes.
6. Apply decay, archive, cleanup, and reconstruction through explicit lifecycle services.

SQLite is authoritative for structured durable state. FAISS and graph indexes accelerate
retrieval but must not become the only copy of a memory. Multi-step writes use the shared
write coordinator or a store-local transaction following the same serialization contract.

## Retrieval and adaptive injection

`RecallHandler` remains the request-event orchestrator. It performs content extraction,
query rewriting, persona/session filtering, retrieval, optional auxiliary context, final
routing, execution, and sanitized observability.

Adaptive injection has one strategy path:

- `InjectionStrategyRouter` resolves Manual, Auto, or Hybrid routing deterministically.
- `core/injection/selection.py` owns pure candidate normalization, utility ranking, and
  stable budget selection; `InjectionExecutor` remains the sole orchestration and request
  mutation boundary.
- Built-in presets are Tool First, Low Cost, Balanced, and Quality.
- Preflight may skip passive retrieval only when the current Provider request really exposes
  an active memory tool.
- Final routing uses normalized candidate signals and does not make an extra LLM call.
- `InjectionExecutor` owns utility selection, layered formatting, prompt protection, the
  global hard character budget, Provider delivery adaptation, and atomic request mutation.
- Dynamic memory never enters System Prompt; the normal path uses temporary user content.

The configured budget includes prospective, ordinary-memory, and optional cognitive layers.
The effective budget is additionally clamped by conservative context headroom. Protection
wrappers count toward the same cap. A failed build or delivery leaves the request unchanged.

Decision metadata is persisted in the `injection_decisions` SQLite table through a bounded,
non-blocking recorder. The schema and API response allowlists exclude query text, prompt
text, memory bodies or memory-ID lists, raw user/session/group/persona identities, Provider
credentials/headers/endpoints, and stack traces. Retention applies time expiry first and a
stable newest-row cap second.

The detailed design and execution record remain in the existing adaptive-memory-injection
specification and implementation plan under `docs/superpowers/`; this repository document
only states the stable ownership and safety boundaries.

## Configuration contract

Runtime configuration is a three-layer merge:

```text
AstrBot configuration -> persisted Memora configuration -> code defaults
```

Every public configuration leaf must agree across `_conf_schema.json`, Pydantic models,
runtime readers, Dashboard types/defaults, and contract tests. Load-time tolerance may fall
back from invalid external data, but save APIs must reject invalid candidate configurations.

Configuration writes use revision-protected compare-and-apply semantics. The Dashboard sends
only changed leaves with `base_revision`; conflicts preserve the local draft until the
administrator explicitly accepts or rebases the remote state.

`recall_engine.injection_method` is a deliberate breaking removal. There is no compatibility
migration or dual strategy system; rollback uses the supported Manual + Balanced settings or
a version rollback.

## Page API and Dashboard

`PluginPageApi` composes focused API mixins. Handlers validate a fixed request envelope,
reject unknown fields where required, bind all query values, and return explicit response
allowlists. Internal exceptions may be logged, but raw exception data and sensitive request
content are not returned to the browser.

The Dashboard is a React application embedded through the AstrBot plugin bridge. It preserves
the classic-script single-bundle artifact contract. Shared layout primitives, semantic theme
tokens, accessible Dialog/Sheet controls, true server pagination, stale-response suppression,
and three-language key parity are cross-page requirements.

The Injection Strategy workbench contains Overview, Strategy Configuration, and Decision
History. Decision and trace identifiers may be displayed in a controlled detail surface and
used for one-shot in-memory navigation, but they are not written to the URL, browser storage,
bridge-call logs, or persistent debug state.

## Security model

Memora treats chat content, memory bodies, identities, Provider configuration, and prompt
material as sensitive. The main controls are:

- input normalization and fixed-field validation;
- SQL value binding and allowlisted identifiers;
- prompt-protection wrappers for dynamic injected content;
- output and decision-record allowlists;
- no raw decision payload logging;
- bounded queues, budgets, pagination, and retention;
- cancellation propagation and failure isolation at asynchronous boundaries.

Accepted residual risks are explicit. Prompt wrappers and response cleaning are
defense-in-depth controls; untrusted memory remains untrusted and is never promoted to a
trusted instruction. A process crash can lose the last decision batch that has not reached
SQLite, and sustained recorder failure can make the bounded queue discard its oldest pending
records to protect chat latency and memory use. Decision telemetry remains inside the AstrBot
host-authenticated administration boundary and must not be exposed to ordinary chat users.

Security scanners are evidence, not automatic truth. Findings in fixed SQL identifiers,
test DOM setup, local CLI paths, or non-security randomness require manual data-flow review;
actual user-controlled Critical/High findings block delivery.

## Observability and performance

Metrics use bounded labels and sanitized scalar counts. Recall observability records stage
latency, cache behavior, candidate/selection counts, budget use, fallback outcomes, and
recorder health without logging the source query or selected memory identifiers.

Performance gates include deterministic routing/execution metrics, a real
`RecallHandler.handle_memory_recall` total-path p95 comparison against a recorded baseline,
and a file-backed 100,000-row SQLite decision benchmark. Baselines are versioned artifacts;
they may only be refreshed through an explicit measurement command and reviewed diff.

## Quality and release contract

The unified local gate is:

```powershell
python scripts/check_all.py
```

It runs configuration validation, the backend regression suite, integration smoke tests,
Dashboard production build and artifact validation, frontend tests, runtime smoke, and a real
browser smoke. Browser screenshot contents are inspected manually after the automated run.

Behavior changes follow RED -> GREEN -> REFACTOR. Larger changes additionally run the
repository change and quality checks; persistence, API, prompt, and sensitive-data changes run
the security check. Completion requires fresh outputs for the full scope, a clean Git diff,
and a requirement-by-requirement audit rather than extrapolation from narrow tests.

The quality gate may report parameter-count design warnings on the fixed utility formula,
preset override resolver, recorder dependency-injection constructor, and internal routing or
payload assembly helpers. These signatures intentionally keep the approved scalar formula,
Schema leaves, clock/sleep test seams, and immutable routing inputs explicit. Replacing them
with single-use parameter-holder objects would add coupling without reducing behavior or
complexity; complexity, naming, duplication, and oversized-function findings remain blocking.

Release notes must identify breaking configuration changes, operational loss windows, default
retention, and rollback settings. Feature work is committed by concern and never stages
unrelated local artifacts.

## Authoritative references

- [Package-by-Feature migration baseline](docs/architecture/package-by-feature/README.md):
  proposed dependency rules, per-file matrix, staged runbook, and acceptance gates for
  AST-6 review.
- Root and module `CLAUDE.md` files: current ownership and implementation notes.
- `website/docs/development/`: environment and gate commands.
- `docs/superpowers/specs/`: approved feature designs.
- `docs/superpowers/plans/`: executable implementation plans.
- `tests/` and `scripts/check_all.py`: executable repository contract.
