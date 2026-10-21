import argparse
import os
import sys
import tkinter as tk
from collections import defaultdict
from tkinter import filedialog, messagebox

import mido


GROUP_START_WINDOW_SECONDS = 0.05
BEAM_WIDTH = 32

# Span in semitones within one onset group for one hand.
COMFORT_SPAN = 12
HARD_SPAN = 14
SPAN_COST_PER_SEMITONE = 3.0
HARD_SPAN_COST_PER_SEMITONE = 20.0

# Soft register preference: left hand above LEFT_COMFORT_MAX, right hand below RIGHT_COMFORT_MIN.
LEFT_COMFORT_MAX = 62
RIGHT_COMFORT_MIN = 55
REGISTER_COST_PER_SEMITONE = 1.0

# Hand movement cost is semitones per second since that hand's previous onset.
MOVE_COST_WEIGHT = 0.01
MIN_MOVE_SECONDS = 0.05

# Extra simultaneous notes for a hand inside a fast single-note run: at least
# FAST_RUN_MIN_NOTES consecutive single notes, each within FAST_RUN_SECONDS of the previous one.
FAST_RUN_SECONDS = 0.25
FAST_RUN_MIN_NOTES = 3
FAST_RUN_EXTRA_NOTE_COST = 3.0

CROSSING_COST_PER_SEMITONE = 3.0
MAX_FINGERS = 5
EXTRA_FINGER_COST = 20.0

RIGHT_HAND_CHANNEL = 0
LEFT_HAND_CHANNEL = 2


def collect_tempo_changes(midi_file):
    events = []

    for track in midi_file.tracks:
        absolute_ticks = 0
        for message in track:
            absolute_ticks += message.time
            if message.type == 'set_tempo':
                events.append((absolute_ticks, message.tempo))

    events.sort(key=lambda item: item[0])
    changes = [(0, 500000)]

    for ticks, tempo in events:
        if ticks == 0:
            changes[-1] = (0, tempo)
        else:
            changes.append((ticks, tempo))

    return changes


def tick_to_second(ticks, ticks_per_beat, tempo_changes):
    seconds = 0.0
    previous_ticks, previous_tempo = tempo_changes[0]

    for change_ticks, tempo in tempo_changes[1:]:
        if ticks <= change_ticks:
            break
        seconds += mido.tick2second(
            change_ticks - previous_ticks,
            ticks_per_beat,
            previous_tempo,
        )
        previous_ticks = change_ticks
        previous_tempo = tempo

    seconds += mido.tick2second(ticks - previous_ticks, ticks_per_beat, previous_tempo)
    return seconds


def collect_notes(midi_file):
    tempo_changes = collect_tempo_changes(midi_file)
    notes = []

    for track_index, track in enumerate(midi_file.tracks):
        active_notes = defaultdict(list)
        absolute_ticks = 0

        for message_index, message in enumerate(track):
            absolute_ticks += message.time

            if message.type == 'note_on' and message.velocity > 0:
                key = (message.channel, message.note)
                active_notes[key].append({
                    'track_index': track_index,
                    'start_index': message_index,
                    'start_ticks': absolute_ticks,
                    'start_seconds': tick_to_second(
                        absolute_ticks,
                        midi_file.ticks_per_beat,
                        tempo_changes,
                    ),
                    'note': message.note,
                    'velocity': message.velocity,
                    'start_message': message,
                })
            elif message.type in ('note_off', 'note_on'):
                key = (message.channel, message.note)
                if active_notes[key]:
                    start = active_notes[key].pop()
                    notes.append({
                        'track_index': track_index,
                        'start_index': start['start_index'],
                        'end_index': message_index,
                        'start_ticks': start['start_ticks'],
                        'end_ticks': absolute_ticks,
                        'start_seconds': start['start_seconds'],
                        'note': start['note'],
                        'velocity': start['velocity'],
                        'start_message': start['start_message'],
                        'end_message': message,
                        'hand': None,
                    })

    notes.sort(key=lambda item: (item['start_seconds'], item['note']))
    return notes


def group_notes(notes):
    groups = []
    current = []
    group_start = None

    for note in notes:
        if not current:
            current = [note]
            group_start = note['start_seconds']
            continue

        if note['start_seconds'] - group_start <= GROUP_START_WINDOW_SECONDS:
            current.append(note)
        else:
            groups.append(current)
            current = [note]
            group_start = note['start_seconds']

    if current:
        groups.append(current)

    return groups


