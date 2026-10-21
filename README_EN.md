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

BPM fixing, then BPM and first beat adjustment in a browser workbench.

Usage:

1. If you already know the BPM, enter it in the terminal; otherwise press Return and choose the source audio so Essentia can detect a fixed BPM.
2. Choose the MIDI file to fix. The tool writes `*_bpmfix.mid`.
3. The first beat is moved to a bar line with the algorithm below and written to `*_bpmfix.mid` (no question; use `--no-auto-first-beat` on the CLI to skip it).
4. The MIDI workbench opens in the browser (the vsti-webui-wes7-pcm interface): piano roll, BPM / first beat / visual delay inputs, follow, left/right hand solo and mute, and the 88-key Pianometer. Playback sends the MIDI to S-MU2000 in realtime.
5. After setting BPM and the first beat, press "寫入 MIDI" to overwrite `*_bpmfix.mid` in place. "取消" leaves it unchanged.
6. Key signatures are written last with the `key_signature.command` method (`--no-key-signature` on the CLI skips it).

`first_beat_ui.py` serves the workbench from a temporary local HTTP server on `127.0.0.1` that stops after save or cancel, so no extra MIDI file is created. For playback, `mu2000_playback.py` opens a virtual MIDI port named `pianotrans-tools` and starts the headless S-MU2000 `live` engine on it in the background. Sound goes to the system default output; MU2000.app and an IAC bus are not needed.

Saving uses the original workbench fix: ticks are rescaled to the page BPM, then the first beat is moved to the start of bar 1; if pickup notes would start before 0, whole bars are added in front. Channels are unchanged. The result is appended to `*_stats.txt`.

The `pianotrans.command` pipeline only runs the BPM fix and does not open the workbench.

Automatic first beat alignment uses only the BPM-fixed MIDI (no audio) and assumes a fixed 4/4:

```text
1. Velocity-weighted onsets give the mean phase on the 8th-note period, i.e. the grid offset
2. Of the two 8th-note positions, the one with more weight on the quarter grid is the beat
3. Of the four beats, the one with the most left-hand (channel 3) or bass (<= F#3) onset weight is beat 1
4. The bar line nearest to 0 s is used, so running it again on an aligned MIDI barely moves it
```

The result (estimate, 8th-grid coherence, bass share on beats 1-4, shift ticks) is appended to `*_stats.txt`. Beat 1 may be wrong with heavily syncopated bass, intros without bass, or 3/4; fix it with +-1 beat in the workbench.

Agents and scripts can run it non-interactively (with `input_midi` no dialog opens, and the workbench is off by default):

```bash
python bpmfix.py song.mid --audio song.flac
```

```bash
python bpmfix.py song.mid --bpm 135 --no-auto-first-beat --no-key-signature --workbench
```

Automatic BPM detection estimates the actual BPM from the half-beat phase coherence of the Essentia beats (robust to missed beats and off-beat jumps), then checks the grid drift at the end of the song if the BPM is rounded to an integer:

```text
end_drift_ms = duration * |beat_phase_bpm / applied_bpm - 1| * 1000
```

If the drift is <= 50 ms, the integer BPM is applied; otherwise two decimals are kept. The report shows the raw Essentia BPM, the beat-phase BPM and coherence, whether rounding was applied, and `Estimated end drift`. Manual BPM input is used as entered.

The fix keeps the real playback speed and rewrites MIDI ticks plus tempo metadata:

```text
new_ticks = old_ticks * applied_bpm / source_bpm
```

`source_bpm` is the MIDI's own tempo (120 for transcription output; 120 when there is no tempo event), so running the fix again on an already fixed MIDI keeps the playback time.

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

Marks a provisional hand split directly on a cleaned MIDI file by changing note channels only. Two hands by default; for songs that need three hands add `--hands 3` for a second right hand.

It does not create separate instrument tracks and does not remove notes.

