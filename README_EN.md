# Pianotrans Run Tools

[Chinese README](README.md)

This folder contains Pianotrans transcription entrypoints with added Apple GPU / MPS support, plus post-processing tools for BPM fixing, MIDI statistics, conservative rule-based cleanup, provisional hand-splitting / MIDI channel marking, and MIDI review export. All `.command` files are intended to be double-clickable.

## Main Pipeline

### `pianotrans.command`

Main transcription entrypoint. Double-click it, then choose one or more audio files. MIDI files are written next to the selected audio files.

Device selection order:

```text
CUDA -> CPU
```

Pipeline:

```text
Pianotrans inference
-> write original MIDI
-> write MIDI stats
-> detect BPM from source audio
-> write BPM-fixed MIDI
-> rule-based conservative cleanup
-> provisional hand split by MIDI channel
```

Output files:

```text
song.mid
song_stats.txt
song_bpmfix.mid
song_bpmfix_cleaned.mid
```

`song_stats.txt` contains the original MIDI quality stats, BPMFix stats, cleanup stats, and hand-split stats.

If `song.mid` already exists, the pipeline does not run transcription again. It only fills in missing stats, BPM-fixed MIDI, and cleaned MIDI.

### `pianotrans_mps.command`

Preserved Apple GPU / MPS-capable entrypoint. It runs the same pipeline as `pianotrans.command`, but uses this device selection order:

```text
MPS -> CUDA -> CPU
```

On the tested M2 MacBook Air, this Pianotrans model was slower on MPS than on CPU, so `pianotrans.command` is the recommended daily entrypoint.

## Standalone Tools

### `bpmfix.command`

BPM fixing plus first beat adjustment.

Usage:

1. If you already know the BPM, enter it in the terminal; otherwise press Return and Essentia detects a fixed BPM from the source audio.
2. Choose the source audio file (used for BPM detection and for listening to the first beat).
3. Choose the MIDI file to fix. The tool writes `*_bpmfix.mid`.
4. The first beat page opens in the browser: play the source audio with a metronome, press "設為播放位置" at the first downbeat, fine-tune with +-1 beat and +-10 ms, then press "寫入 MIDI" to overwrite `*_bpmfix.mid` in place. "取消" leaves it unchanged.

`first_beat_ui.py` serves the page from a temporary local HTTP server on `127.0.0.1` that stops after save or cancel, so no extra MIDI file is created. Saving moves the whole MIDI earlier so the chosen time lands on a bar line; if pickup notes would start before 0, whole bars are added in front. BPM, channels and tick-0 meta events are unchanged. The first beat result is appended to `*_stats.txt`.

The `pianotrans.command` pipeline only runs the BPM fix and does not open the first beat page.

Automatic BPM detection estimates the actual BPM from the half-beat phase coherence of the Essentia beats (robust to missed beats and off-beat jumps), then checks the grid drift at the end of the song if the BPM is rounded to an integer:

```text
end_drift_ms = duration * |beat_phase_bpm / applied_bpm - 1| * 1000
```

If the drift is <= 50 ms, the integer BPM is applied; otherwise two decimals are kept. The report shows the raw Essentia BPM, the beat-phase BPM and coherence, whether rounding was applied, and `Estimated end drift`. Manual BPM input is used as entered.

The fix keeps the real playback speed and rewrites MIDI ticks plus tempo metadata:

```text
new_ticks = old_ticks * detected_bpm / 120
```

### `midi_stats.command`

Runs MIDI quality statistics only.

Usage:

1. Choose a MIDI file.
2. The tool writes `*_stats.txt`.

Statistics:

```text
Total notes
Very short notes (< 0.050s)
Low velocity notes (<= 30)
Lowest note
Highest note
Pitch span
Average velocity
Max polyphony
```

Pitch values include both MIDI note number and English pitch name, for example `24 (C1)`.

### `clean_midi_rules.command`

Runs first-stage conservative rule-based cleanup only.

Usage:

1. Choose a MIDI file.
2. The tool writes `*_cleaned.mid`.
3. The tool writes `*_clean_report.txt`.

When run standalone, `clean_midi_rules.command` writes `*_clean_report.txt`. In the full `pianotrans.command` pipeline, the cleanup report is appended to the original `song_stats.txt`.

The cleanup step removes only highly likely artifacts. It does not perform musical semantic judgment, hand splitting, quantization, chord analysis, key analysis, or melody analysis.

Current rules:

```text
duration < 0.03s
velocity < 20
velocity < 35 and duration < 0.08s
pitch outside 21-108
```

Cleanup report example:

```text
MIDI rule cleaning stats
Input: song_bpmfix.mid
Output: song_bpmfix_cleaned.mid
Original notes: 1000
Removed notes: 35 (3.50%)
Kept notes: 965 (96.50%)
Rule removals:
- Duration < 0.030s: 12 (1.20%)
- Velocity < 20: 3 (0.30%)
- Velocity < 35 and duration < 0.080s: 18 (1.80%)
- Pitch outside 21-108: 2 (0.20%)
```

