import argparse
import itertools
import os
import sys
import tkinter as tk
from collections import defaultdict
from tkinter import filedialog, messagebox

import mido

from clean_midi_rules import rebuild_track_without_messages
from hand_split_channels import CHANNELS, collect_notes, group_notes, hand_fits
from midi_stats import source_stats_path_for
from report_utils import update_report


# One player: the notes of an onset group must split into a low part for the left hand and a high part for
# the right hand, each with at most this many notes and this span in semitones.
DEFAULT_MAX_NOTES_PER_HAND = 4
DEFAULT_MAX_SPAN = 12
# In a three-hand split the first player's two hands are the lower two slices; the other player's hand is left alone.
THREE_HAND_PLAYER = ('left', 'second_right')
# Groups that still cannot be played after removing this many notes are left as they are.
MAX_REMOVALS_PER_GROUP = 3
# Removal cost: a note doubled in another octave is cheapest, the top (melody) and bottom (bass) notes dearest.
DOUBLED_COST = 1
SINGLE_COST = 3
OUTER_COST = 5
BEATS_PER_BAR = 4
NOTE_NAMES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')


def playable_cuts(pitches, max_notes, max_span):
    """Every cut index into a low and a high part that fits both hands."""
    pitches = sorted(pitches)
    return [cut for cut in range(len(pitches) + 1)
            if hand_fits(pitches[:cut], max_notes, max_span) and hand_fits(pitches[cut:], max_notes, max_span)]


def playable(pitches, max_notes, max_span):
    """Whether some cut into a low and a high part fits both hands."""
    return bool(playable_cuts(pitches, max_notes, max_span))


def reassign_hands(notes, channels, max_notes, max_span):
    """When the split's low and high hand do not fit, move notes to the fitting cut that changes the fewest.
    Returns how many notes changed hand."""
    notes = sorted(notes, key=lambda note: note['note'])
    low, high = channels
    current = [note['start_message'].channel for note in notes]
    if all(hand_fits([note['note'] for note, channel in zip(notes, current) if channel == hand], max_notes, max_span)
           for hand in channels):
        return 0
    cuts = playable_cuts([note['note'] for note in notes], max_notes, max_span)
    if not cuts:
        return 0
    cut = min(cuts, key=lambda cut: sum(channel != (low if index < cut else high)
                                        for index, channel in enumerate(current)))
    moved = 0
    for index, note in enumerate(notes):
        channel = low if index < cut else high
        if note['start_message'].channel != channel:
            note['start_message'].channel = channel
            note['end_message'].channel = channel
            moved += 1
    return moved


def removal_cost(note, notes):
    """notes is the group sorted by pitch; softer notes cost slightly less."""
    pitch_classes = [other['note'] % 12 for other in notes]
    cost = DOUBLED_COST if pitch_classes.count(note['note'] % 12) > 1 else SINGLE_COST
    if note is notes[0] or note is notes[-1]:
        cost += OUTER_COST
    return cost + note['velocity'] / 1000


def plan_group(group, max_notes, max_span):
    """The cheapest fewest notes to remove so the group is playable, [] when it already is, None when unsolved."""
    notes = sorted(group, key=lambda note: note['note'])
    if playable([note['note'] for note in notes], max_notes, max_span):
        return []
    for size in range(1, min(MAX_REMOVALS_PER_GROUP, len(notes) - 1) + 1):
        options = [
            removed for removed in itertools.combinations(notes, size)
            if playable([note['note'] for note in notes if note not in removed], max_notes, max_span)
        ]
        if options:
            return min(options, key=lambda removed: sum(removal_cost(note, notes) for note in removed))
    return None


