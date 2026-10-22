import argparse
import os
import sys
import tkinter as tk
from collections import defaultdict
from tkinter import filedialog, messagebox

import mido


MIN_NOTE_DURATION_SECONDS = 0.03
MIN_NOTE_VELOCITY = 20
LOW_VELOCITY_AND_SHORT_VELOCITY = 35
LOW_VELOCITY_AND_SHORT_SECONDS = 0.08
PIANO_MIN_PITCH = 21
PIANO_MAX_PITCH = 108

RULE_ORDER = [
    'too_short',
    'too_low_velocity',
    'low_velocity_and_short',
    'outside_piano_range',
]

RULE_LABELS = {
    'too_short': 'Duration < {:.3f}s'.format(MIN_NOTE_DURATION_SECONDS),
    'too_low_velocity': 'Velocity < {}'.format(MIN_NOTE_VELOCITY),
    'low_velocity_and_short': 'Velocity < {} and duration < {:.3f}s'.format(
        LOW_VELOCITY_AND_SHORT_VELOCITY,
        LOW_VELOCITY_AND_SHORT_SECONDS,
    ),
    'outside_piano_range': 'Pitch outside {}-{}'.format(PIANO_MIN_PITCH, PIANO_MAX_PITCH),
}


def cleaned_path_for(midi_path):
    filename, ext = os.path.splitext(midi_path)
    return filename + '_cleaned' + ext


def clean_report_path_for(midi_path):
    filename, _ = os.path.splitext(midi_path)
    return filename + '_clean_report.txt'


def rule_too_short(note):
    return note['duration_seconds'] < MIN_NOTE_DURATION_SECONDS


def rule_too_low_velocity(note):
    return note['velocity'] < MIN_NOTE_VELOCITY


def rule_low_velocity_and_short(note):
    return (
        note['velocity'] < LOW_VELOCITY_AND_SHORT_VELOCITY
        and note['duration_seconds'] < LOW_VELOCITY_AND_SHORT_SECONDS
    )


def rule_outside_piano_range(note):
    return note['note'] < PIANO_MIN_PITCH or note['note'] > PIANO_MAX_PITCH


RULES = [
    ('too_short', rule_too_short),
    ('too_low_velocity', rule_too_low_velocity),
    ('low_velocity_and_short', rule_low_velocity_and_short),
    ('outside_piano_range', rule_outside_piano_range),
]


def absolute_track_messages(midi_file, track):
    tempo = 500000
    absolute_ticks = 0
    absolute_seconds = 0.0

    for index, message in enumerate(track):
        absolute_ticks += message.time
        absolute_seconds += mido.tick2second(
            message.time,
            midi_file.ticks_per_beat,
            tempo,
        )
        yield index, absolute_ticks, absolute_seconds, message
        if message.type == 'set_tempo':
            tempo = message.tempo


def collect_notes(midi_file):
    notes = []

    for track_index, track in enumerate(midi_file.tracks):
        active_notes = defaultdict(list)
        for message_index, absolute_ticks, absolute_seconds, message in absolute_track_messages(midi_file, track):
            if message.type == 'note_on' and message.velocity > 0:
                key = (message.channel, message.note)
                active_notes[key].append({
                    'track_index': track_index,
                    'start_index': message_index,
                    'start_ticks': absolute_ticks,
                    'start_seconds': absolute_seconds,
                    'note': message.note,
                    'velocity': message.velocity,
                })
            elif message.type in ('note_off', 'note_on'):
                key = (message.channel, message.note)
                if active_notes[key]:
                    start = active_notes[key].pop()
                    notes.append({
                        'track_index': track_index,
                        'start_index': start['start_index'],
                        'end_index': message_index,
                        'note': start['note'],
                        'velocity': start['velocity'],
                        'duration_seconds': max(0.0, absolute_seconds - start['start_seconds']),
                        'duration_ticks': max(0, absolute_ticks - start['start_ticks']),
                    })

    return notes


def first_matching_rule(note):
    for rule_name, rule in RULES:
        if rule(note):
            return rule_name
    return None


def plan_removals(notes):
    removals = set()
    rule_counts = {rule_name: 0 for rule_name in RULE_ORDER}

    for note in notes:
        rule_name = first_matching_rule(note)
        if not rule_name:
            continue
        removals.add((note['track_index'], note['start_index']))
        removals.add((note['track_index'], note['end_index']))
        rule_counts[rule_name] += 1

    return removals, rule_counts


