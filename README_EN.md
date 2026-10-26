# Piano MIDI Cleanup

[Chinese README](README.md)

Version **1.0.2**. Piano MIDI Cleanup turns AI piano transcription MIDI into a version that is easier to read, edit and practice. Use the desktop interface to align tempo and bars, clean suspect notes, assign hands and preview the result with the built-in piano.

Scripts are provided to create a Windows shortcut or macOS app. Set up the Python environment below, run the script once, then double-click the shortcut or app to launch.

## What it does

- Fixes the song BPM and aligns the first beat to a bar line.
- Writes key signatures so score view changes key at modulations.
- Removes obvious transcription noise with conservative rules.
- Splits notes into 2 to 4 hands, marked by MIDI channel; in three-hand mode the second player joins in whole-bar segments (default: at least 4 bars).
- Shortens overlaps inside one hand that were caused by sustain-pedal tails.
- Provides a piano roll, note text review, piano roll PNGs and other inspection tools.
- Includes a piano-audio transcription entrypoint built on ByteDance's `piano_transcription_inference`.

The tool does **not** separate sources. If the input is a full song mix, separate the piano part yourself first; otherwise other instruments will be transcribed as piano.

## Getting started

Prepare the Python environment once, then launch the tool from a desktop shortcut:

1. Open the project page: <https://github.com/hibikiwtnb/piano-midi-cleanup>.
2. Choose **Code → Download ZIP** and extract it to a permanent folder. You should see `README.md`, `src/`, `windows/` and `macos/`.
3. Create a Python 3.11 environment for your platform (below).
4. Run the launcher generator for your platform once to create the GUI entry.

If you move the folder or rebuild the Python environment, run the generator again. The `.cmd` / `.command` files switch to the project folder themselves, so you do not need to `cd` first.

## Requirements