def position(note, ticks_per_beat):
    beats = note['start_ticks'] / ticks_per_beat
    return 'm{} beat {:.2f}'.format(int(beats // BEATS_PER_BAR) + 1, beats % BEATS_PER_BAR + 1)


def note_name(note):
    return '{}{}'.format(NOTE_NAMES[note['note'] % 12], note['note'] // 12 - 1)


def reduce_in_place(midi_path, max_notes=DEFAULT_MAX_NOTES_PER_HAND, max_span=DEFAULT_MAX_SPAN, hands=None):
    """hands: None for an unsplit two-hand MIDI, or the low and high hand names of one player in a split MIDI;
    only their notes are reduced, and moved between the two when the split does not fit."""
    midi_file = mido.MidiFile(midi_path)
    channels = tuple(CHANNELS[hand] for hand in hands) if hands else None
    notes = [note for note in collect_notes(midi_file)
             if channels is None or note['start_message'].channel in channels]
    groups = group_notes(notes)
    removals = defaultdict(set)
    changes = []
    unsolved = []
    outer = 0
    moved = 0
    for group in groups:
        removed = plan_group(group, max_notes, max_span)
        if removed is None:
            unsolved.append(group)
            continue
        if channels:
            moved += reassign_hands([note for note in group if note not in removed], channels, max_notes, max_span)
        if not removed:
            continue
        pitches = [note['note'] for note in group]
        for note in removed:
            removals[note['track_index']].update((note['start_index'], note['end_index']))
            outer += note['note'] in (min(pitches), max(pitches))
        changes.append((group, removed))

    for track_index, track in enumerate(midi_file.tracks):
        midi_file.tracks[track_index] = rebuild_track_without_messages(track, removals[track_index])
    midi_file.save(midi_path)
    return {
        'midi_path': midi_path,
        'hands': hands,
        'moved': moved,
        'max_notes': max_notes,
        'max_span': max_span,
        'notes': len(notes),
        'groups': len(groups),
        'changes': changes,
        'outer': outer,
        'unsolved': unsolved,
        'ticks_per_beat': midi_file.ticks_per_beat,
    }


def format_reduction_stats(stats):
    tpb = stats['ticks_per_beat']
    removed = sum(len(notes) for _, notes in stats['changes'])
    lines = [
        'MIDI playable reduction stats',
        'MIDI: {}'.format(stats['midi_path']),
        'Two hands{}, at most {} notes and {} semitones per hand'.format(
            ' ({})'.format(', '.join(hand.replace('_', ' ') for hand in stats['hands'])) if stats['hands'] else '',
            stats['max_notes'], stats['max_span']),
        'Onset groups: {}, reduced {}, unsolved {}'.format(
            stats['groups'], len(stats['changes']), len(stats['unsolved'])),
        'Removed notes: {} of {} (top or bottom note {})'.format(removed, stats['notes'], stats['outer']),
    ]
    if stats['hands']:
        lines.append('Notes moved between the two hands: {}'.format(stats['moved']))
    for group, notes in stats['changes']:
        lines.append('- {}: removed {} from {}'.format(
            position(group[0], tpb),
            ' '.join(note_name(note) for note in sorted(notes, key=lambda note: note['note'])),
            ' '.join(note_name(note) for note in sorted(group, key=lambda note: note['note']))))
    for group in stats['unsolved']:
        lines.append('- {}: unsolved {}'.format(
            position(group[0], tpb), ' '.join(note_name(note) for note in sorted(group, key=lambda note: note['note']))))
    return '\n'.join(lines)


def apply_playable_reduction(midi_path, report_path=None, max_notes=DEFAULT_MAX_NOTES_PER_HAND,
                             max_span=DEFAULT_MAX_SPAN, hands=None):
    stats = reduce_in_place(midi_path, max_notes=max_notes, max_span=max_span, hands=hands)
    report_path = report_path or source_stats_path_for(midi_path)
    report = format_reduction_stats(stats)
    update_report(report_path, report)
    print(report)
    print('Updating playable reduction report: {}'.format(report_path))
    return stats


def choose_midi():
    root = tk.Tk()
    root.withdraw()
    return filedialog.askopenfilename(
        title='Choose cleaned MIDI to reduce to what two hands can play',
        filetypes=[
            ('MIDI files', '*.mid *.midi'),
            ('All files', '*.*'),
        ],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Remove the fewest notes so every onset group can be played by two hands, in place.',
    )
    parser.add_argument('input_midi', nargs='?', help='Cleaned MIDI to modify in place. Without it a dialog opens.')
    parser.add_argument('--report', help='Report path (default: the source transcription *_stats.txt).')
    parser.add_argument('--max-notes-per-hand', type=int, default=DEFAULT_MAX_NOTES_PER_HAND,
                        help='Most notes one hand plays at once (default: {}).'.format(DEFAULT_MAX_NOTES_PER_HAND))
    parser.add_argument('--max-span', type=int, default=DEFAULT_MAX_SPAN,
                        help='Widest span of one hand in semitones (default: {}).'.format(DEFAULT_MAX_SPAN))
    parser.add_argument('--three-hands', action='store_true',
                        help='The MIDI is split into three hands: reduce only the first player\'s left and second '
                             'right hand, leaving the other player\'s right hand alone.')
    args = parser.parse_args(argv)

    midi_path = args.input_midi or choose_midi()
    if not midi_path:
        print('No input selected.')
        return 1
    if not os.path.isfile(midi_path):
        print('MIDI file does not exist: {}'.format(midi_path))
        return 1

    stats = apply_playable_reduction(midi_path, report_path=args.report, max_notes=args.max_notes_per_hand,
                                     max_span=args.max_span, hands=THREE_HAND_PLAYER if args.three_hands else None)
    if not args.input_midi:
        removed = sum(len(notes) for _, notes in stats['changes'])
        messagebox.showinfo('Playable Reduction complete', 'Removed {} notes in {} onset groups; {} unsolved.'.format(
            removed, len(stats['changes']), len(stats['unsolved'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