def span_cost(pitches):
    if len(pitches) < 2:
        return 0.0
    span = pitches[-1] - pitches[0]
    if span <= COMFORT_SPAN:
        return 0.0
    if span <= HARD_SPAN:
        return (span - COMFORT_SPAN) * SPAN_COST_PER_SEMITONE
    return ((HARD_SPAN - COMFORT_SPAN) * SPAN_COST_PER_SEMITONE
            + (span - HARD_SPAN) * HARD_SPAN_COST_PER_SEMITONE)


def register_cost(left_pitches, right_pitches):
    cost = sum(max(0, pitch - LEFT_COMFORT_MAX) for pitch in left_pitches)
    cost += sum(max(0, RIGHT_COMFORT_MIN - pitch) for pitch in right_pitches)
    return cost * REGISTER_COST_PER_SEMITONE


def next_run_length(pitches, onset, last_onset, run_length):
    """Number of consecutive fast single notes ending at this onset for one hand."""
    if len(pitches) != 1:
        return 0
    if last_onset is not None and onset - last_onset < FAST_RUN_SECONDS and run_length > 0:
        return run_length + 1
    return 1


def in_fast_run(onset, last_onset, run_length):
    return (last_onset is not None and onset - last_onset < FAST_RUN_SECONDS
            and run_length >= FAST_RUN_MIN_NOTES)


def hand_cost(pitches, onset, last_pitch, last_onset, run_length):
    """Cost for one hand playing sorted pitches at onset, given its previous position."""
    if not pitches:
        return 0.0

    cost = span_cost(pitches)
    cost += max(0, len(pitches) - MAX_FINGERS) * EXTRA_FINGER_COST

    if last_onset is not None:
        elapsed = max(onset - last_onset, MIN_MOVE_SECONDS)
        center = sum(pitches) / len(pitches)
        cost += MOVE_COST_WEIGHT * abs(center - last_pitch) / elapsed
        if in_fast_run(onset, last_onset, run_length):
            cost += ((len(pitches) - 1) * FAST_RUN_EXTRA_NOTE_COST
                     * (1 - elapsed / FAST_RUN_SECONDS))
    return cost


def split_cost(left_pitches, right_pitches, onset, state):
    cost = hand_cost(left_pitches, onset, state['left_pitch'], state['left_onset'], state['left_run'])
    cost += hand_cost(right_pitches, onset, state['right_pitch'], state['right_onset'], state['right_run'])
    cost += register_cost(left_pitches, right_pitches)
    if left_pitches and right_pitches and left_pitches[-1] > right_pitches[0]:
        cost += (left_pitches[-1] - right_pitches[0]) * CROSSING_COST_PER_SEMITONE
    return cost


def next_state(state, left_pitches, right_pitches, onset, cost, split_index, parent):
    new_state = dict(state)
    if left_pitches:
        new_state['left_pitch'] = sum(left_pitches) / len(left_pitches)
        new_state['left_run'] = next_run_length(left_pitches, onset, state['left_onset'], state['left_run'])
        new_state['left_onset'] = onset
    if right_pitches:
        new_state['right_pitch'] = sum(right_pitches) / len(right_pitches)
        new_state['right_run'] = next_run_length(right_pitches, onset, state['right_onset'], state['right_run'])
        new_state['right_onset'] = onset
    new_state['cost'] = state['cost'] + cost
    new_state['split_index'] = split_index
    new_state['parent'] = parent
    return new_state


def search_splits(groups):
    """Beam search over onset groups; each group sends its lowest k notes to the left hand."""
    beam = [{
        'left_pitch': None,
        'left_onset': None,
        'left_run': 0,
        'right_pitch': None,
        'right_onset': None,
        'right_run': 0,
        'cost': 0.0,
        'split_index': None,
        'parent': None,
    }]

    for group in groups:
        pitches = sorted(note['note'] for note in group)
        onset = group[0]['start_seconds']
        candidates = []
        for state in beam:
            for split_index in range(len(pitches) + 1):
                left_pitches = pitches[:split_index]
                right_pitches = pitches[split_index:]
                cost = split_cost(left_pitches, right_pitches, onset, state)
                candidates.append(next_state(
                    state, left_pitches, right_pitches, onset, cost, split_index, state,
                ))
        candidates.sort(key=lambda item: item['cost'])
        beam = candidates[:BEAM_WIDTH]

    split_indexes = []
    state = beam[0]
    while state['parent'] is not None:
        split_indexes.append(state['split_index'])
        state = state['parent']
    split_indexes.reverse()
    return split_indexes


def assign_groups(groups):
    for group, split_index in zip(groups, search_splits(groups)):
        ordered = sorted(group, key=lambda note: note['note'])
        for index, note in enumerate(ordered):
            note['hand'] = 'left' if index < split_index else 'right'