All percentages use `Original notes` as the denominator.

### `hand_split_channels.command`

Marks provisional left/right hand assignment directly on a cleaned MIDI file by changing note channels only.

It does not create separate instrument tracks and does not remove notes.

Assignment is a cost-minimizing beam search. Notes whose onsets are within 50 ms form one group; each group is sorted by pitch and only "lowest k notes to the left hand, the rest to the right hand" splits are considered. The lowest-cost path over the whole song is chosen. Cost terms:

```text
Hand span: free up to 12 semitones, heavily penalized beyond 14
Register: left hand above D4, right hand below G3, by distance
Movement speed: change of hand position divided by time since that hand's previous onset (an idle hand moves cheaply)
Fast runs: once a hand has played 3 or more consecutive single notes each < 0.25 s apart, each extra simultaneous note costs more (a dyad inside a fast right-hand single-note run tends to go to an idle left hand)
Crossing: left-hand top note above right-hand bottom note
```

The report shows the number of groups with a hand span over 12 semitones and the number of multi-note hands left inside fast runs. All weights are constants at the top of the file.

Channel mapping:

```text
MIDI channel 1 = right hand
MIDI channel 3 = left hand
```

For libraries such as `mido`, this means:

```text
channel 0 = MIDI channel 1
channel 2 = MIDI channel 3
```

This step is intended to make the MIDI easier to inspect and edit in Logic Pro score view.

### `generate_midi_review.py`

Work in progress: the output is not final. It is not part of the default pipeline; run it manually only when explicitly needed.

Reads a processed MIDI and writes a compact English Markdown score facts table. It does not modify the MIDI.

Command:

```bash
python generate_midi_review.py input.mid
```

Default output:

```text
song_midi_review.md
```

Current output structure:

```text
Global Info
Notes
```

Detailed spec:

```text
MIDI_REVIEW_SPEC.md
```

### `generate_chord_review.command`

Work in progress: the output is not final. It is not part of the default pipeline; run it manually only when explicitly needed.

Reads a processed MIDI and writes an English Markdown vertical chord chart. It does not modify the MIDI.

Command:

```bash
python generate_chord_review.py input.mid
```

Default output:

```text
song_chord_review.md
```

Table columns:

```text
Bar
Beat
Chord
Bass
LH
RH
Range
NoteCount
```

Each bar has 4 fixed rows. If multiple onset events appear inside the same beat, `Beat` and `Chord` are separated with `;`. Chord detection uses vendored Tonal.js and follows pianometer's `Tonal.Chord.detect(notes, { assumePerfectFifth: true })`; each event keeps up to the first 3 candidates. Undetected chords are written as `-`.

## Python Entrypoints

Python scripts used by the `.command` launchers:

```text
pianotrans.py
pianotrans_mps.py
bpmfix.py
midi_stats_command.py
clean_midi_rules.py
hand_split_channels.py
generate_midi_review.py
generate_chord_review.py
```

Shared helpers:

```text
bpmfix_utils.py
first_beat_ui.py
first_beat_ui.html
midi_stats.py
detect_chords_tonal.js
```

## Dependencies

All `.command` launchers and BPM detection share one user-level Python environment (tkinter is required; with Homebrew use `python@3.11` plus `python-tk@3.11`):

```text
$HOME/.local/share/pianotrans-tools-env
```

If that environment is missing, the launchers fall back to `./.venv` and then system Python.

Create the environment (Intel Mac pins: the last PyTorch for macOS x86_64 is 2.2.2, which needs `numpy<2`; newer numba / llvmlite have no x86_64 wheels):

```bash
python3.11 -m venv ~/.local/share/pianotrans-tools-env
```

```bash
~/.local/share/pianotrans-tools-env/bin/python -m pip install "torch==2.2.2" "numpy<2" "numba==0.62.1" "llvmlite==0.45.1" "essentia==2.1b6.dev1389" mido librosa piano_transcription_inference
```

ffmpeg must be on PATH (librosa uses it to read m4a / mp3 and similar formats).

Tonal.js chord detection requires Node.js. The launcher searches `PATH` and common Node locations; `PIANOTRANS_NODE` can override it. On this setup Node lives in `$HOME/.local/share/node-<version>-darwin-x64` and is exposed on PATH through a `$HOME/.local/bin/node` link.

Vendored Tonal.js:

```text
vendor/tonal.min.js
```

Default model checkpoint path:

```text
$HOME/piano_transcription_inference_data/note_F1=0.9677_pedal_F1=0.9186.pth
```

Overridable environment variables:

```text
PIANOTRANS_BPM_ENV
PIANOTRANS_BPM_PYTHON
```

## Notes

- `pianotrans.py` tries CUDA, then CPU.
- `pianotrans_mps.py` tries MPS, then CUDA, then CPU; on Apple Silicon with PyTorch MPS available, it uses Apple GPU.
- BPM fixing assumes a fixed BPM and does not build a tempo map.
- Rule cleanup is conservative preprocessing. More complex decisions should be handled by later feature extraction or ML/classifier workflows.
