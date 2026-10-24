# Repository workflow

- After completing requested changes, commit and push by default unless the user asks otherwise.
- Keep each commit focused on one function or feature; do not combine unrelated changes into a single commit.
- Keep local screenshots, audio renders, installers, comparison outputs, and machine-specific shortcuts out of commits.
- Routine documentation updates are handled by OpenCode with DeepSeek. Codex reviews them occasionally for accuracy against the code; edit documentation directly when the user explicitly requests it.
- A screenshot request means the user is away and cannot inspect or test locally. Use the project's screenshot script (`windows/capture_gui.py`) to capture complete windows and return the images directly. Do not use Computer Use for screenshot tasks, and do not ask the user to inspect or capture the UI themselves. Reuse one GUI instance when capturing multiple tabs.
