# Tk Canvas First Beat Workbench

The first-beat workbench runs in Tkinter and draws its piano roll with `Canvas`. It does not start an HTTP server or open a browser.

## Modules

- `src/first_beat.py` contains BPM rescaling, first-beat alignment, and stats reporting.
- `src/pianoroll_canvas.py` contains the Tk piano roll, MIDI output playback, seek, zoom, and beat scrolling.
- `src/first_beat_ui.py` keeps the command-line entry point used by BPMFix and MIDI Workbench.
- `src/postprocess_panel.py` opens the workbench for the selected MIDI.

## Workbench Controls

- BPM and first-beat offset in seconds
- `-1 beat` and `+1 beat`
- Play, Pause, and click-to-seek
- Ctrl/Alt + mouse wheel to zoom; Shift + wheel to scroll horizontally
- Apply overwrites the selected MIDI and returns the result to the caller; Cancel leaves the file unchanged

Playback uses `src/midi_playback.py` and the configured MIDI output. If no output is available, the piano roll still opens without sound.

The roll is intentionally limited to first-beat review. It assumes fixed BPM and 4/4 bar lines; it is not a general-purpose MIDI editor.