def summarize_groups(groups):
    wide_span_groups = 0
    fast_run_chords = 0
    last_onset = {'left': None, 'right': None}
    run_length = {'left': 0, 'right': 0}

    for group in groups:
        onset = group[0]['start_seconds']
        wide = False
        for hand in ('left', 'right'):
            pitches = [note['note'] for note in group if note['hand'] == hand]
            if not pitches:
                continue
            if max(pitches) - min(pitches) > COMFORT_SPAN:
                wide = True
            if len(pitches) > 1 and in_fast_run(onset, last_onset[hand], run_length[hand]):
                fast_run_chords += 1
            run_length[hand] = next_run_length(pitches, onset, last_onset[hand], run_length[hand])
            last_onset[hand] = onset
        if wide:
            wide_span_groups += 1

    return wide_span_groups, fast_run_chords


def apply_channels(notes):
    for note in notes:
        channel = RIGHT_HAND_CHANNEL if note['hand'] == 'right' else LEFT_HAND_CHANNEL
        note['start_message'].channel = channel
        note['end_message'].channel = channel


def split_hands_in_place(midi_path):
    midi_file = mido.MidiFile(midi_path)
    notes = collect_notes(midi_file)
    groups = group_notes(notes)
    assign_groups(groups)
    apply_channels(notes)
    midi_file.save(midi_path)

    right_notes = sum(1 for note in notes if note['hand'] == 'right')
    left_notes = sum(1 for note in notes if note['hand'] == 'left')
    wide_span_groups, fast_run_chords = summarize_groups(groups)

    stats = {
        'midi_path': midi_path,
        'total_notes': len(notes),
        'right_notes': right_notes,
        'left_notes': left_notes,
        'onset_groups': len(groups),
        'wide_span_groups': wide_span_groups,
        'fast_run_chords': fast_run_chords,
    }
    return stats


def format_split_stats(stats):
    total_notes = stats['total_notes']
    lines = [
        'MIDI provisional hand split stats',
        'MIDI: {}'.format(stats['midi_path']),
        'Original notes: {}'.format(total_notes),
        'Channel 1 / right hand notes: {} ({:.2%})'.format(
            stats['right_notes'],
            stats['right_notes'] / total_notes if total_notes else 0.0,
        ),
        'Channel 3 / left hand notes: {} ({:.2%})'.format(
            stats['left_notes'],
            stats['left_notes'] / total_notes if total_notes else 0.0,
        ),
        'Onset groups: {}'.format(stats['onset_groups']),
        'Groups with a hand span > {} semitones: {}'.format(COMFORT_SPAN, stats['wide_span_groups']),
        'Multi-note hand inside a fast run (< {:.2f}s): {}'.format(
            FAST_RUN_SECONDS,
            stats['fast_run_chords'],
        ),
    ]
    return '\n'.join(lines)


def write_split_report(stats, report_path=None, append=False):
    report = format_split_stats(stats)
    if report_path:
        mode = 'a' if append else 'w'
        with open(report_path, mode, encoding='utf-8') as file:
            if append:
                file.write('\n\n')
            file.write(report)
            file.write('\n')

    print(report)
    if report_path:
        if append:
            print('Appending hand split report: {}'.format(report_path))
        else:
            print('Writing hand split report: {}'.format(report_path))


def apply_hand_split(midi_path, report_path=None, append_report=False):
    stats = split_hands_in_place(midi_path)
    write_split_report(stats, report_path=report_path, append=append_report)
    return stats


def choose_midi():
    root = tk.Tk()
    root.withdraw()
    return filedialog.askopenfilename(
        title='Choose cleaned MIDI to mark channels',
        filetypes=[
            ('MIDI files', '*.mid *.midi'),
            ('All files', '*.*'),
        ],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description='Mark provisional piano hand split by MIDI channel.')
    parser.add_argument('input_midi', nargs='?', help='Input MIDI file to modify in place.')
    parser.add_argument('--report', help='Optional report path.')
    parser.add_argument('--append-report', action='store_true', help='Append to report instead of overwriting.')
    args = parser.parse_args(argv)

    midi_path = args.input_midi or choose_midi()
    if not midi_path:
        print('No input selected.')
        return 1
    if not os.path.isfile(midi_path):
        messagebox.showerror('Hand Split', 'MIDI file does not exist.')
        return 1

    try:
        apply_hand_split(midi_path, report_path=args.report, append_report=args.append_report)
    except Exception as error:
        messagebox.showerror('Hand Split failed', str(error))
        raise

    if not args.input_midi:
        messagebox.showinfo('Hand Split complete', 'Updated in place:\n{}'.format(midi_path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
