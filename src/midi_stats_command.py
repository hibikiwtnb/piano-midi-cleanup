import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

from midi_stats import write_midi_stats


def choose_midi():
    root = tk.Tk()
    root.withdraw()
    return filedialog.askopenfilename(
        title='Choose MIDI to analyze',
        filetypes=[
            ('MIDI files', '*.mid *.midi'),
            ('All files', '*.*'),
        ],
    )


def main():
    print('--------------------------------------------------------------------------------')
    print('MIDI quality stats')
    print('Choose a MIDI file to analyze.')
    print('--------------------------------------------------------------------------------')

    midi_path = choose_midi()
    if not midi_path:
        print('No input selected.')
        return 1

    if not os.path.isfile(midi_path):
        messagebox.showerror('MIDI Stats', 'MIDI file does not exist.')
        return 1

    try:
        output_path, _ = write_midi_stats(midi_path)
    except Exception as error:
        messagebox.showerror('MIDI Stats failed', str(error))
        raise

    messagebox.showinfo('MIDI Stats complete', 'Output:\n{}'.format(output_path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