def rebuild_track_without_messages(track, removed_indexes):
    rebuilt = mido.MidiTrack()
    pending_ticks = 0

    for index, message in enumerate(track):
        pending_ticks += message.time
        if index in removed_indexes:
            continue

        copied = message.copy(time=pending_ticks)
        rebuilt.append(copied)
        pending_ticks = 0

    if pending_ticks:
        if rebuilt:
            rebuilt[-1].time += pending_ticks
        else:
            rebuilt.append(mido.MetaMessage('end_of_track', time=pending_ticks))

    return rebuilt


def clean_midi(input_path, output_path=None):
    if output_path is None:
        output_path = cleaned_path_for(input_path)

    midi_file = mido.MidiFile(input_path)
    notes = collect_notes(midi_file)
    removals, rule_counts = plan_removals(notes)

    cleaned = mido.MidiFile(
        type=midi_file.type,
        ticks_per_beat=midi_file.ticks_per_beat,
        charset=midi_file.charset,
        debug=midi_file.debug,
        clip=midi_file.clip,
    )

    removals_by_track = defaultdict(set)
    for track_index, message_index in removals:
        removals_by_track[track_index].add(message_index)

    for track_index, track in enumerate(midi_file.tracks):
        cleaned.tracks.append(
            rebuild_track_without_messages(track, removals_by_track[track_index])
        )

    cleaned.save(output_path)

    original_notes = len(notes)
    removed_notes = sum(rule_counts.values())
    kept_notes = original_notes - removed_notes
    stats = {
        'input_path': input_path,
        'output_path': output_path,
        'original_notes': original_notes,
        'removed_notes': removed_notes,
        'kept_notes': kept_notes,
        'rule_counts': rule_counts,
    }
    return output_path, stats


def format_clean_stats(stats):
    original_notes = stats['original_notes']
    removed_notes = stats['removed_notes']
    kept_notes = stats['kept_notes']

    lines = [
        'MIDI rule cleaning stats',
        'Input: {}'.format(stats['input_path']),
        'Output: {}'.format(stats['output_path']),
        'Original notes: {}'.format(original_notes),
        'Removed notes: {} ({:.2%})'.format(
            removed_notes,
            removed_notes / original_notes if original_notes else 0.0,
        ),
        'Kept notes: {} ({:.2%})'.format(
            kept_notes,
            kept_notes / original_notes if original_notes else 0.0,
        ),
        'Rule removals:',
    ]

    for rule_name in RULE_ORDER:
        count = stats['rule_counts'][rule_name]
        lines.append(
            '- {}: {} ({:.2%})'.format(
                RULE_LABELS[rule_name],
                count,
                count / original_notes if original_notes else 0.0,
            )
        )

    return '\n'.join(lines)


def write_clean_report(input_path, stats, report_path=None, append=False):
    if report_path is None:
        report_path = clean_report_path_for(input_path)
    report = format_clean_stats(stats)
    mode = 'a' if append else 'w'

    with open(report_path, mode, encoding='utf-8') as file:
        if append:
            file.write('\n\n')
        file.write(report)
        file.write('\n')

    print(report)
    if append:
        print('Appending clean report: {}'.format(report_path))
    else:
        print('Writing clean report: {}'.format(report_path))
    return report_path


def apply_rule_cleaning(input_path, output_path=None, report_path=None, append_report=False):
    output_path, stats = clean_midi(input_path, output_path)
    report_path = write_clean_report(
        input_path,
        stats,
        report_path=report_path,
        append=append_report,
    )
    return output_path, report_path, stats


def choose_midi():
    root = tk.Tk()
    root.withdraw()
    return filedialog.askopenfilename(
        title='Choose MIDI to clean',
        filetypes=[
            ('MIDI files', '*.mid *.midi'),
            ('All files', '*.*'),
        ],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description='Conservative rule-based MIDI cleanup.')
    parser.add_argument('input_midi', nargs='?', help='Input MIDI file.')
    parser.add_argument('-o', '--output', help='Output cleaned MIDI path.')
    args = parser.parse_args(argv)

    input_path = args.input_midi or choose_midi()
    if not input_path:
        print('No input selected.')
        return 1
    if not os.path.isfile(input_path):
        messagebox.showerror('MIDI Cleaner', 'MIDI file does not exist.')
        return 1

    try:
        output_path, report_path, _ = apply_rule_cleaning(input_path, args.output)
    except Exception as error:
        messagebox.showerror('MIDI Cleaner failed', str(error))
        raise

    if not args.input_midi:
        messagebox.showinfo(
            'MIDI Cleaner complete',
            'Output:\n{}\n\nReport:\n{}'.format(output_path, report_path),
        )
    return 0


if __name__ == '__main__':
    sys.exit(main())
