import argparse
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

import mido

from midi_stats import source_stats_path_for
from report_utils import update_report


BEATS_PER_BAR = 4
# A key change must be backed by more than this many bars that vote against the current key.
CHANGE_COST = 4
MAJOR_STEPS = (0, 2, 4, 5, 7, 9, 11)
# Major key signatures; F# and Db are used for the 6- and 7-accidental enharmonic pairs.
KEY_SIGNATURES = (
    ('C', 0), ('G', 7), ('D', 2), ('A', 9), ('E', 4), ('B', 11),
    ('F#', 6), ('Db', 1), ('Ab', 8), ('Eb', 3), ('Bb', 10), ('F', 5),
)


def scale_pitch_classes(tonic):
    return {(tonic + step) % 12 for step in MAJOR_STEPS}


def note_onsets(midi_file):
    onsets = []
    for track in midi_file.tracks:
        absolute_tick = 0
        for message in track:
            absolute_tick += message.time
            if message.type == 'note_on' and message.velocity > 0:
                onsets.append((absolute_tick, message.note))
    return onsets


def bar_counts(midi_file, onsets):
    """Onset counts per pitch class for every bar from the first bar with notes to the last."""
    bar_ticks = midi_file.ticks_per_beat * BEATS_PER_BAR
    if not onsets:
        return 0, []
    first_bar = min(tick for tick, _ in onsets) // bar_ticks
    counts = []
    for tick, note in onsets:
        index = tick // bar_ticks - first_bar
        while len(counts) <= index:
            counts.append([0] * 12)
        counts[index][note % 12] += 1
    return first_bar, counts


def accidentals(pitch_counts, key_index):
    scale = scale_pitch_classes(KEY_SIGNATURES[key_index][1])
    return sum(count for pitch_class, count in enumerate(pitch_counts) if pitch_class not in scale)


def bar_votes(counts):
    """Per bar and key: 0 if the key is among the bar's fewest-accidental keys, else 1.

    A borrowed chord only turns a few bars against the home key, so the vote follows the
    majority of bars instead of the total accidental count. Empty bars vote for every key.
    """
    votes = []
    for bar in counts:
        costs = [accidentals(bar, key) for key in range(len(KEY_SIGNATURES))]
        fewest = min(costs)
        votes.append([0 if cost == fewest else 1 for cost in costs])
    return votes


def choose_signatures(costs):
    """Viterbi over bars: fewest opposing bars plus CHANGE_COST per key change."""
    keys = range(len(KEY_SIGNATURES))
    totals = list(costs[0])
    back = []
    for bar_costs in costs[1:]:
        best_previous = min(keys, key=lambda key: totals[key])
        step_back = []
        new_totals = []
        for key in keys:
            stay = totals[key]
            move = totals[best_previous] + CHANGE_COST
            previous = key if stay <= move else best_previous
            step_back.append(previous)
            new_totals.append(min(stay, move) + bar_costs[key])
        back.append(step_back)
        totals = new_totals
    path = [min(keys, key=lambda key: totals[key])]
    for step_back in reversed(back):
        path.append(step_back[path[-1]])
    path.reverse()
    return path


def detect_key_segments(midi_path):
    midi_file = mido.MidiFile(midi_path)
    first_bar, counts = bar_counts(midi_file, note_onsets(midi_file))
    if not counts:
        return []
    path = choose_signatures(bar_votes(counts))
    bar_ticks = midi_file.ticks_per_beat * BEATS_PER_BAR
    segments = []
    for index, key in enumerate(path):
        notes = sum(counts[index])
        cost = accidentals(counts[index], key)
        if segments and segments[-1]['key_index'] == key:
            segments[-1]['notes'] += notes
            segments[-1]['accidentals'] += cost
            continue
        bar = first_bar + index
        segments.append({
            'key_index': key,
            'key': KEY_SIGNATURES[key][0],
            'tick': 0 if not segments else bar * bar_ticks,
            'bar': 1 if not segments else bar + 1,
            'notes': notes,
            'accidentals': cost,
        })
    return segments


def write_key_signatures(midi_path, segments):
    """Replace all key signature events with one per segment in the first track."""
    midi_file = mido.MidiFile(midi_path)
    for track_index, track in enumerate(midi_file.tracks):
        events = []
        absolute_tick = 0
        for order, message in enumerate(track):
            absolute_tick += message.time
            if message.type == 'key_signature':
                continue
            events.append((absolute_tick, 1, order, message))
        if track_index == 0:
            for order, segment in enumerate(segments):
                events.append((segment['tick'], 0, order, mido.MetaMessage('key_signature', key=segment['key'])))
        events.sort(key=lambda item: item[:3])
        track.clear()
        previous_tick = 0
        for tick, _, _, message in events:
            track.append(message.copy(time=tick - previous_tick))
            previous_tick = tick
    midi_file.save(midi_path)


def format_key_stats(midi_path, segments):
    lines = [
        'MIDI key signature stats',
        'MIDI: {}'.format(midi_path),
        'Vote: one bar each, change cost {} bars'.format(CHANGE_COST),
    ]
    for segment in segments:
        lines.append('- Bar {}: {} (accidental notes {} of {})'.format(
            segment['bar'], segment['key'], segment['accidentals'], segment['notes']))
    total_notes = sum(segment['notes'] for segment in segments)
    total_accidentals = sum(segment['accidentals'] for segment in segments)
    lines.append('Accidental notes: {} of {} ({:.2%})'.format(
        total_accidentals, total_notes, total_accidentals / total_notes if total_notes else 0.0))
    return '\n'.join(lines)


def apply_key_signatures(midi_path, report_path=None):
    segments = detect_key_segments(midi_path)
    write_key_signatures(midi_path, segments)
    report_path = report_path or source_stats_path_for(midi_path)
    report = format_key_stats(midi_path, segments)
    update_report(report_path, report)
    print(report)
    print('Updating key signature report: {}'.format(report_path))
    return segments


def choose_midi():
    root = tk.Tk()
    root.withdraw()
    return filedialog.askopenfilename(
        title='Choose bar-aligned MIDI for key signatures',
        filetypes=[
            ('MIDI files', '*.mid *.midi'),
            ('All files', '*.*'),
        ],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Detect key signatures by a per-bar fewest-accidentals vote and write them into the MIDI in place.',
    )
    parser.add_argument('input_midi', nargs='?', help='Bar-aligned MIDI to modify in place. Without it a dialog opens.')
    parser.add_argument('--report', help='Report path (default: the source transcription *_stats.txt).')
    args = parser.parse_args(argv)

    midi_path = args.input_midi or choose_midi()
    if not midi_path:
        print('No input selected.')
        return 1
    if not os.path.isfile(midi_path):
        print('MIDI file does not exist: {}'.format(midi_path))
        return 1

    segments = apply_key_signatures(midi_path, report_path=args.report)
    if not args.input_midi:
        messagebox.showinfo('Key Signature complete', '\n'.join(
            'Bar {}: {}'.format(segment['bar'], segment['key']) for segment in segments))
    return 0


if __name__ == '__main__':
    sys.exit(main())
