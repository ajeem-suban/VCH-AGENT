# VCH-AGENT

VCH-Agent is a local-first system for researching whether fresh Reddit and
YouTube content will outperform its expected future engagement baseline.

## Current slice

The first backend slice accepts normalized content, validates five semantic
signals, and stores both in SQLite. It includes an optional, lazy-loaded
`llama-cpp-python` adapter for a local GGUF model. It deliberately does
**not** claim a viral probability or model performance.

Semantic signals: `hook`, `emotion`, `utility`, `surprise`, and `controversy`.
Each is a strict integer from 1 through 10.

## Setup

VCH currently requires Python 3.10 or later.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Copy `.env.example` to `.env` when configuration is needed. No external API,
network connection, or downloaded LLM model is required for the current test
suite.

## Local LLM setup

Install the optional runtime only after choosing a compatible Windows CUDA
wheel or build for your Python environment:

```powershell
python -m pip install -e ".[local-llm]"
```

Set `VCH_LLM_MODEL_PATH` in `.env` to an existing Phi-3.5-mini-Instruct GGUF
file. The adapter loads one model only when it is first used, requests JSON
mode, and retries malformed output once. Start with the values in
`.env.example`; benchmark VRAM usage before increasing GPU layers or context.

## Reddit setup

Install the optional PRAW client, copy the Reddit values from `.env.example`
into `.env`, and provide the credentials granted to your Reddit application:

```powershell
python -m pip install -e ".[reddit]"
```

`RedditHarvester` collects one broad fresh `new` candidate pool across the
configured subreddits, filters it by `VCH_REDDIT_MAX_AGE_HOURS`, normalizes
the source fields and current engagement state, and returns it for SQLite
persistence.
Invalid individual submissions are skipped; a listing/API failure is reported
without exposing credentials.

## Harvest command

After installing the project and configuring `.env`, run:

```powershell
vch harvest reddit
```

Or, before installing the package:

```powershell
$env:PYTHONPATH = "src"
python -m vch harvest reddit
```

The command initializes the configured SQLite database, fetches the candidate
pool, and persists its normalized content. It reports failures with a nonzero
exit code and does not print credentials.

## Scan command

With both the `reddit` and `local-llm` extras installed and `.env` configured:

```powershell
vch scan reddit
```

The scan keeps one local LLM instance for the entire candidate pool, stores
content before analysis, and appends a validated feature snapshot for each
successful candidate. A malformed response for one candidate does not discard
the rest of the scan. This is semantic-feature extraction only, not a viral
probability or an evaluated prediction.

## YouTube setup

Set one or more public channel IDs in `VCH_YOUTUBE_CHANNEL_IDS` in `.env`:

```powershell
vch harvest youtube
vch scan youtube
```

YouTube harvesting uses public Atom feeds and does not require an API key. VCH
merges fresh entries across all configured channels before applying the global
candidate limit. The RSS feed is used for discovery and metadata only;
transcript retrieval and later engagement snapshots are separate phases.

## Test

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## Design notes

`ARCHITECTURE_AUDIT.md` explains the initial architecture and its incremental
path toward harvesting, tracking, and evaluated predictive ML.
