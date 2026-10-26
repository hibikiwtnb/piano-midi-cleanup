# Repository workflow

## Required reading before development

Before changing code, every developer or Agent must read and follow:

1. [`docs/development-rules.md`](docs/development-rules.md) — project-wide development rules.
2. Relevant specs / docs for the area being changed.
3. For algorithm or behavioral changes, relevant git history and incident reports, especially [`docs/INCIDENT-2026-10-26-BLOB-SEMANTIC-REGRESSION.md`](docs/INCIDENT-2026-10-26-BLOB-SEMANTIC-REGRESSION.md).

These rules apply to the entire repository, not only hand splitting or multi-hand logic.

## Workflow

- **Do not create branches.** This project works directly on the existing `main` branch.
- After completing requested changes, commit and push by default unless the user explicitly asks for an experiment only, no commit, or no push.
- Keep each commit focused on one function, bug, or feature; do not combine unrelated changes.
- Do not broaden the requested scope with opportunistic refactors or cleanup.
- Before modifying established algorithmic behavior, identify the original semantic intent and inspect relevant git history. Do not reinterpret an algorithm merely because names, topology, ownership, or data structures changed.
- Structural changes (naming/channel/topology/data model) and behavioral changes (cost/objective/heuristic/search) must be separated unless inseparable; if inseparable, document old semantics, new semantics, rationale, expected behavior differences, and regression coverage.
- Algorithm changes require behavior-level regression tests and/or representative A/B comparisons. Passing unit tests or producing plausible-looking output is not sufficient.
- When a regression appears, determine the root cause before adding compensating heuristics, rewards, thresholds, or additional complexity.
- Keep local screenshots, audio renders, installers, comparison outputs, temporary debug scripts, and machine-specific shortcuts out of commits.
- MIDI test data lives in the separate private sibling repository `../piano-midi-cleanup-data/` (formerly `testdata/`). Commit data baselines there before reprocessing; record the application revision and processing settings with each comparison. Do not overwrite original/baseline MIDI during experiments.
- All test data, temporary test directories, scratch scripts, intermediate files, and comparison outputs must stay inside `../piano-midi-cleanup-data/`, optionally in subfolders. Do not write them to the application repository, the system temporary directory, or other locations. Explicitly place temporary directories inside the data repository.
- Routine documentation updates are handled by OpenCode with DeepSeek. Codex reviews them occasionally for accuracy against the code; edit documentation directly when the user explicitly requests it.
- A screenshot request means the user is away and cannot inspect or test locally. Use the project's screenshot script (`windows/capture_gui.py`) to capture complete windows and return the images directly. Do not use Computer Use for screenshot tasks, and do not ask the user to inspect or capture the UI themselves. Reuse one GUI instance when capturing multiple tabs.
