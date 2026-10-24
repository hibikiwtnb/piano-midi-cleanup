"""MIDI first-beat alignment and report helpers."""

import math

import mido

from bpmfix_utils import estimate_first_beat, format_auto_first_beat_stats
from midi_stats import source_stats_path_for
from report_utils import update_report


DEFAULT_MIDI_TEMPO = 500000


def midi_tempo(midi_file):
    for track in midi_file.tracks:
        for message in track:
            if message.type == 'set_tempo':
                return message.tempo
    return DEFAULT_MIDI_TEMPO


def build_bpm_fixed_midi(midi_path, target_bpm, first_beat_delay, shift_ticks=None):
    """Rewrite midi_path at target_bpm with the first beat on a bar line.

    shift_ticks reuses another file's shift so files of one song keep a shared timeline.
    """
    midi = mido.MidiFile(midi_path)
    source_tempo = midi_tempo(midi)
    target_tempo = int(mido.bpm2tempo(target_bpm))
    bar_ticks = midi.ticks_per_beat * 4
    user_offset_ticks = int(round(first_beat_delay * midi.ticks_per_beat * target_bpm / 60.0))
    scaled_tracks = []
    earliest_note_tick = None

    for track in midi.tracks:
        source_absolute_tick = 0
        scaled_events = []
        for message in track:
            source_absolute_tick += message.time
            absolute_tick = (source_absolute_tick * source_tempo + target_tempo // 2) // target_tempo
            copied = message.copy(time=0)
            if copied.type != 'set_tempo':
                scaled_events.append((absolute_tick, copied))
            if copied.type == 'note_on' and copied.velocity > 0:
                earliest_note_tick = absolute_tick if earliest_note_tick is None else min(earliest_note_tick, absolute_tick)
        scaled_tracks.append(scaled_events)

    padding_bars = 0
    if shift_ticks is None:
        shift_ticks = -user_offset_ticks
        if earliest_note_tick is not None and earliest_note_tick + shift_ticks < 0:
            padding_bars = math.ceil(-(earliest_note_tick + shift_ticks) / bar_ticks)
            shift_ticks += padding_bars * bar_ticks

    output = mido.MidiFile(type=midi.type, ticks_per_beat=midi.ticks_per_beat)
    for track_index, events in enumerate(scaled_tracks):
        output_track = mido.MidiTrack()
        output.tracks.append(output_track)
        previous_tick = 0
        if track_index == 0:
            output_track.append(mido.MetaMessage('set_tempo', tempo=target_tempo, time=0))
        for absolute_tick, message in events:
            corrected_tick = max(0, absolute_tick + shift_ticks)
            message.time = corrected_tick - previous_tick
            previous_tick = corrected_tick
            output_track.append(message)
    output.save(midi_path)
    return {'bpm': target_bpm, 'first_beat_seconds': first_beat_delay,
            'shift_ticks': shift_ticks, 'padding_bars': padding_bars}


def format_first_beat_stats(midi_path, result):
    if result is None:
        return '\n'.join(('MIDI first beat stats', 'MIDI: {}'.format(midi_path), 'First beat: not changed'))
    return '\n'.join(('MIDI first beat stats', 'MIDI: {}'.format(midi_path),
                      'BPM: {:.2f}'.format(result['bpm']),
                      'First beat in source timeline: {:.3f}s'.format(result['first_beat_seconds']),
                      'Shift ticks: {}'.format(result['shift_ticks']),
                      'Padding bars: {}'.format(result['padding_bars'])))


def append_first_beat_stats(midi_path, result, report_path=None):
    if result is None:
        return ''
    report_path = report_path or source_stats_path_for(midi_path)
    report = format_first_beat_stats(midi_path, result)
    update_report(report_path, report)
    print(report)
    print('Updating first beat report: {}'.format(report_path))
    return report


def auto_align_first_beat(midi_path, report_path=None):
    estimate = estimate_first_beat(midi_path)
    if estimate is None:
        print('No notes; first beat not aligned.')
        return None
    bpm = mido.tempo2bpm(midi_tempo(mido.MidiFile(midi_path)))
    result = build_bpm_fixed_midi(midi_path, bpm, estimate['first_beat_seconds'])
    report_path = report_path or source_stats_path_for(midi_path)
    report = format_auto_first_beat_stats(midi_path, estimate, result)
    update_report(report_path, report)
    print(report)
    print('Updating auto first beat report: {}'.format(report_path))
    return result
