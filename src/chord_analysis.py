"""Chord segments for a MIDI file: boundaries at chord onsets and bass changes, names from Tonal.js.

A segment starts where at least three pitches start together or where a new bass note
starts; every note sounding inside the segment counts, weighted by how long it sounds.
"""

import argparse
import json
import math
import re
import subprocess
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

import mido

from clean_midi_rules import tick_seconds
from node_utils import node_executable


ONSET_WINDOW_SECONDS = 0.05
CHORD_ONSET_PITCHES = 3
# A new lowest note below this pitch (C4) counts as a bass change.
BASS_LIMIT = 60
ONSET_WEIGHT = 1.5
# Pitch classes weaker than this share of the strongest are passing notes.
MIN_WEIGHT_RATIO = 0.2
MAX_PITCH_CLASSES = 5
# Fewest pitch classes tried when the full set has no chord name.
MIN_PITCH_CLASSES = 3
CANDIDATE_COUNT = 3
# Chord qualities from most to least preferred; Tonal lists exotic readings such as m#5 for plain inversions.
QUALITY_ORDER = ('', 'm', '7', 'maj7', 'm7', 'sus4', 'sus2', '6', 'm6', 'add9', 'madd9', '9', 'maj9', 'm9',
                 '7sus4', 'dim', 'm7b5', 'dim7', 'aug', '5')
# Shorter segments are arpeggio or passing figures and join the previous chord.
MIN_SEGMENT_BEATS = 0.5
SLASH_PENALTY = 5
DROPPED_PITCH_PENALTY = 4
CHORD_NAME = re.compile(r'([A-G][#b]?)(.*?)(?:/([A-G][#b]?))?')

SHARP_NAMES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')
FLAT_NAMES = ('C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B')
FLAT_KEYS = {'F', 'Bb', 'Eb', 'Ab', 'Db', 'Gb', 'Cb', 'Dm', 'Gm', 'Cm', 'Fm', 'Bbm', 'Ebm', 'Abm'}


@dataclass(frozen=True)
class Note:
    start_tick: int
    end_tick: int
    start: float
    end: float
    pitch: int


@dataclass(frozen=True)
class ChordSegment:
    start_tick: int
    end_tick: int
    start: float
    end: float
    name: str
    candidates: tuple
    bass: str
    pitch_classes: tuple


def read_notes(midi_file, seconds):
    notes = []
    key_signatures = []
    for track in midi_file.tracks:
        active = defaultdict(list)
        tick = 0
        for message in track:
            tick += message.time
            if message.type == 'note_on' and message.velocity > 0:
                active[message.channel, message.note].append(tick)
            elif message.type in ('note_on', 'note_off'):
                starts = active[message.channel, message.note]
                if starts:
                    start = starts.pop()
                    if tick > start:
                        notes.append(Note(start, tick, seconds(start), seconds(tick), message.note))
            elif message.type == 'key_signature':
                key_signatures.append((tick, message.key))
    notes.sort(key=lambda note: (note.start_tick, note.pitch))
    key_signatures.sort()
    return notes, key_signatures


def onset_groups(notes):
    """Notes starting within the onset window of the group's first note."""
    groups = []
    for note in notes:
        if groups and note.start - groups[-1][0].start <= ONSET_WINDOW_SECONDS:
            groups[-1].append(note)
        else:
            groups.append([note])
    return groups


def boundaries(groups):
    """Groups that start a segment: a chord onset, or a new bass pitch class."""
    starts = []
    bass_class = None
    for group in groups:
        lowest = min(note.pitch for note in group)
        chord_onset = len({note.pitch for note in group}) >= CHORD_ONSET_PITCHES
        bass_change = lowest < BASS_LIMIT and lowest % 12 != bass_class
        if chord_onset or bass_change or not starts:
            starts.append(group)
        if lowest < BASS_LIMIT:
            bass_class = lowest % 12
    return starts


def segment_profile(notes, group, start, end):
    """Weighted pitch classes and the bass pitch of one segment."""
    weights = defaultdict(float)
    bass_notes = list(group)
    length = end - start
    for note in notes:
        overlap = min(note.end, end) - max(note.start, start)
        if overlap <= 0:
            continue
        weight = overlap * (ONSET_WEIGHT if note.start >= start - ONSET_WINDOW_SECONDS else 1.0)
        weights[note.pitch % 12] += weight
        if overlap >= length / 2:
            bass_notes.append(note)
    bass = min(note.pitch for note in bass_notes)
    return weights, bass


def pitch_class_sets(weights, bass):
    """Strongest pitch classes, bass first, from the full set down to MIN_PITCH_CLASSES."""
    strongest = max(weights.values())
    ranked = [pc for pc, weight in sorted(weights.items(), key=lambda item: -item[1])
              if pc != bass % 12 and weight >= strongest * MIN_WEIGHT_RATIO]
    ranked = ranked[:MAX_PITCH_CLASSES - 1]
    sets = []
    for count in range(len(ranked), min(MIN_PITCH_CLASSES - 1, len(ranked)) - 1, -1):
        sets.append([bass % 12] + sorted(ranked[:count], key=lambda pc: (pc - bass) % 12))
    return sets or [[bass % 12]]


