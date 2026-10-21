import argparse
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

import mido

from hand_split_channels import (
    CHANNELS, HARD_SPAN, MAX_FINGERS, RIGHT_HAND_CHANNEL, collect_notes, group_notes,
)
from midi_stats import source_stats_path_for


# Right-hand fast figure: at least FAST_FIGURE_MIN_ONSETS onset groups, each within FAST_FIGURE_BEATS of the previous.
FAST_FIGURE_BEATS = 0.5
FAST_FIGURE_MIN_ONSETS = 3
HAND_NAMES = {channel: hand.replace('_', ' ') for hand, channel in CHANNELS.items()}


def hand_groups(notes):
    """Onset groups per channel, in time order."""
    by_channel = {}
    for note in notes:
        by_channel.setdefault(note['start_message'].channel, []).append(note)
    return {channel: group_notes(channel_notes) for channel, channel_notes in by_channel.items()}


def release(note, tick, rule, counts):
    if note['new_end'] > tick:
        note['new_end'] = tick
        counts[rule] = counts.get(rule, 0) + 1


def trim_fast_figures(groups, ticks_per_beat, counts):
    """Inside a fast figure each note ends at the next onset group of the same hand."""
    max_gap = FAST_FIGURE_BEATS * ticks_per_beat
    onsets = [group[0]['start_ticks'] for group in groups]
    in_figure = [False] * len(groups)
    start = 0
    for index in range(1, len(groups) + 1):
        if index == len(groups) or onsets[index] - onsets[index - 1] > max_gap:
            if index - start >= FAST_FIGURE_MIN_ONSETS:
                in_figure[start:index] = [True] * (index - start)
            start = index
    for index in range(len(groups) - 1):
        if in_figure[index] and in_figure[index + 1]:
            for note in groups[index]:
                release(note, onsets[index + 1], 'fast_figure', counts)


def trim_chord_changes(groups, counts):
    """A hand releases held notes when it moves to a new chord, or when it could not hold them with the new notes."""
    sounding = []
    for group in groups:
        onset = group[0]['start_ticks']
        sounding = [note for note in sounding if note['new_end'] > onset]
        if len(group) > 1:
            for note in sounding:
                if note['chord']:
                    release(note, onset, 'chord_change', counts)
            sounding = [note for note in sounding if note['new_end'] > onset]
        pitches = [note['note'] for note in sounding + group]
        if sounding and (len(set(pitches)) > MAX_FINGERS or max(pitches) - min(pitches) > HARD_SPAN):
            for note in sounding:
                release(note, onset, 'out_of_reach', counts)
            sounding = []
        for note in group:
            note['chord'] = len(group) > 1
        sounding += group


def max_sounding(groups):
    """Most notes of one hand sounding at once, from one sweep over starts and ends."""
    events = []
    for group in groups:
        for note in group:
            events.append((note['start_ticks'], 1))
            events.append((note['new_end'], -1))
    peak = sounding = 0
    for _, change in sorted(events):
        sounding += change
        peak = max(peak, sounding)
    return peak


def write_note_ends(midi_file, notes):
    """Move each trimmed note's end message to its new tick; note ends sort before note starts on the same tick."""
    moved = {(note['track_index'], note['end_index']): note['new_end']
             for note in notes if note['new_end'] != note['end_ticks']}
    for track_index, track in enumerate(midi_file.tracks):
        events = []
        absolute_tick = 0
        for order, message in enumerate(track):
            absolute_tick += message.time
            tick = moved.get((track_index, order), absolute_tick)
            is_end = message.type == 'note_off' or (message.type == 'note_on' and message.velocity == 0)
            events.append((tick, 0 if is_end else 1, order, message))
        events.sort(key=lambda item: item[:3])
        track.clear()
        previous_tick = 0
        for tick, _, _, message in events:
            track.append(message.copy(time=tick - previous_tick))
            previous_tick = tick


def trim_overlaps_in_place(midi_path):
    midi_file = mido.MidiFile(midi_path)
    notes = collect_notes(midi_file)
    for note in notes:
        note['new_end'] = note['end_ticks']
    groups_by_channel = hand_groups(notes)

    peaks_before = {channel: max_sounding(groups) for channel, groups in groups_by_channel.items()}
    counts = {}
    for channel, groups in sorted(groups_by_channel.items()):
        channel_counts = counts.setdefault(channel, {})
        if channel == RIGHT_HAND_CHANNEL:
            trim_fast_figures(groups, midi_file.ticks_per_beat, channel_counts)
        trim_chord_changes(groups, channel_counts)
    peaks_after = {channel: max_sounding(groups) for channel, groups in groups_by_channel.items()}

    write_note_ends(midi_file, notes)
    midi_file.save(midi_path)
    return {
        'midi_path': midi_path,
        'notes': {channel: sum(len(group) for group in groups) for channel, groups in groups_by_channel.items()},
        'counts': counts,
        'peaks_before': peaks_before,
        'peaks_after': peaks_after,
    }


def format_trim_stats(stats):
    lines = [
        'MIDI overlap trim stats',
        'MIDI: {}'.format(stats['midi_path']),
        'Fast figure: right hand, {} or more onset groups each within {} beat'.format(
            FAST_FIGURE_MIN_ONSETS, FAST_FIGURE_BEATS),
    ]
    for channel in sorted(stats['notes']):
        counts = stats['counts'][channel]
        lines.append('- Channel {} / {}: {} notes, fast figure {}, chord change {}, out of reach {}, '
                     'most notes sounding {} -> {}'.format(
                         channel + 1, HAND_NAMES.get(channel, 'unsplit'), stats['notes'][channel],
                         counts.get('fast_figure', 0), counts.get('chord_change', 0), counts.get('out_of_reach', 0),
                         stats['peaks_before'][channel], stats['peaks_after'][channel]))
    return '\n'.join(lines)


def apply_overlap_trim(midi_path, report_path=None):
    stats = trim_overlaps_in_place(midi_path)
    report_path = report_path or source_stats_path_for(midi_path)
    report = format_trim_stats(stats)
    with open(report_path, 'a', encoding='utf-8') as file:
        file.write('\n\n' + report + '\n')
    print(report)
    print('Appending overlap trim report: {}'.format(report_path))
    return stats


def choose_midi():
    root = tk.Tk()
    root.withdraw()
    return filedialog.askopenfilename(
        title='Choose hand-split MIDI to trim overlaps',
        filetypes=[
            ('MIDI files', '*.mid *.midi'),
            ('All files', '*.*'),
        ],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Shorten notes the sustain pedal left overlapping within each hand, in place.',
    )
    parser.add_argument('input_midi', nargs='?', help='Hand-split MIDI to modify in place. Without it a dialog opens.')
    parser.add_argument('--report', help='Report path (default: the source transcription *_stats.txt).')
    args = parser.parse_args(argv)

    midi_path = args.input_midi or choose_midi()
    if not midi_path:
        print('No input selected.')
        return 1
    if not os.path.isfile(midi_path):
        print('MIDI file does not exist: {}'.format(midi_path))
        return 1

    stats = apply_overlap_trim(midi_path, report_path=args.report)
    if not args.input_midi:
        messagebox.showinfo('Trim Overlaps complete', format_trim_stats(stats))
    return 0


if __name__ == '__main__':
    sys.exit(main())
