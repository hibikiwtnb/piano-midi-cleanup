import os
from collections import defaultdict

import mido


SHORT_NOTE_SECONDS = 0.05
LOW_VELOCITY = 30
NOTE_NAMES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')


def stats_path_for(midi_path):
    filename, _ = os.path.splitext(midi_path)
    return filename + '_stats.txt'


def source_stats_path_for(midi_path):
    """Stats file of the source transcription: song_bpmfix_cleaned.mid -> song_stats.txt."""
    filename, _ = os.path.splitext(midi_path)
    for suffix in ('_cleaned', '_bpmfix'):
        if filename.endswith(suffix):
            filename = filename[:-len(suffix)]
    return filename + '_stats.txt'


def note_name(note_number):
    octave = note_number // 12 - 1
    return '{}{}'.format(NOTE_NAMES[note_number % 12], octave)


def format_note(note_number):
    if note_number is None:
        return 'N/A'
    return '{} ({})'.format(note_number, note_name(note_number))


def _iter_timed_messages(midi_file):
    tempo = 500000
    elapsed_seconds = 0.0

    for message in mido.merge_tracks(midi_file.tracks):
        elapsed_seconds += mido.tick2second(
            message.time,
            midi_file.ticks_per_beat,
            tempo,
        )
        if message.type == 'set_tempo':
            tempo = message.tempo
        yield elapsed_seconds, message


def analyze_midi(midi_path):
    midi_file = mido.MidiFile(midi_path)
    active_notes = defaultdict(list)
    notes = []
    polyphony_events = []

    for time_seconds, message in _iter_timed_messages(midi_file):
        if message.type == 'note_on' and message.velocity > 0:
            key = (message.channel, message.note)
            active_notes[key].append((time_seconds, message.velocity))
            polyphony_events.append((time_seconds, 1))
        elif message.type in ('note_off', 'note_on'):
            key = (message.channel, message.note)
            if active_notes[key]:
                start_seconds, velocity = active_notes[key].pop()
                notes.append({
                    'note': message.note,
                    'velocity': velocity,
                    'duration': max(0.0, time_seconds - start_seconds),
                })
                polyphony_events.append((time_seconds, -1))

    total_notes = len(notes)
    if not notes:
        return {
            'midi_path': midi_path,
            'total_notes': 0,
            'short_notes': 0,
            'short_note_ratio': 0.0,
            'low_velocity_notes': 0,
            'low_velocity_ratio': 0.0,
            'lowest_note': None,
            'highest_note': None,
            'pitch_span': 0,
            'average_velocity': 0.0,
            'max_polyphony': 0,
        }

    short_notes = sum(1 for note in notes if note['duration'] < SHORT_NOTE_SECONDS)
    low_velocity_notes = sum(1 for note in notes if note['velocity'] <= LOW_VELOCITY)
    lowest_note = min(note['note'] for note in notes)
    highest_note = max(note['note'] for note in notes)
    average_velocity = sum(note['velocity'] for note in notes) / total_notes

    current_polyphony = 0
    max_polyphony = 0
    for _, delta in sorted(polyphony_events, key=lambda item: (item[0], item[1])):
        current_polyphony += delta
        max_polyphony = max(max_polyphony, current_polyphony)

    return {
        'midi_path': midi_path,
        'total_notes': total_notes,
        'short_notes': short_notes,
        'short_note_ratio': short_notes / total_notes,
        'low_velocity_notes': low_velocity_notes,
        'low_velocity_ratio': low_velocity_notes / total_notes,
        'lowest_note': lowest_note,
        'highest_note': highest_note,
        'pitch_span': highest_note - lowest_note,
        'average_velocity': average_velocity,
        'max_polyphony': max_polyphony,
    }


def format_stats(stats):
    return '\n'.join([
        'MIDI quality stats',
        'MIDI: {}'.format(stats['midi_path']),
        'Total notes: {}'.format(stats['total_notes']),
        'Very short notes (< {:.3f}s): {} ({:.2%})'.format(
            SHORT_NOTE_SECONDS,
            stats['short_notes'],
            stats['short_note_ratio'],
        ),
        'Low velocity notes (<= {}): {} ({:.2%})'.format(
            LOW_VELOCITY,
            stats['low_velocity_notes'],
            stats['low_velocity_ratio'],
        ),
        'Lowest note: {}'.format(format_note(stats['lowest_note'])),
        'Highest note: {}'.format(format_note(stats['highest_note'])),
        'Pitch span: {} semitone(s)'.format(stats['pitch_span']),
        'Average velocity: {:.2f}'.format(stats['average_velocity']),
        'Max polyphony: {}'.format(stats['max_polyphony']),
    ])


def write_midi_stats(midi_path):
    stats = analyze_midi(midi_path)
    report = format_stats(stats)
    output_path = stats_path_for(midi_path)

    with open(output_path, 'w', encoding='utf-8') as file:
        file.write(report)
        file.write('\n')

    print(report)
    print('Writing MIDI stats: {}'.format(output_path))
    return output_path, stats
