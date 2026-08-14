# Security Design

## Scope

`core/security/` protects Memora's LLM-facing memory injection pipeline and structured output validation path. It does not implement user authentication, transport encryption, or host-level sandboxing. Those concerns belong to AstrBot or the deployment environment.

## Threat Model

This module is designed to reduce the impact of the following threats:

- Prompt injection disclosure: injected memory or internal steering text being echoed back to end users.
- Unsafe LLM output structure: malformed JSON or schema-breaking responses from the model corrupting downstream logic.
- SQL identifier misuse in adjacent persistence code: internal table or trigger names drifting from fixed constants into dynamically controlled values.
- Stored-content leakage amplification: previously captured memory fragments being repeated verbatim by the model.

This module is not intended to defend against:

- A compromised host runtime, Python process, or AstrBot core.
- A malicious plugin with arbitrary local filesystem or database access.
- A fully adversarial LLM provider that ignores all instructions and output post-processing.

## Trust Boundaries

- Trusted:
  - Static code in `core/security/`.
  - Internal allowlisted SQLite identifiers in persistence code.
  - Pydantic schema definitions used by `guardrails.py`.
- Untrusted:
  - User messages and group chat content.
  - Memory text recovered from storage.
  - Raw LLM outputs before validation and sanitization.
  - Any future config value that could influence SQL identifiers or injected prompt templates.

## Security Decisions

### Prompt wrapping

`MetaInstructionWrapper` adds non-user-facing wrapper tags around injected content. The wrapper is a defense-in-depth signal to the model, not a sole protection mechanism.

Why:
- It lowers accidental disclosure rates on cooperative models.
- It keeps the instruction boundary explicit for downstream sanitization.

### Output sanitization

`ResponseSanitizer` removes leaked internal tags, leak-indicator phrases, and registered instruction fragments from model output.

Why:
- Model obedience is probabilistic.
- Post-processing is required even when upstream prompting is careful.

### Multi-algorithm validation

`DoubleCheckValidator` uses Jaccard, sequence similarity, LCS, and n-gram overlap to detect partial leakage after sanitization.

Why:
- Exact matching misses paraphrased or truncated leakage.
- Multiple cheap heuristics are more robust than a single score.

### Structured output guardrails

`guardrails.py` validates LLM-generated JSON and schema-constrained payloads before they reach storage or graph logic.

Why:
- Model output is not trusted to be valid JSON or shape-safe.
- Failing closed at the validation layer is cheaper than repairing corrupted state later.

### SQLite identifier allowlists

Persistence code in adjacent modules now validates dynamic SQL identifiers against small fixed allowlists before quoting them into DDL or FTS statements.

Why:
- SQLite parameters cannot bind table names, index names, or trigger names.
- Allowlisting prevents future config drift from turning internal f-strings into real injection surfaces.

## Known Risks

- Prompt protection remains best-effort. A sufficiently misaligned model may still paraphrase sensitive context in ways heuristics do not catch.
- Sanitization currently uses deterministic text rules and similarity thresholds, which may over-filter or under-filter edge cases.
- `random.choice()` is still used for suffix variation. This is acceptable because it is not used for secrets, tokens, or security-sensitive randomness.
- The dashboard frontend still has no dedicated XSS-oriented test coverage, even though the reviewed `innerHTML = ""` usage is currently a safe container reset.

## Operational Notes

- Changes to wrapper templates, leak keywords, or validation thresholds should be accompanied by updates to `tests/test_prompt_sanitizer.py`.
- Any new persistence code that interpolates identifiers should reuse allowlist-plus-quoting helpers rather than raw f-strings.
- Security-significant behavior changes should be reflected in this document so `verify-security` remains traceable.