First a blob line is found for every beat: on a piano roll the bass and middle notes often form one blob and the fast broken chords another above it, with an empty pitch band in between. For each beat (with the onsets of one beat on each side) each candidate line is scored by its distance to the nearest notes above and below, and Viterbi keeps the line in the widest empty band while charging 0.5 per semitone it moves.

Assignment is a cost-minimizing beam search. Notes whose onsets are within 50 ms form one group; each group is sorted by pitch and cut into one contiguous slice per hand (two hands: left, right; three hands: left, second right, right). The lowest-cost path over the whole song is chosen. Cost terms:

```text
Hand span: free up to 12 semitones, heavily penalized beyond 14
Register: left hand above D4, right hand below G3, by distance
Movement speed: change of hand position divided by time since that hand's previous onset (an idle hand moves cheaply)
Fast runs: once a hand has played 3 or more consecutive single notes each < 0.25 s apart, each extra simultaneous note costs more (a dyad inside a fast right-hand single-note run tends to go to an idle left hand)
Blob: 4 per note when the top hand (right) gets a note below the blob line or another hand gets one above it; scaled down where the empty band is narrower than 4 semitones on each side
Second right hand: with three hands, 1 per note given to it, so it is used only when needed
```

The report shows the number of groups with a hand span over 12 semitones and the number of multi-note hands left inside fast runs. All weights are constants at the top of the file.

Channel mapping:

```text
MIDI channel 1 = right hand
MIDI channel 2 = second right hand (only with --hands 3)
MIDI channel 3 = left hand
MIDI channel 4 = reserved for a left-hand bass voice (not used yet)
```

For 0-based libraries such as `mido`, channels 0, 1, 2 and 3 are MIDI channels 1, 2, 3 and 4.

This step is intended to make the MIDI easier to inspect and edit in Logic Pro score view; with three hands Logic needs a staff style that includes channel 2.

Non-interactive:

```bash
python hand_split_channels.py song_bpmfix_cleaned.mid
python hand_split_channels.py song_bpmfix_cleaned.mid --hands 3
```

### `trim_overlaps.command`

Run after the hand split: shortens notes that the sustain pedal left overlapping within one hand, in place. Only note ends move; no notes are removed and no onsets change. Each hand (channel) is handled separately, and notes whose onsets are within 50 ms form one group.

```text
Fast figure (right hand only): 3 or more onset groups each within 0.5 beat of the previous; inside it every note ends by the next right-hand onset group
Chord change: when a hand plays a new chord or octave (2 or more notes), notes of its previous chord still sounding end there
Out of reach: when the sounding notes plus the new ones exceed 5 notes or span more than 14 semitones, the sounding notes end there
```

The trim counts per rule and each hand's most notes sounding at once, before and after, are appended to the source transcription `*_stats.txt`.

Non-interactive:

```bash
python trim_overlaps.py song_bpmfix_cleaned.mid
```

### `midi_workbench.command`

Opens any MIDI in the browser workbench to set BPM and the first beat, without running the BPM fix. The interface, realtime S-MU2000 playback and saving are the same as the `bpmfix.command` workbench.

Usage:

1. Choose a MIDI file (usually `*_bpmfix.mid` or `*_bpmfix_cleaned.mid`).
2. Adjust it in the workbench and press "寫入 MIDI" to overwrite the chosen file in place. "取消" leaves it unchanged.

The result is appended to the source transcription stats file (for example `song_bpmfix_cleaned.mid` goes to `song_stats.txt`) and shown in the terminal and the completion dialog. When the first beat moves, key signatures are written again (`--no-key-signature` skips it).

Non-interactive use (for agents): auto-align only, without the workbench, overwriting in place:

```bash
python midi_workbench.py song_bpmfix_cleaned.mid --auto-first-beat --no-workbench
```

### `key_signature.command`

Detects the key signature per 4-bar phrase and writes key signature events into the MIDI in place, so Logic score view changes key at modulations. The MIDI must be bar-aligned (after the BPM fix and first beat alignment).

Method:

