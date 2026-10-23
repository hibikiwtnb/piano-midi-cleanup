import argparse
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

import mido
from report_utils import update_report

from hand_split_channels import (
    CHANNELS, HARD_SPAN, MAX_FINGERS, collect_notes, group_notes,
)
from midi_stats import source_stats_path_for


# Fast figure: at least FAST_FIGURE_MIN_ONSETS onset groups, each within FAST_FIGURE_BEATS of the previous.
FAST_FIGURE_BEATS = 0.5
FAST_FIGURE_MIN_ONSETS = 3
# Hands that get the fast figure rule; on the left hand it also cuts bass notes held under a broken chord.
DEFAULT_FAST_FIGURE_HANDS = ('right', 'second_right')
# An onset whose notes over all hands have at least this many pitch classes is a harmony change.
HARMONY_MIN_PITCH_CLASSES = 3
# A new outer note (top of a right hand, bottom of a left hand) within this many semitones of a held outer note
# continues a scale line, so the held note ends there.
OUTER_STEP_SEMITONES = 2
LEFT_CHANNELS = {CHANNELS['left'], CHANNELS['second_left']}
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


def mark_harmonies(notes):
    """Give each note the pitch classes of its onset over all hands when they form a harmony, else None."""
    for group in group_notes(notes):
        pitch_classes = {note['note'] % 12 for note in group}
        for note in group:
            note['harmony'] = pitch_classes if len(pitch_classes) >= HARMONY_MIN_PITCH_CLASSES else None


def trim_chord_changes(groups, counts, outer_sign=1):
    """A hand releases held notes when it moves to a new chord, when the harmony changes under them,
    when its outer line steps on, or when it could not hold them with the new notes."""
    sounding = []
    for group in groups:
        onset = group[0]['start_ticks']
        sounding = [note for note in sounding if note['new_end'] > onset]
        outer = max(group, key=lambda note: outer_sign * note['note'])
        for note in sounding:
            if note['outer'] and abs(note['note'] - outer['note']) <= OUTER_STEP_SEMITONES:
                release(note, onset, 'outer_line', counts)
        sounding = [note for note in sounding if note['new_end'] > onset]
        harmony = group[0]['harmony']
        if harmony:
            for note in sounding:
                if note['note'] % 12 not in harmony:
                    release(note, onset, 'harmony_change', counts)
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
            note['outer'] = note is outer
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


def trim_overlaps_in_place(midi_path, fast_figure_hands=DEFAULT_FAST_FIGURE_HANDS):
    midi_file = mido.MidiFile(midi_path)
    notes = collect_notes(midi_file)
    for note in notes:
        note['new_end'] = note['end_ticks']
    mark_harmonies(notes)
    groups_by_channel = hand_groups(notes)

    peaks_before = {channel: max_sounding(groups) for channel, groups in groups_by_channel.items()}
    fast_figure_channels = {CHANNELS[hand] for hand in fast_figure_hands}
    counts = {}
    for channel, groups in sorted(groups_by_channel.items()):
        channel_counts = counts.setdefault(channel, {})
        if channel in fast_figure_channels:
            trim_fast_figures(groups, midi_file.ticks_per_beat, channel_counts)
        trim_chord_changes(groups, channel_counts, outer_sign=-1 if channel in LEFT_CHANNELS else 1)
    peaks_after = {channel: max_sounding(groups) for channel, groups in groups_by_channel.items()}

    write_note_ends(midi_file, notes)
    midi_file.save(midi_path)
    return {
        'midi_path': midi_path,
        'fast_figure_hands': fast_figure_hands,
        'notes': {channel: sum(len(group) for group in groups) for channel, groups in groups_by_channel.items()},
        'counts': counts,
        'peaks_before': peaks_before,
        'peaks_after': peaks_after,
    }


def format_trim_stats(stats):
    lines = [
        'MIDI overlap trim stats',
        'MIDI: {}'.format(stats['midi_path']),
        'Fast figure: {}; {} or more onset groups each within {} beat'.format(
            ', '.join(hand.replace('_', ' ') for hand in stats['fast_figure_hands']),
            FAST_FIGURE_MIN_ONSETS, FAST_FIGURE_BEATS),
    ]
    for channel in sorted(stats['notes']):
        counts = stats['counts'][channel]
        lines.append('- Channel {} / {}: {} notes, fast figure {}, harmony change {}, chord change {}, '
                     'outer line {}, out of reach {}, most notes sounding {} -> {}'.format(
                         channel + 1, HAND_NAMES.get(channel, 'unsplit'), stats['notes'][channel],
                         counts.get('fast_figure', 0), counts.get('harmony_change', 0),
                         counts.get('chord_change', 0), counts.get('outer_line', 0),
                         counts.get('out_of_reach', 0),
                         stats['peaks_before'][channel], stats['peaks_after'][channel]))
    return '\n'.join(lines)


def apply_overlap_trim(midi_path, report_path=None, fast_figure_hands=DEFAULT_FAST_FIGURE_HANDS):
    stats = trim_overlaps_in_place(midi_path, fast_figure_hands=fast_figure_hands)
    report_path = report_path or source_stats_path_for(midi_path)
    report = format_trim_stats(stats)
    update_report(report_path, report)
    print(report)
    print('Updating overlap trim report: {}'.format(report_path))
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
    parser.add_argument('--fast-figure-hands', nargs='+', choices=sorted(CHANNELS), default=DEFAULT_FAST_FIGURE_HANDS,
                        help='Hands that get the fast figure rule (default: right second_right).')
    args = parser.parse_args(argv)

    midi_path = args.input_midi or choose_midi()
    if not midi_path:
        print('No input selected.')
        return 1
    if not os.path.isfile(midi_path):
        print('MIDI file does not exist: {}'.format(midi_path))
        return 1

    stats = apply_overlap_trim(midi_path, report_path=args.report, fast_figure_hands=args.fast_figure_hands)
    if not args.input_midi:
        messagebox.showinfo('Trim Overlaps complete', format_trim_stats(stats))
    return 0


if __name__ == '__main__':
    sys.exit(main())
