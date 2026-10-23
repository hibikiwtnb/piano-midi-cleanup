"""Compatibility entry point for the Tk first-beat workbench."""

import tkinter as tk

from first_beat import append_first_beat_stats, auto_align_first_beat, build_bpm_fixed_midi
from pianoroll_canvas import PianoRollWorkbench


def run_first_beat_ui(midi_path, open_browser=True, port=0, midi_port=None, root=None):
    del open_browser, port
    owns_root = root is None
    root = root or tk.Tk()
    workbench = PianoRollWorkbench(root, str(midi_path), midi_port=midi_port)
    if owns_root:
        root.mainloop()
    return getattr(workbench, 'result', None)
