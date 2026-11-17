---
name: piano-midi-cleanup
description: Turn a song or piano recording (or an existing transcribed MIDI) into a cleaned, readable piano MIDI with Piano MIDI Cleanup. Use when the user names a song and wants a MIDI, score draft or cleaned transcription, e.g. "make a clean piano MIDI of this song". The agent runs the built-in separation, transcription and post-processing, reads the reports, makes evidence-based fixes and hands back the result. The user does not need to know music, models, time signatures or thresholds.
---

# Piano MIDI Cleanup: one song, one sentence

The user gives a song (file or title) and what they want. You decide and run everything, check the reports, and deliver. Never hand the user a question about BPM, meter, thresholds or models; decide from the reports, and say what you chose and what still needs a human check.

The tool has one built-in model chain (BS-RoFormer SW separation, ByteDance Piano Transcription). There is no model choice; do not add one. Authoritative references (read, do not copy): `README_EN.md`, `docs/spec/` (chapters 02 tempo and first beat, 04 rule cleaning, 06 hand split, 07 overlap trim, 08 runner), `docs/open-issues.md`, `docs/development-rules.md`. Parameter names and defaults: always read them live with `postprocess.py --list-parameters`, not from memory.

## 1. Request and input

- Find the input: an audio path the user gave, or search the user's music folder for the title once; if not found, ask for the file (only the user knows where it is).
- Classify it: a full song with vocals or accompaniment (separate first, the default), a solo piano recording (`--no-separate`, or a file stem ending `_piano`), or an existing transcribed MIDI (skip to step 3 with `postprocess.py`; pass `--audio` only if the original audio is available, else `--bpm`).
- Environment: use the shared Python environment `~/.local/share/piano-midi-cleanup-env` (Windows: `%USERPROFILE%\.local\share\piano-midi-cleanup-env\Scripts\python.exe`); install steps are in `README_EN.md`. Check that the separation and ByteDance models are already on disk (`model_store.MODELS[...].ready()`); `audio_to_midi.py` downloads missing ones silently, so if one is missing, tell the user it is being downloaded. Check free memory first; the first run is the expensive one (separation plus transcription take minutes for a full song, on GPU or CPU with `--cpu`).
- Output location: `../piano-midi-cleanup-data/<song> <source>+<model>-<version>/` (`--output-dir`). The data repository is private; commit what exists there before reprocessing (its README). Never write songs, outputs or local paths into this public repository.

## 2. Song facts and plan

Collect only what changes a decision: duration, whether it is solo piano or a mix, and anything the user said (hands, "easy version", meter, tempo). Do not research the song further.

Defaults to keep unless a fact says otherwise: 2 hands, automatic meter, automatic BPM and first beat, rule cleaning with high-phantom removal on, glissando repair on, overlap trim, no playable reduction. Choose `--hands 3` or `4` only when the user asks for duet or four-hand output. Use `--playable` only when the user asks for an easier one-player version.

## 3. First run

```
python src/audio_to_midi.py "<audio>" --output-dir "<folder>"
```

Reuse rule: existing `_piano.wav` and `_piano.mid` are reused; `--regenerate` redoes separation and transcription and must not be used for tuning. The run produces `_piano.mid` (raw), `_piano_bpmfix.mid` (tempo, meter, first beat, key), `_piano_bpmfix_cleaned.mid` (final), `_piano_stats.txt` (all reports, appended per step), `.changes.json` (every deleted or moved note) and `.gliss.json`.

## 4. Read the report and decide

Open `_piano_stats.txt` and check each item. The "Observed" column is what you read; act only when it matches.

