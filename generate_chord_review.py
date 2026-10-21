import argparse
import json
import math
import os
import subprocess
import sys
import tkinter as tk
from collections import defaultdict
from pathlib import Path
from tkinter import filedialog, messagebox

import mido

from node_utils import node_executable


DEFAULT_TEMPO = 500000
DEFAULT_TIME_SIGNATURE = (4, 4)
RIGHT_HAND_CHANNEL = 0
LEFT_HAND_CHANNEL = 2
CHORD_CANDIDATE_COUNT = 3
CHORD_GROUP_WINDOW_SECONDS = 0.05

NOTE_NAMES_SHARP = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')


def pitch_name(note_number):
    octave = note_number // 12 - 1
    return '{}{}'.format(NOTE_NAMES_SHARP[note_number % 12], octave)


def hand_for_channel(channel, pitch):
    if channel == RIGHT_HAND_CHANNEL:
        return 'Right'
    if channel == LEFT_HAND_CHANNEL:
        return 'Left'
    if pitch < 60:
        return 'Left'
    return 'Right'


def collect_tempo_changes(midi_file):
    events = []
    for track in midi_file.tracks:
        absolute_ticks = 0
        for message in track:
            absolute_ticks += message.time
            if message.type == 'set_tempo':
                events.append((absolute_ticks, message.tempo))

    events.sort(key=lambda item: item[0])
    changes = [(0, DEFAULT_TEMPO)]
    for ticks, tempo in events:
        if ticks == 0:
            changes[-1] = (0, tempo)
        else:
            changes.append((ticks, tempo))
    return changes


def collect_time_signatures(midi_file):
    events = []
    for track in midi_file.tracks:
        absolute_ticks = 0
        for message in track:
            absolute_ticks += message.time
            if message.type == 'time_signature':
                events.append((absolute_ticks, message.numerator, message.denominator))

    events.sort(key=lambda item: item[0])
    if not events:
        events.append((0, DEFAULT_TIME_SIGNATURE[0], DEFAULT_TIME_SIGNATURE[1]))
    return events


def time_signature_at_tick(ticks, time_signatures):
    current = time_signatures[0]
    for event in time_signatures[1:]:
        if ticks < event[0]:
            break
        current = event
    return current[1], current[2]


def beat_length_ticks(ticks_per_beat, denominator):
    return ticks_per_beat * 4 / denominator


def bar_length_ticks(ticks_per_beat, numerator, denominator):
    return beat_length_ticks(ticks_per_beat, denominator) * numerator


def tick_to_second(ticks, ticks_per_beat, tempo_changes):
    seconds = 0.0
    previous_ticks, previous_tempo = tempo_changes[0]
    for change_ticks, tempo in tempo_changes[1:]:
        if ticks <= change_ticks:
            break
        seconds += mido.tick2second(change_ticks - previous_ticks, ticks_per_beat, previous_tempo)
        previous_ticks = change_ticks
        previous_tempo = tempo
    seconds += mido.tick2second(ticks - previous_ticks, ticks_per_beat, previous_tempo)
    return seconds


def collect_notes(midi_file, tempo_changes):
    notes = []
    active_notes = defaultdict(list)

    for track_index, track in enumerate(midi_file.tracks):
        absolute_ticks = 0
        for message_index, message in enumerate(track):
            absolute_ticks += message.time

            if message.type == 'note_on' and message.velocity > 0:
                key = (track_index, message.channel, message.note)
                active_notes[key].append({
                    'track_index': track_index,
                    'message_index': message_index,
                    'start_ticks': absolute_ticks,
                    'channel': message.channel,
                    'pitch': message.note,
                    'velocity': message.velocity,
                })
            elif message.type in ('note_off', 'note_on'):
                key = (track_index, message.channel, message.note)
                if active_notes[key]:
                    start = active_notes[key].pop()
                    notes.append({
                        'track_index': track_index,
                        'start_index': start['message_index'],
                        'end_index': message_index,
                        'start_ticks': start['start_ticks'],
                        'end_ticks': absolute_ticks,
                        'start_seconds': tick_to_second(
                            start['start_ticks'],
                            midi_file.ticks_per_beat,
                            tempo_changes,
                        ),
                        'channel': start['channel'],
                        'hand': hand_for_channel(start['channel'], start['pitch']),
                        'pitch': start['pitch'],
                        'velocity': start['velocity'],
                        'duration_ticks': max(0, absolute_ticks - start['start_ticks']),
                    })

    notes.sort(key=lambda note: (note['start_ticks'], note['pitch'], note['end_ticks']))
    return notes