```text
1. Phrases of 4 bars start at the first full bar after any pickup (pickup notes count into the first phrase)
2. For each phrase, count the notes that need accidentals under each of the 12 major key signatures (relative minors share them)
3. Viterbi picks the path with the fewest accidentals; each key change costs 4 extra notes, and keys only change at phrase boundaries
4. Only a phrase whose accidentals exceed 10% of its notes is checked bar by bar: it is split at the bar line, with the keys of it and its neighbouring phrases, that gives the fewest accidentals, so a key change can fall inside that phrase
5. F# and Db are used for the 6- and 7-accidental enharmonic pairs
```

Existing key signature events are replaced. Each segment's key and accidental count is appended to the source transcription `*_stats.txt`. 4/4 is assumed.

Non-interactive use:

```bash
python key_signature.py song_bpmfix_cleaned.mid
```

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

### `note_text_review.py`

Reads a bar-aligned MIDI and prints its notes as text, one onset group per line: bar, beat, then for each note from low to high its hand (`R` right, `S` second right, `L` left, `?` when not split), pitch name and length in beats. It does not modify the MIDI and is not part of the default flow; run it by hand when checking hand split, key signatures and similar. Assumes 4/4.

```bash
python note_text_review.py song_bpmfix_cleaned.mid --bars 95-97
python note_text_review.py song_bpmfix_cleaned.mid -o song_notes.txt
```

Example output:

```text
m96 beat 2.51  LA#3(0.99b) LC#4(1.22b) RG#4(1.00b)
```

### `pianoroll_snapshot.command`

Saves piano roll PNGs of a bar-aligned MIDI, four bars per image, without modifying the MIDI. Colors mark the hand: blue = right, amber = second right, green = left. Each image's pitch range follows the notes in its four bars.

```text
landscape: four bars in one row, 1920 wide
portrait: one bar per row, four rows, 1080 wide, for phones
```

By default both are written to `<MIDI name>_pianoroll/` next to the MIDI (ignored by git).

```bash
python pianoroll_snapshot.py song_bpmfix_cleaned.mid
python pianoroll_snapshot.py song_bpmfix_cleaned.mid --bars 17-32 --layout portrait -o out_dir
```

## Python Entrypoints

Python scripts used by the `.command` launchers:

```text
pianotrans.py
pianotrans_mps.py
bpmfix.py
midi_stats_command.py
clean_midi_rules.py
hand_split_channels.py
trim_overlaps.py
midi_workbench.py
key_signature.py
generate_midi_review.py
generate_chord_review.py
note_text_review.py
pianoroll_snapshot.py
```

Shared helpers:

```text
bpmfix_utils.py
first_beat_ui.py
mu2000_playback.py
web/result.js
web/result.css
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
~/.local/share/pianotrans-tools-env/bin/python -m pip install "torch==2.2.2" "numpy<2" "numba==0.62.1" "llvmlite==0.45.1" "essentia==2.1b6.dev1389" mido librosa piano_transcription_inference "python-rtmidi==1.5.8"
```

ffmpeg must be on PATH (librosa uses it to read m4a / mp3 and similar formats).

Tonal.js chord detection requires Node.js. The launcher searches `PATH` and common Node locations; `PIANOTRANS_NODE` can override it. On this setup Node lives in `$HOME/.local/share/node-<version>-darwin-x64` and is exposed on PATH through a `$HOME/.local/bin/node` link.

Realtime workbench playback needs the S-MU2000 emulator (`app/bin/live` and `roms`), by default at `$HOME/Music/S-MU2000-mac`; `PIANOTRANS_MU2000_DIR` overrides it. The virtual MIDI port needs `python-rtmidi` (included in the install command).

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
PIANOTRANS_MU2000_DIR
```

## Notes

- `pianotrans.py` tries CUDA, then CPU.
- `pianotrans_mps.py` tries MPS, then CUDA, then CPU; on Apple Silicon with PyTorch MPS available, it uses Apple GPU.
- BPM fixing assumes a fixed BPM and does not build a tempo map.
- Rule cleanup is conservative preprocessing. More complex decisions should be handled by later feature extraction or ML/classifier workflows.
