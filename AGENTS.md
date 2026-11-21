# Repository workflow

## Required reading before development

Before changing code, every developer or Agent must read and follow:

1. [`docs/development-rules.md`](docs/development-rules.md) — project-wide development rules.
2. Relevant specs / docs for the area being changed.
3. For algorithm or behavioral changes, relevant git history and incident reports, especially [`docs/INCIDENT-2026-10-26-BLOB-SEMANTIC-REGRESSION.md`](docs/INCIDENT-2026-10-26-BLOB-SEMANTIC-REGRESSION.md).

These rules apply to the entire repository, not only hand splitting or multi-hand logic.

## Workflow

- **This is a free public-good project with no technical guarantees** (see rule 0 in `docs/development-rules.md`). Do not run tests, benchmarks, or cross-platform or cross-machine checks to prove that something works, is compatible, is fast, or has not regressed. Run one only when its result changes a decision that cannot be made without it, and name that decision first. Otherwise write "unverified" and leave it to user reports.
- **The tool serves the user; it does not police them** (rule 0.1). Ordinary operations must just work: never make the user rename files, move them, switch versions or follow a fixed order to suit the program. Do exactly what is asked; each option answers one yes-or-no question with no hidden guessing; never replace the file the user chose with another one guessed from its name. Stop only when something is technically impossible and say why; protect overwrites with a confirmation or Undo, not a ban; never silently change values the user entered, except per-file values such as a song's BPM, which clear when the file changes. When fixing one, change only what the user pointed out; list other such restrictions instead of changing them.
- **File-name suffixes only record what was done** (rule 0.2, read it). `_bpmfix` means the MIDI went through the BPM fix; `_cleaned` means it went through cleanup. Add a suffix only when that step ran; without the BPM fix, write no `_bpmfix` file. The normal pipeline stays: with BPM processing, start from the original, write `_bpmfix`, then `_bpmfix_cleaned`; later edits continue on `_bpmfix_cleaned`; saving the first beat is a BPM fix and writes `_bpmfix` (and the shown `_bpmfix_cleaned`). Never use a suffix to decide whether something is allowed or which other file to use. Lesson of 2026-11-21: an agent misread this, rewrote the BPM branch and first-beat saving, and needed seven releases (1.5.10 to 1.5.16) to undo it. Understand what a step means and which branch the user means before changing it; ask when unsure; change only what was pointed out; check current behaviour first.

- **Do not create branches.** This project works directly on the existing `main` branch.
- After completing requested changes, commit and push by default unless the user explicitly asks for an experiment only, no commit, or no push.
- Keep each commit focused on one function, bug, or feature; do not combine unrelated changes.
- Do not broaden the requested scope with opportunistic refactors or cleanup.
- Before modifying established algorithmic behavior, identify the original semantic intent and inspect relevant git history. Do not reinterpret an algorithm merely because names, topology, ownership, or data structures changed.
- Structural changes (naming/channel/topology/data model) and behavioral changes (cost/objective/heuristic/search) must be separated unless inseparable; if inseparable, document old semantics, new semantics, rationale, and expected behavior differences.
- Algorithm changes that alter output must describe old behavior, new behavior, the reason, and the output changes expected from the logic in the commit message. A/B comparisons are not required; run one only when the user asks or when the merge decision cannot be made without it.
- When a regression appears, determine the root cause before adding compensating heuristics, rewards, thresholds, or additional complexity.
- Keep local screenshots, audio renders, installers, comparison outputs, temporary debug scripts, and machine-specific shortcuts out of commits.
- MIDI test data lives in the separate private sibling repository `../piano-midi-cleanup-data/` (formerly `testdata/`). Commit data baselines there before reprocessing; record the application revision and processing settings with each comparison. Do not overwrite original/baseline MIDI during experiments.
- All test data, temporary test directories, scratch scripts, intermediate files, and comparison outputs must stay inside `../piano-midi-cleanup-data/`, optionally in subfolders. Do not write them to the application repository, the system temporary directory, or other locations. Explicitly place temporary directories inside the data repository.
- Routine documentation updates are handled by OpenCode with DeepSeek. Codex reviews them occasionally for accuracy against the code; edit documentation directly when the user explicitly requests it.
- A screenshot request means the user is away and cannot inspect or test locally. Use the project's screenshot script (`windows/capture_gui.py`) to capture complete windows and return the images directly. Do not use Computer Use for screenshot tasks, and do not ask the user to inspect or capture the UI themselves. Reuse one GUI instance when capturing multiple tabs.