| Observed in the report | Meaning | Action |
| --- | --- | --- |
| Raw "very short notes" about 10 percent, cleaning removed about 15 percent, mostly under the duration rule | Normal for ByteDance output (pedal-lengthened notes, spurious blips) | Keep the defaults. |
| Cleaning removed over about a quarter of notes, or "Max simultaneous keys" is far above what hands can play, or the removal is spread across all rules | Separation leaked other instruments; thresholds cannot fix it | Do not tune thresholds. Record it as a source-quality problem. Re-separation does not change the model, so only retry if the input file was wrong (for example a different mix). |
| Input is a MIDI from another model (Transkun style, note ends are finger releases) | Short notes are real and the 30-50 ms short-note count is not comparable | Add `--no-high-phantoms`. |
| `BPM source: detected`, small end drift (a few ms), high beat-phase coherence | Tempo is reliable | Keep. |
| Large end drift, low coherence, or applied BPM looks like half or double of the felt pulse | Tempo may be an octave off | Rerun postprocess with `--bpm` set to the half or double value only if the user gave a tempo or the note density supports it; otherwise keep and record the uncertainty. |
| `Time signature: undetermined` | Audio did not decide the meter; the MIDI's own 4/4 is used | Keep 4/4 and say so. Override with `--time-signature 3/4` or `6/8` only when the user states it or the evidence line clearly favors it. Overriding re-runs BPMFix, so it resets the first beat. |
| First beat: low "8th-grid onset coherence" | The first beat is a guess | Check "Bass onset share on beats 1-4": if beat 1 (and 3 in 4/4) dominate, keep. If another beat dominates, rerun with `--first-beat SECONDS` shifted by that many beats and record the change. |
| Gliss section lists runs with bar numbers | Glissandi were repaired | Keep. If a run is clearly a chord repeated (many same-pitch notes), tighten with `--set gliss.max_repeated_share=...`; otherwise leave. |
| Hand split: many groups wider than 12 semitones or "multi-note hand inside a fast run" | Some passages are hard to play with two hands | Report the count as a human-check item; do not loosen limits to hide it. |
| Overlap trim: many "kept with uncertain pedal" | Pedal events were unreliable | Report only. Note: `audio_to_midi.py` runs trim without pedal protection; keep that when you rerun `postprocess.py` (`--no-pedal-protection`) so reruns match. |
| Passages where one chord is re-struck at 16th-note intervals | Known unsolved issue (`docs/open-issues.md`) | Do not invent a rule; list the bars in the delivery as a human-check item. |

Threshold tuning is allowed only when one specific symptom is visible in the report or `.changes.json` and a parameter in `--list-parameters` directly governs it (for example `clean.min_velocity` for many deleted soft notes you can see are real, `trim.*` for over-shortened held notes). Make one change at a time, give the count you expect to move, and rerun only `postprocess.py` on the existing `_bpmfix.mid` (seconds, no model work). At most two tuning reruns per song; if the symptom is still there, record it and deliver. Never rerun separation or transcription to try a setting.

Rerun command (keeps the manual first beat and meter because no `--audio` or `--bpm` is given):

```
python src/postprocess.py "<folder>/<song>_piano_bpmfix_cleaned.mid" --no-pedal-protection --set KEY=VALUE
```

## 5. Deliver

Give the user, in plain language: the final `_bpmfix_cleaned.mid` path, the tempo and meter used (and that meter or first beat was an assumption when it was), what was removed or repaired in a sentence, and the short list of places to check by ear or eye (bar numbers from the reports: glissandi, wide hand spans, re-struck chords, uncertain first beat). Mention anything you could not verify (the tool gives no accuracy guarantee). Do not paste whole reports.

## 6. Per-song record and continuing

Write `run-record.md` in the song's output folder in the data repository (template: `skill/run-record-template.md`): input, tool version and commit, command lines run, settings changed with the evidence and the result, unresolved points. Commit it in the data repository with the application commit and settings in the message. On a later request for the same song, read the record first, reuse the existing outputs, and rerun only the step the new request changes.

## Worked example (anonymized, from a real 1.5.6 run)

A full-mix pop off-vocal track of about 2 min 10 s: separation 88 s, transcription then post-processing on one Apple Silicon laptop. Raw MIDI 2,580 notes, 11.16 percent under 50 ms. Reading the report: BPM 86.00 detected (end drift 6 ms, beat-phase coherence 0.94), so tempo was kept. Meter was `undetermined` (3-per-beat 0.25 against 2-per-beat 0.13, no accent period stood out), so 4/4 was kept and flagged as an assumption. First beat 1.038 s: 8th-grid coherence was only 0.12, but the bass shares on beats 1 to 4 were 0.46, 0.04, 0.45, 0.05, which fits a 4/4 bar starting there, so it was kept. Cleaning removed 378 notes (14.7 percent), 288 of them under the duration rule: the normal ByteDance profile, so no threshold was changed. One 13-note glissando in bar 67 was detected and repaired (one missing scale note filled). The two-hand split left 35 onset groups wider than 12 semitones, and trim shortened 960 notes with pedal protection off; both went into the delivery as human-check items. No tuning rerun was needed. The per-song record with the song name and paths lives in the private data repository.
