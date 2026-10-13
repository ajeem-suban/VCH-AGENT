# Architecture Audit

## Current State

The repository contains project documentation and Git metadata only. There is no application code, dependency manifest, virtual environment, database, test suite, or existing implementation pattern to preserve.

## Proposed First Slice

Build a small Python package that accepts a normalized content item, obtains five semantic scores through an injectable feature extractor, validates those scores, and persists the result in SQLite.

The first implementation uses a deterministic fixture extractor for tests and offline development. The local `llama-cpp-python` adapter is optional, lazy-loaded, and configured through environment variables; no model download is required for this slice.

## Initial Structure

```text
src/vch/
  domain.py          # Typed content and semantic-feature models
  database.py        # SQLite schema and persistence
  extractors.py      # Feature-extractor protocol and fixture implementation
  service.py         # Small orchestration layer
tests/
```

## Decisions

- Use SQLite directly via Python's standard library: it is sufficient for one local process and avoids an ORM before query complexity justifies one.
- Use Pydantic for strict input/output validation: malformed or out-of-range LLM values fail clearly.
- Keep LLM inference behind one small protocol: it keeps production inference replaceable and unit tests fast, without introducing a framework.
- Do not create prediction, harvesting, tracking, model-registry, or scheduler modules yet: those need data that the first slice will produce.

## Migration Path

1. Implement and test structured semantic features plus persistence.
2. Add a configured local LLM adapter after runtime compatibility and model placement are verified.
3. Add Reddit harvesting and normalized content persistence. (Implemented with an optional PRAW client.)
4. Add YouTube RSS harvesting and normalized content persistence. (Implemented with built-in HTTP/XML support.)
5. Add snapshots and outcomes before training a predictive model.

## Slice 5: measurement foundations

`metric_snapshots` is append-only. A snapshot records the time VCH observed a
source, not the time it was published. Harvest and scan commands store an honest
T0 snapshot using the metrics already supplied by the collector: Reddit score,
comment count and upvote ratio; or the YouTube Atom feed's view count when it is
present. Missing source values remain `NULL`, never zero.

`outcomes` has one logical row per content item and horizon (currently any
positive number of hours, with 24 and 72 intended for normal use). It links a
baseline snapshot to a later follow-up snapshot and records pending, observed,
unavailable, or failed status. Relative growth is undefined for zero baselines;
absolute change remains available.

The persistence guard rejects a follow-up snapshot earlier than the outcome's
target time. Prediction records also reject feature references extracted after
their prediction timestamp. These rules prevent later observations being used as
T0 data. Semantic dimensions remain uncalibrated inputs, not virality
probabilities or a trained model.

The public YouTube Atom feed is useful for discovery and its published view
count, but it is not represented as a reliable per-video follow-up API. The
outcome service is intentionally collector-injected so an optional, explicitly
configured platform API can be added without fabricating values.

Reddit follow-ups use the existing optional PRAW credentials and stored
submission ID. Attempts append a `follow_up` snapshot with actual UTC time.
Transient failures are retryable after restart; unavailable content is terminal.
Snapshot roles distinguish original T0 observations from later observations.

The initial Reddit scan path now persists source content before local semantic-feature extraction. This preserves the T0 input even if an individual local LLM response is invalid.

## Risks

- The documentation files are incomplete and do not yet describe a runnable setup.
- `llama-cpp-python` may need a supported Python runtime/build configuration on Windows; it will be evaluated before being made a required dependency.
- There is no historical dataset, so no probability or performance result can be claimed yet.
