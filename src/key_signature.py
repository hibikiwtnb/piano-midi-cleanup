import argparse
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

import mido

from midi_stats import source_stats_path_for


BEATS_PER_BAR = 4
PHRASE_BARS = 4
# A key change must save more accidentals than this to be taken.
CHANGE_COST = 4
# A phrase whose accidentals exceed this share of its notes is checked bar by bar for a key change inside it.
CROWDED_RATIO = 0.10
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


def phrase_counts(midi_file, onsets):
    """Onset counts per pitch class for each phrase, starting at the first full bar after any pickup."""
    bar_ticks = midi_file.ticks_per_beat * BEATS_PER_BAR
    if not onsets:
        return 0, []

    first_tick = min(tick for tick, _ in onsets)
    start_tick = -(-first_tick // bar_ticks) * bar_ticks
    phrase_ticks = bar_ticks * PHRASE_BARS
    counts = []
    for tick, note in onsets:
        index = max(0, (tick - start_tick) // phrase_ticks)
        while len(counts) <= index:
            counts.append([0] * 12)
        counts[index][note % 12] += 1
    return start_tick, counts


def accidentals(pitch_counts, key_index):
    scale = scale_pitch_classes(KEY_SIGNATURES[key_index][1])
    return sum(count for pitch_class, count in enumerate(pitch_counts) if pitch_class not in scale)


def accidental_costs(counts):
    return [[accidentals(phrase, key) for key in range(len(KEY_SIGNATURES))] for phrase in counts]


def choose_signatures(costs):
    """Viterbi over phrases: fewest accidentals plus CHANGE_COST per key change."""
    keys = range(len(KEY_SIGNATURES))
    totals = list(costs[0])
    back = []
    for phrase_costs in costs[1:]:
        best_previous = min(keys, key=lambda key: totals[key])
        step_back = []
        new_totals = []
        for key in keys:
            stay = totals[key]
            move = totals[best_previous] + CHANGE_COST
            previous = key if stay <= move else best_previous
            step_back.append(previous)
            new_totals.append(min(stay, move) + phrase_costs[key])
        back.append(step_back)
        totals = new_totals
    path = [min(keys, key=lambda key: totals[key])]
    for step_back in reversed(back):
        path.append(step_back[path[-1]])
    path.reverse()
    return path


def detect_key_segments(midi_path):
    midi_file = mido.MidiFile(midi_path)
    onsets = note_onsets(midi_file)
    start_tick, counts = phrase_counts(midi_file, onsets)
    if not counts:
        return []
    costs = accidental_costs(counts)
    path = choose_signatures(costs)
    bar_ticks = midi_file.ticks_per_beat * BEATS_PER_BAR
    phrase_ticks = bar_ticks * PHRASE_BARS
    pieces = []
    for index, key in enumerate(path):
        tick = 0 if index == 0 else start_tick + index * phrase_ticks
        notes = sum(counts[index])
        if index == 0 or costs[index][key] <= notes * CROWDED_RATIO:
            pieces.append((tick, key, notes, costs[index][key], False))
        else:
            pieces.extend(split_crowded_phrase(onsets, tick, bar_ticks, path, index))

    segments = []
    for tick, key, notes, cost, inside in pieces:
        if segments and segments[-1]['key_index'] == key:
            segments[-1]['notes'] += notes
            segments[-1]['accidentals'] += cost
            continue
        segments.append({
            'key_index': key,
            'key': KEY_SIGNATURES[key][0],
            'tick': tick,
            'bar': tick // bar_ticks + 1,
            'notes': notes,
            'accidentals': cost,
            'inside_phrase': inside,
        })
    return segments


def split_crowded_phrase(onsets, tick, bar_ticks, path, index):
    """Split a crowded phrase at the bar line that gives the fewest accidentals, using the neighbouring keys."""
    bars = [[0] * 12 for _ in range(PHRASE_BARS)]
    for onset_tick, note in onsets:
        bar = (onset_tick - tick) // bar_ticks
        if 0 <= bar < PHRASE_BARS:
            bars[bar][note % 12] += 1
    first_keys = {path[index - 1], path[index]}
    second_keys = {path[index]} | ({path[index + 1]} if index + 1 < len(path) else set())
    best = None
    for split in range(1, PHRASE_BARS):
        for first in first_keys:
            for second in second_keys:
                cost = (sum(accidentals(bar, first) for bar in bars[:split])
                        + sum(accidentals(bar, second) for bar in bars[split:]))
                if best is None or cost < best[0]:
                    best = (cost, split, first, second)
    cost, split, first, second = best
    whole = sum(accidentals(bar, path[index]) for bar in bars)
    if cost >= whole:
        return [(tick, path[index], sum(map(sum, bars)), whole, False)]
    return [
        (tick, first, sum(map(sum, bars[:split])), sum(accidentals(bar, first) for bar in bars[:split]), False),
        (tick + split * bar_ticks, second, sum(map(sum, bars[split:])),
         sum(accidentals(bar, second) for bar in bars[split:]), first != second),
    ]


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
        'Phrase: {} bars from the first full bar, change cost {} accidental notes'.format(PHRASE_BARS, CHANGE_COST),
    ]
    for segment in segments:
        lines.append('- Bar {}: {} (accidental notes {} of {}){}'.format(
            segment['bar'], segment['key'], segment['accidentals'], segment['notes'],
            ', inside a crowded phrase' if segment['inside_phrase'] else ''))
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
    with open(report_path, 'a', encoding='utf-8') as file:
        file.write('\n\n' + report + '\n')
    print(report)
    print('Appending key signature report: {}'.format(report_path))
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
        description='Detect key signatures per 4-bar phrase by fewest accidentals and write them into the MIDI in place.',
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
