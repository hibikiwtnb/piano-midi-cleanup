import argparse
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

from first_beat import append_first_beat_stats, auto_align_first_beat
from first_beat_ui import run_first_beat_ui
from key_signature import apply_key_signatures


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


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description='Set BPM and the first beat of a MIDI in place. Without input_midi the tool asks with a dialog.',
    )
    parser.add_argument('input_midi', nargs='?', help='MIDI to modify in place.')
    parser.add_argument('--auto-first-beat', action='store_true', help='Align the first beat from the MIDI first.')
    parser.add_argument('--no-workbench', action='store_true', help='Do not open the Tk workbench.')
    parser.add_argument('--midi-port', help='Name of the MIDI output port to play on instead of the default.')
    parser.add_argument('--no-key-signature', action='store_true',
                        help='Do not rewrite key signatures after the first beat moves.')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.input_midi:
        if not os.path.isfile(args.input_midi):
            print('MIDI file does not exist: {}'.format(args.input_midi))
            return 1
        moved = False
        if args.auto_first_beat:
            moved = auto_align_first_beat(args.input_midi) is not None
        if not args.no_workbench:
            result = run_first_beat_ui(args.input_midi, midi_port=args.midi_port)
            append_first_beat_stats(args.input_midi, result)
            moved = moved or result is not None
        if moved and not args.no_key_signature:
            # Shifting moves the old key signature events off the bar lines.
            apply_key_signatures(args.input_midi)
        return 0

    print('--------------------------------------------------------------------------------')
    print('MIDI workbench')
    print('Choose a MIDI file. Applying changes in the workbench overwrites that file in place.')
    print('--------------------------------------------------------------------------------')

    midi_path = choose_midi()
    if not midi_path:
        print('No MIDI selected.')
        return 1
    if not os.path.isfile(midi_path):
        messagebox.showerror('MIDI Workbench', 'MIDI file does not exist.')
        return 1

    try:
        result = run_first_beat_ui(midi_path, midi_port=args.midi_port)
        report = append_first_beat_stats(midi_path, result)
        if result is not None:
            apply_key_signatures(midi_path)
    except Exception as error:
        messagebox.showerror('MIDI Workbench failed', str(error))
        raise

    if report:
        messagebox.showinfo('MIDI Workbench complete', report)
    return 0


if __name__ == '__main__':
    sys.exit(main())
