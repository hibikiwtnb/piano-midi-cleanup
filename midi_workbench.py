import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

from first_beat_ui import append_first_beat_stats, run_first_beat_ui


def choose_midi():
    root = tk.Tk()
    root.withdraw()
    return filedialog.askopenfilename(
        title='Choose MIDI to open in the workbench',
        filetypes=[
            ('MIDI files', '*.mid *.midi'),
            ('All files', '*.*'),
        ],
    )


def main():
    print('--------------------------------------------------------------------------------')
    print('MIDI workbench')
    print('Choose a MIDI file. Saving in the browser overwrites that file in place.')
    print('--------------------------------------------------------------------------------')

    midi_path = choose_midi()
    if not midi_path:
        print('No MIDI selected.')
        return 1
    if not os.path.isfile(midi_path):
        messagebox.showerror('MIDI Workbench', 'MIDI file does not exist.')
        return 1

    try:
        result = run_first_beat_ui(midi_path)
        report = append_first_beat_stats(midi_path, result)
    except Exception as error:
        messagebox.showerror('MIDI Workbench failed', str(error))
        raise

    messagebox.showinfo('MIDI Workbench complete', report)
    return 0


if __name__ == '__main__':
    sys.exit(main())
