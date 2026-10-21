import math
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

import mido


DEFAULT_TEMPO = 500000
BEATS_PER_BAR = 4


def first_beat_path_for(midi_path):
    filename, ext = os.path.splitext(midi_path)
    return filename + '_firstbeat' + ext


def midi_tempo(midi_file):
    for track in midi_file.tracks:
        for message in track:
            if message.type == 'set_tempo':
                return message.tempo
    return DEFAULT_TEMPO


def earliest_note_tick(midi_file):
    earliest = None
    for track in midi_file.tracks:
        absolute_tick = 0
        for message in track:
            absolute_tick += message.time
            if message.type == 'note_on' and message.velocity > 0:
                if earliest is None or absolute_tick < earliest:
                    earliest = absolute_tick
    return earliest


def shift_midi_first_beat(midi_path, output_path, first_beat_seconds):
    """Move first_beat_seconds to a bar line; add whole bars if notes would start before 0."""
    midi_file = mido.MidiFile(midi_path)
    tempo = midi_tempo(midi_file)
    bar_ticks = midi_file.ticks_per_beat * BEATS_PER_BAR
    offset_ticks = int(round(mido.second2tick(first_beat_seconds, midi_file.ticks_per_beat, tempo)))

    shift_ticks = -offset_ticks
    padding_bars = 0
    earliest = earliest_note_tick(midi_file)
    if earliest is not None and earliest + shift_ticks < 0:
        padding_bars = math.ceil(-(earliest + shift_ticks) / bar_ticks)
        shift_ticks += padding_bars * bar_ticks

    for track in midi_file.tracks:
        absolute_tick = 0
        previous_tick = 0
        for message in track:
            absolute_tick += message.time
            shifted_tick = 0 if absolute_tick == 0 else max(0, absolute_tick + shift_ticks)
            message.time = shifted_tick - previous_tick
            previous_tick = shifted_tick

    midi_file.save(output_path)
    return {
        'bpm': mido.tempo2bpm(tempo),
        'offset_ticks': offset_ticks,
        'shift_ticks': shift_ticks,
        'padding_bars': padding_bars,
    }


def choose_midi():
    root = tk.Tk()
    root.withdraw()
    return filedialog.askopenfilename(
        title='Choose BPM-fixed MIDI to shift',
        filetypes=[
            ('MIDI files', '*.mid *.midi'),
            ('All files', '*.*'),
        ],
    )


def ask_first_beat_seconds():
    raw_value = input('First downbeat time in the source audio (seconds): ').strip()
    try:
        return float(raw_value)
    except ValueError:
        raise ValueError('First beat time must be a number.')


def main():
    print('--------------------------------------------------------------------------------')
    print('MIDI first beat shift')
    print('Choose a BPM-fixed MIDI, then enter where bar 1 beat 1 is in the source audio.')
    print('--------------------------------------------------------------------------------')

    midi_path = choose_midi()
    if not midi_path:
        print('No MIDI selected.')
        return 1
    if not os.path.isfile(midi_path):
        messagebox.showerror('First Beat Shift', 'MIDI file does not exist.')
        return 1

    try:
        midi_file = mido.MidiFile(midi_path)
        tempo = midi_tempo(midi_file)
        earliest = earliest_note_tick(midi_file)
        print('MIDI: {}'.format(midi_path))
        print('BPM: {:.2f}'.format(mido.tempo2bpm(tempo)))
        if earliest is not None:
            print('First note: {:.3f}s'.format(
                mido.tick2second(earliest, midi_file.ticks_per_beat, tempo)))

        first_beat_seconds = ask_first_beat_seconds()
        output_path = first_beat_path_for(midi_path)
        result = shift_midi_first_beat(midi_path, output_path, first_beat_seconds)
    except Exception as error:
        messagebox.showerror('First Beat Shift failed', str(error))
        raise

    summary = 'First beat: {:.3f}s\nPadding bars: {}\nOutput:\n{}'.format(
        first_beat_seconds,
        result['padding_bars'],
        output_path,
    )
    print(summary)
    messagebox.showinfo('First Beat Shift complete', summary)
    return 0


if __name__ == '__main__':
    sys.exit(main())