- **Python 3.11** with **tkinter**. On Windows choose 64-bit Python 3.11 from the [official Python downloads](https://www.python.org/downloads/windows/). On macOS use python.org Python, or Homebrew `python@3.11` plus `python-tk@3.11`.
- Python packages: `torch`, `numpy`, `mido`, `librosa`, `piano_transcription_inference`, `python-rtmidi`, `pillow`, `sounddevice`. The macOS installs also include `numba`, `llvmlite` and `essentia`.
- **[ffmpeg](https://ffmpeg.org/download.html)** on `PATH` for m4a, mp3 and similar compressed audio formats.
- **[Node.js](https://nodejs.org/en/download)**: required on Windows for automatic BPM detection and chord display; on macOS only the chord track needs it.
- Transcription can run on CPU. An NVIDIA GPU may use a CUDA PyTorch build instead. Linux is outside the GUI support described here.

## Windows x64 setup

After installing Python, open **Command Prompt (cmd.exe)** from the Start menu and paste these commands. This block uses cmd syntax; keep the quotes around paths:

```bat
py -3.11 -m venv "%USERPROFILE%\.local\share\piano-midi-cleanup-env"
"%USERPROFILE%\.local\share\piano-midi-cleanup-env\Scripts\python.exe" -m pip install --upgrade pip
"%USERPROFILE%\.local\share\piano-midi-cleanup-env\Scripts\python.exe" -m pip install torch --index-url https://download.pytorch.org/whl/cpu
"%USERPROFILE%\.local\share\piano-midi-cleanup-env\Scripts\python.exe" -m pip install mido librosa piano_transcription_inference python-rtmidi pillow "sounddevice>=0.5"
```

For an NVIDIA GPU, replace the third line with PyTorch's current CUDA wheel index, for example `--index-url https://download.pytorch.org/whl/cu126`.

Essentia has no Windows wheel, so it is not installed. Automatic BPM detection uses the bundled essentia.js instead and therefore needs Node.js. Install ffmpeg and add its `bin` folder to `PATH` as well.

## macOS setup

The macOS `.command` launchers look for `~/.local/share/piano-midi-cleanup-env` by default. Run these commands in the project folder.

Install Python 3.11, Tk, ffmpeg and Node.js. With Homebrew:

```bash
brew install python@3.11 python-tk@3.11 ffmpeg node
```

If you use python.org Python 3.11, Tk is included in the installer; still install ffmpeg and Node.js separately. If `python3.11` is not on `PATH`, replace `python3.11` in the commands below with `$(brew --prefix python@3.11)/bin/python3.11`.

<details>
<summary><strong>Apple Silicon (arm64) setup</strong></summary>

Apple Silicon does not need the old Intel package pins. Install current versions:

```bash
python3.11 -m venv ~/.local/share/piano-midi-cleanup-env
~/.local/share/piano-midi-cleanup-env/bin/python -m pip install --upgrade pip
~/.local/share/piano-midi-cleanup-env/bin/python -m pip install torch numpy numba llvmlite essentia mido librosa piano_transcription_inference python-rtmidi pillow "sounddevice>=0.5"
```

Automatic BPM detection on Apple Silicon uses Python Essentia. Node.js is only needed for the chord track.

</details>

<details>
<summary><strong>Intel Mac (x86_64) setup</strong></summary>

Intel Macs use the last PyTorch release for macOS x86_64, so the versions must stay pinned. `numpy` must stay below 2, and `numba`, `llvmlite` and `essentia` use the versions below:

```bash
python3.11 -m venv ~/.local/share/piano-midi-cleanup-env
~/.local/share/piano-midi-cleanup-env/bin/python -m pip install --upgrade pip
~/.local/share/piano-midi-cleanup-env/bin/python -m pip install "torch==2.2.2" "numpy<2" "numba==0.62.1" "llvmlite==0.45.1" "essentia==2.1b6.dev1389" mido librosa piano_transcription_inference "python-rtmidi==1.5.8" pillow "sounddevice>=0.5"
```

Do not upgrade only `torch` or `numpy`; the Intel environment may stop working.

</details>

## Launching the GUI

### Windows

Double-click `windows\create_shortcut.cmd`. It creates `Piano MIDI Cleanup.lnk` in the project root and opens the panel with `pythonw.exe`, without a console window. You can copy the shortcut to the Desktop or Start menu.

If the shortcut fails to start, open cmd in the project folder and run this command to see the full error:

```bat
"%USERPROFILE%\.local\share\piano-midi-cleanup-env\Scripts\python.exe" src\postprocess_panel.py
```

### macOS

Double-click `macos/create_app.command`. It creates `Piano MIDI Cleanup.app` in the project root; drag it to the Dock or `/Applications`. If the app fails to start, it shows a dialog and writes details to `~/Library/Logs/Piano MIDI Cleanup/panel.log`.

Without the app, double-click `macos/postprocess_panel.command`, or run:

```bash
~/.local/share/piano-midi-cleanup-env/bin/python src/postprocess_panel.py
```

## Everyday workflow

### You already have MIDI

1. Open the GUI and choose any MIDI file you want to process. Keep its existing filename.
2. Choose the tempo source:
   - **Set tempo (BPM)**: enter the song's known tempo. **Choose the file first, then enter the BPM**; changing the file clears the BPM field.
   - **Detect tempo from audio**: choose the source audio to estimate its tempo.
   - **Keep corrected tempo**: use an existing tempo-corrected file (`*_bpmfix.mid`), including manual first-beat edits. If this is your first run and that file does not exist, choose one of the first two options.
3. Start with the default steps and press **Start processing**. To simplify difficult note groups, enable **Reduce notes for one player, two hands**. For three or four hands, change **Detailed settings → Assign hands → Number of hands**.
4. On success, the roll automatically shows the latest processed result, whose name ends in `_bpmfix_cleaned.mid`. Import that MIDI into notation software to prepare a score.
5. Check notes, chords and pedal in the roll; see the next section to adjust the first beat.

### Start from piano audio

1. Make sure the audio is solo piano or an already-separated piano part.
2. Windows: double-click `windows\transcribe.cmd`. macOS: double-click `macos/transcribe.command`. Both open a file dialog and accept one or more audio files. `src/transcribe.py` currently also uses a file dialog; there is **no** headless audio command line.
3. The first transcription downloads a model of about **165 MB** with Python; `wget` is not needed. A failed download keeps the old file, and you can retry.
4. Transcription writes the original MIDI, report, tempo-corrected MIDI and processed result next to the audio, using its filename as the base (see the table below). This step only fixes the BPM, runs rule cleanup and does a provisional two-hand split; it does **not** align the first beat, write key signatures or trim overlaps.
5. Return to the GUI, select the processed result, and check and save the first beat in the roll; this also writes key signatures. To adjust cleanup, hand count or trimming afterward, select **Keep corrected tempo** and process again.

If the original MIDI with the audio's base name already exists, the transcription entrypoint only fills in missing stats and post-processing files instead of transcribing again.

### First beat, preview and reruns

- The roll initially shows the file you selected. After successful processing, it automatically shows the latest result.
- **When the processed result is shown**, **Save first beat** updates both the matching tempo-corrected file and the shown result, rewrites key signatures on both, shifts the change overlay, and then previews the tempo-corrected file.
- **When the original MIDI is shown**, **Save first beat** creates or replaces its tempo-corrected file and **does not edit the original**, then previews the tempo-corrected file.
- If the shown name ends in `_bpmfix` or `_bpmfix_cleaned`, a matching tempo-corrected file must exist before the first beat can be edited.
- On later runs, choose **Keep corrected tempo** to retain manual alignment. Recomputing from the original MIDI with automatic first-beat alignment can replace it.
- Every run rebuilds and overwrites the processed result from the tempo-corrected file. Keep the source audio, original MIDI and tempo-corrected file, rather than only the result.

## Output files

Here, `*` stands for your original base filename. Actual outputs keep that name; you do not need to rename files.

| File | Contents | Notes |
|:---|:---|:---|
| `*.mid` | Original transcribed MIDI | Avoid editing it directly |
| `*_stats.txt` | Statistics and run record | Each processing block is updated |
| `*_bpmfix.mid` | Tempo-corrected MIDI; first beat and key signatures can be written here | Rebuild source; manual first beat is saved here |
| `*_bpmfix_cleaned.mid` | Processed result after rule cleanup, hand split and optional trim | Overwritten on every run |
| `*_bpmfix_cleaned.changes.json` | Data for the roll's change overlay | Tied to the same result |
| `*.chords.json` | Chord-analysis cache for a viewed MIDI | Matched by MIDI content and analysis version |
| `*_bpmfix_cleaned_pianoroll/` | Piano roll PNGs | Created only when snapshots are enabled |

Running `clean_midi_rules` standalone also writes a matching `*_clean_report.txt` next to the input.

## Common problems and limitations

- **Python not found**: the launcher tries the shared environment, the project `.venv`, `py -3.11` and Python on `PATH`. Confirm Python 3.11 and tkinter are installed, then rebuild the environment.
- **tkinter fails to load**: use the python.org build on Windows; on macOS Homebrew, install `python-tk@3.11`.
- **m4a / mp3 will not load**: install ffmpeg and make sure the `ffmpeg` command is on `PATH`.
- **The chord track is empty**: Node.js is required on both macOS and Windows. Without Node.js only chord display is affected; on Windows automatic BPM also becomes unavailable.
- **First transcription is slow or the download fails**: the model is about 165 MB and is downloaded with Python, not `wget`. Retry on an unstable connection.
- **BPM is detected at half or double speed**: enter the correct value with **Set tempo (BPM)**. Changing the file clears the field, so choose the file first.
- **The first beat is wrong**: adjust it by ±1 beat in the roll and save. Syncopated bass, an intro without bass, or 3/4 can confuse the estimate.
- **The result is not shown after processing**: check that processing finished, then open the piano roll. If processing failed, check the report for the error message.
- **My manual first beat disappeared after a rerun**: choose **Keep corrected tempo** next time instead of recomputing automatic alignment from the original.
- **Playback is not export**: roll playback is realtime monitoring only. The tool does not write WAV, MP3 or other audio files.
- **Built-in sound**: Coopiano is a physical-modeling instrument, so no piano samples are downloaded. Playback uses the OS default audio device; a Windows Remote Desktop session must have a usable playback device.
- **Playback environments**: native synth libraries ship for Windows x64 and macOS; systematic playback validation across platforms and Remote Desktop sessions remains outstanding.
- **4/4 and one tempo only**: tempo changes, free tempo and 3/4 will make bar lines drift.
- **Not a finished score**: the output is still MIDI, not engraved notation or MusicXML. Wrong and missing notes still need human review.
- **Three and four hands**: splitting supports 2 to 4 hands. Playable reduction handles every note with two hands; with three hands it must run after the split, and only the first player's left and right hands are reduced while the second player's second_right is left alone. Four-hand splitting is supported, but playable reduction is not.
- **Different trim defaults**: the GUI's pedal-aware trim protection is **off** by default; the command line defaults to **on** and can disable it with `--no-pedal-protection`.

## Advanced docs and command line

The [design specification](docs/spec/README.md) documents every rule, threshold and known limit (Traditional Chinese). Current issues are in [open issues](docs/open-issues.md), and cleanup details are in [cleaning rules](docs/cleaning-rules.md).

In these examples, `input.mid` and `input.flac` stand for your own files; replace them with actual paths, not the table's `*` patterns. Run the commands in the project folder. On macOS, first activate the shared environment where the packages were installed:

```bash
. "$HOME/.local/share/piano-midi-cleanup-env/bin/activate"
python src/postprocess.py "input.mid" --audio "input.flac"
python src/postprocess.py "input.mid" --bpm 135 --hands 3
python src/postprocess.py "input_bpmfix_cleaned.mid" --playable --snapshot portrait
python src/postprocess.py --list-parameters
```

On Windows, skip the activation command above and call the shared environment's `python.exe` directly in cmd, quoting its path:

```bat
"%USERPROFILE%\.local\share\piano-midi-cleanup-env\Scripts\python.exe" src\postprocess.py "input.mid" --bpm 135
```

`windows\*.cmd` and `macos/*.command` are double-clickable standalone tools. `bpmfix.py` and `midi_workbench.py` are command-line only and have no double-click entry. See the [runner and tools specification](docs/spec/08-runner-and-tools.md) for the full tool and option map.

## License and attribution

Copyright (c) 2026 hibikiwtnb and contributors.

Original project code is licensed under the **GNU Affero General Public License v3.0 only** (`AGPL-3.0-only`); see [LICENSE](LICENSE). Commercial use is permitted. Distribution requires providing the Corresponding Source under the license terms, and modified versions that interact with users over a network must offer access to their Corresponding Source as required by section 13. The software comes without warranty.

Transcription uses ByteDance's [piano_transcription_inference](https://github.com/qiuqiangkong/piano_transcription_inference) (MIT) and its model weights ([Zenodo 4034264](https://zenodo.org/record/4034264), CC BY 4.0). Bundled third-party components, including the Coopiano piano sound, Tonal.js chord naming, note-seq chord inference and Essentia.js for Windows automatic BPM, retain their own licenses. See [sound_sources/README.md](sound_sources/README.md), `vendor/TONAL_LICENSE.txt`, `vendor/NOTE_SEQ_LICENSE.txt` and `vendor/ESSENTIA_LICENSE.txt`. This is a personal project, not affiliated with ByteDance.