def note_names_for_key(key):
    return FLAT_NAMES if key in FLAT_KEYS else SHARP_NAMES


def detect_chords(note_groups):
    script_path = Path(__file__).with_name('detect_chords_tonal.js')
    completed = subprocess.run(
        [node_executable(), str(script_path)],
        input=json.dumps({'maxCandidates': CANDIDATE_COUNT, 'noteGroups': note_groups}),
        text=True, encoding='utf-8', capture_output=True, check=True,
    )
    return json.loads(completed.stdout)


def normalize_chord_name(name):
    """Write Tonal's major-based qualities without the M: BMadd9 -> Badd9."""
    root, quality, bass = CHORD_NAME.fullmatch(name).groups()
    if quality.startswith('M') and not quality.startswith('Maj'):
        quality = quality[1:]
    return root + quality + ('/' + bass if bass else '')


def chord_score(name, dropped):
    root, quality, bass = CHORD_NAME.fullmatch(name).groups()
    rank = QUALITY_ORDER.index(quality) if quality in QUALITY_ORDER else 2 * len(QUALITY_ORDER)
    return rank + (SLASH_PENALTY if bass else 0) + DROPPED_PITCH_PENALTY * dropped


def choose_chord(results):
    """Best names over the pitch class sets, largest set first; returns (candidates, set index)."""
    scored = {}
    for dropped, names in enumerate(results):
        for name in map(normalize_chord_name, names):
            if name not in scored or chord_score(name, dropped) < scored[name][0]:
                scored[name] = (chord_score(name, dropped), dropped)
    ranked = sorted(scored, key=lambda name: scored[name][0])
    return tuple(ranked[:CANDIDATE_COUNT]), (scored[ranked[0]][1] if ranked else 0)


def merge_into_previous(segments, joins):
    merged = []
    for segment in segments:
        if merged and joins(merged[-1], segment):
            merged[-1] = replace(merged[-1], end_tick=segment.end_tick, end=segment.end)
        elif segment.name or merged:
            merged.append(segment)
    return merged


def merge_segments(segments, ticks_per_beat):
    """Join repeats of a chord first, so a short chord start is not absorbed before its continuation."""
    def same_chord(previous, segment):
        return (not segment.name or segment.name == previous.name
                or set(segment.pitch_classes) <= set(previous.pitch_classes))

    def short(_previous, segment):
        return segment.end_tick - segment.start_tick < ticks_per_beat * MIN_SEGMENT_BEATS

    merged = merge_into_previous(segments, same_chord)
    merged = merge_into_previous(merged, short)
    return merge_into_previous(merged, lambda previous, segment: segment.name == previous.name)


def analyze_chords(midi_path):
    """Return merged ChordSegments of a MIDI file, in time order."""
    midi_file = mido.MidiFile(midi_path)
    seconds = tick_seconds(midi_file)
    notes, key_signatures = read_notes(midi_file, seconds)
    if not notes:
        return []
    starts = boundaries(onset_groups(notes))
    song_end = max(note.end for note in notes)
    song_end_tick = max(note.end_tick for note in notes)
    key_ticks = [tick for tick, _ in key_signatures]

    raw = []
    queries = []
    for index, group in enumerate(starts):
        start = group[0].start
        end = starts[index + 1][0].start if index + 1 < len(starts) else song_end
        end_tick = starts[index + 1][0].start_tick if index + 1 < len(starts) else song_end_tick
        if end <= start:
            continue
        weights, bass = segment_profile(notes, group, start, end)
        key_index = bisect_right(key_ticks, group[0].start_tick) - 1
        names = note_names_for_key(key_signatures[key_index][1] if key_index >= 0 else 'C')
        sets = pitch_class_sets(weights, bass)
        named_sets = [tuple(names[pc] for pc in pitch_classes) for pitch_classes in sets]
        raw.append((group[0].start_tick, end_tick, start, end, names[bass % 12], named_sets, len(queries)))
        queries.extend(list(named) for named in named_sets)

    results = detect_chords(queries)
    segments = []
    for start_tick, end_tick, start, end, bass, named_sets, first in raw:
        candidates, chosen = choose_chord(results[first:first + len(named_sets)])
        segments.append(ChordSegment(start_tick, end_tick, start, end, candidates[0] if candidates else '',
                                     candidates, bass, named_sets[chosen]))
    return merge_segments(segments, midi_file.ticks_per_beat)


def bar_beat(tick, ticks_per_beat, beats_per_bar=4):
    beats = math.floor(tick / ticks_per_beat * 100 + 1e-6) / 100
    return int(beats // beats_per_bar) + 1, beats % beats_per_bar + 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='Print the chord segments of a MIDI file.')
    parser.add_argument('input_midi')
    args = parser.parse_args(argv)
    ticks_per_beat = mido.MidiFile(args.input_midi).ticks_per_beat
    for segment in analyze_chords(args.input_midi):
        bar, beat = bar_beat(segment.start_tick, ticks_per_beat)
        print('{:>4} {:5.2f}  {:<12} {:>6.2f}s  bass {:<3} {:<22} {}'.format(
            bar, beat, segment.name, segment.end - segment.start, segment.bass,
            ' '.join(segment.pitch_classes), ', '.join(segment.candidates[1:])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