def end_ticks(notes):
    if not notes:
        return 0
    return max(note['end_ticks'] for note in notes)


def total_bars(notes, ticks_per_beat, time_signatures):
    if not notes:
        return 0
    max_tick = end_ticks(notes)
    numerator, denominator = time_signature_at_tick(max_tick, time_signatures)
    length = bar_length_ticks(ticks_per_beat, numerator, denominator)
    return int(math.ceil(max_tick / length))


def primary_bpm(tempo_changes, note_end_ticks):
    durations_by_tempo = defaultdict(int)
    for index, (change_start_ticks, tempo) in enumerate(tempo_changes):
        if index + 1 < len(tempo_changes):
            stop_ticks = tempo_changes[index + 1][0]
        else:
            stop_ticks = note_end_ticks
        segment_stop = min(stop_ticks, note_end_ticks)
        durations_by_tempo[tempo] += max(0, segment_stop - change_start_ticks)

    if not durations_by_tempo:
        return mido.tempo2bpm(tempo_changes[0][1])
    primary_tempo = max(durations_by_tempo.items(), key=lambda item: item[1])[0]
    return mido.tempo2bpm(primary_tempo)


def slot_for_note(note, ticks_per_beat, time_signatures):
    numerator, denominator = time_signature_at_tick(note['start_ticks'], time_signatures)
    beat_ticks = beat_length_ticks(ticks_per_beat, denominator)
    bar_ticks = beat_ticks * numerator
    bar_index = int(note['start_ticks'] // bar_ticks) + 1
    ticks_in_bar = note['start_ticks'] - int((bar_index - 1) * bar_ticks)
    beat_index = int(ticks_in_bar // beat_ticks) + 1

    if numerator == 4 and beat_index > 4:
        beat_index = 4

    return bar_index, beat_index


def beat_value_for_tick(ticks, ticks_per_beat, time_signatures):
    numerator, denominator = time_signature_at_tick(ticks, time_signatures)
    beat_ticks = beat_length_ticks(ticks_per_beat, denominator)
    bar_ticks = beat_ticks * numerator
    bar_index = int(ticks // bar_ticks) + 1
    ticks_in_bar = ticks - int((bar_index - 1) * bar_ticks)
    return ticks_in_bar / beat_ticks + 1


def beat_rows(total_bar_count):
    rows = []
    for bar in range(1, total_bar_count + 1):
        for beat in range(1, 5):
            rows.append({
                'bar': bar,
                'beat': beat,
                'notes': [],
            })
    return rows


def group_notes_by_beat(notes, ticks_per_beat, time_signatures):
    grouped = defaultdict(list)
    for note in notes:
        grouped[slot_for_note(note, ticks_per_beat, time_signatures)].append(note)
    return grouped


def chord_groups_for_row(notes):
    groups = []
    current = []
    group_start = None

    for note in sorted(notes, key=lambda item: (item['start_seconds'], item['pitch'])):
        if not current:
            current = [note]
            group_start = note['start_seconds']
            continue

        if note['start_seconds'] - group_start <= CHORD_GROUP_WINDOW_SECONDS:
            current.append(note)
        else:
            groups.append(current)
            current = [note]
            group_start = note['start_seconds']

    if current:
        groups.append(current)

    return groups


def beat_text_for_groups(groups, ticks_per_beat, time_signatures):
    if not groups:
        return None

    values = []
    for group in groups:
        start_tick = min(note['start_ticks'] for note in group)
        values.append(format_beat_value(beat_value_for_tick(start_tick, ticks_per_beat, time_signatures)))
    return ';'.join(values)


def format_beat_value(value):
    scaled = math.floor((value + 0.000000001) * 100) / 100
    return '{:.2f}'.format(scaled)


def notes_for_hand(notes, hand):
    pitches = sorted({note['pitch'] for note in notes if note['hand'] == hand})
    if not pitches:
        return '-'
    return ','.join(pitch_name(pitch) for pitch in pitches)


def range_text(notes):
    if not notes:
        return '-'
    pitches = [note['pitch'] for note in notes]
    return '{}-{}'.format(pitch_name(min(pitches)), pitch_name(max(pitches)))


def bass_text(notes):
    if not notes:
        return '-'
    return pitch_name(min(note['pitch'] for note in notes))


def note_names_for_chord(notes):
    pitches = sorted({note['pitch'] for note in notes})
    return [pitch_name(pitch) for pitch in pitches]


def detect_chords(note_groups):
    script_path = Path(__file__).with_name('detect_chords_tonal.js')
    payload = {
        'maxCandidates': CHORD_CANDIDATE_COUNT,
        'noteGroups': note_groups,
    }
    completed = subprocess.run(
        [node_executable(), str(script_path)],
        input=json.dumps(payload),
        text=True,
        encoding='utf-8',
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def format_chord(candidates):
    if not candidates:
        return '-'
    return ','.join(candidates)


def markdown_escape(value):
    return str(value).replace('|', '\\|')


def build_chord_review(midi_path):
    midi_file = mido.MidiFile(midi_path)
    tempo_changes = collect_tempo_changes(midi_file)
    time_signatures = collect_time_signatures(midi_file)
    notes = collect_notes(midi_file, tempo_changes)
    row_count = total_bars(notes, midi_file.ticks_per_beat, time_signatures)
    rows = beat_rows(row_count)
    notes_by_slot = group_notes_by_beat(notes, midi_file.ticks_per_beat, time_signatures)

    note_groups = []
    group_counts = []
    for row in rows:
        row_notes = notes_by_slot[(row['bar'], row['beat'])]
        row['notes'] = row_notes
        row_groups = chord_groups_for_row(row_notes)
        row['groups'] = row_groups
        group_counts.append(len(row_groups))
        for group in row_groups:
            note_groups.append(note_names_for_chord(group))

    flat_chord_candidates = detect_chords(note_groups)
    chord_candidates = []
    index = 0
    for count in group_counts:
        chord_candidates.append(flat_chord_candidates[index:index + count])
        index += count

    numerator, denominator = time_signature_at_tick(0, time_signatures)
    lines = ['# Chord Review Data', '']
    lines.extend([
        '## Global Info',
        '',
        '- Source filename: {}'.format(os.path.basename(midi_path)),
        '- BPM: {:.2f}'.format(primary_bpm(tempo_changes, end_ticks(notes))),
        '- Time signature: {}/{}'.format(numerator, denominator),
        '- Total bars: {}'.format(row_count),
        '- Total notes: {}'.format(len(notes)),
        '',
    ])
    lines.extend([
        '## Chord Chart',
        '',
        '| Bar | Beat | Chord | Bass | LH | RH | Range | NoteCount |',
        '| ---: | ---: | --- | --- | --- | --- | --- | ---: |',
    ])

    for row, candidates in zip(rows, chord_candidates):
        row_notes = row['notes']
        beat_text = beat_text_for_groups(row['groups'], midi_file.ticks_per_beat, time_signatures)
        if beat_text is None:
            beat_text = format_beat_value(row['beat'])
        lines.append('| {} | {} | {} | {} | {} | {} | {} | {} |'.format(
            row['bar'],
            markdown_escape(beat_text),
            markdown_escape(';'.join(format_chord(group_candidates) for group_candidates in candidates) or '-'),
            bass_text(row_notes),
            notes_for_hand(row_notes, 'Left'),
            notes_for_hand(row_notes, 'Right'),
            range_text(row_notes),
            len(row_notes),
        ))
    lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def default_output_path(input_midi):
    midi_path = Path(input_midi)
    return str(midi_path.with_name('{}_chord_review.md'.format(midi_path.stem)))


def choose_midi():
    root = tk.Tk()
    root.withdraw()
    return filedialog.askopenfilename(
        title='Choose MIDI to generate chord review',
        filetypes=[
            ('MIDI files', '*.mid *.midi'),
            ('All files', '*.*'),
        ],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description='Generate chord review Markdown data.')
    parser.add_argument('input_midi', nargs='?', help='Input MIDI file.')
    parser.add_argument('-o', '--output', help='Output Markdown path.')
    args = parser.parse_args(argv)

    input_midi = args.input_midi or choose_midi()
    if not input_midi:
        print('No input selected.')
        return 1

    output_path = args.output or default_output_path(input_midi)
    review = build_chord_review(input_midi)
    with open(output_path, 'w', encoding='utf-8') as file:
        file.write(review)

    print('Writing chord review: {}'.format(output_path))
    if args.input_midi is None:
        messagebox.showinfo('Chord Review complete', 'Output:\n{}'.format(output_path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
