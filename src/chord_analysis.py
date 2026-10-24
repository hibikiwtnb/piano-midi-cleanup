"""Chord segments for a MIDI file: an HMM over half-beat frames finds the chords, Tonal.js names them.

Every note counts in each frame it sounds in, held by the sustain pedal and weighted by how
long it sounds, so broken chords and arpeggios add up to their chord; the Viterbi path keeps
a chord until the notes clearly fit another. Chord changes are likelier where three or more
pitches start, or a note below everything still sounding. Each segment is then named by Tonal.js from its strongest pitch classes, keeping
the inferred root and writing an inversion over the bass.
"""

import argparse
import json
import math
import re
import subprocess
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import mido

from chord_inference import NO_CHORD, chord_pitch_classes, frame_pitch_vectors, infer_key_chords
from clean_midi_rules import tick_seconds
from node_utils import node_executable


FRAMES_PER_BEAT = 2
# The project assumes 4/4 after first beat alignment.
BEATS_PER_BAR = 4
# A first note this close before a bar line is an early downbeat, not a pickup.
PICKUP_TOLERANCE_BEATS = 0.25
# Frame vectors are unit length, so this sets how strongly one frame's notes outweigh the chord change cost.
CHORD_NOTE_CONCENTRATION = 10.0
# Bass notes count this much more in the frame vectors; the left hand carries the harmony more than the melody.
BASS_WEIGHT = 2
# Pedal-held tails fade, so they count less, and for at most PEDAL_TAIL_BEATS.
PEDAL_TAIL_WEIGHT = 0.5
PEDAL_TAIL_BEATS = 2
# Log-likelihood bonus when a frame's bass is the chord root, or another chord tone.
BASS_ROOT_BONUS = 3.0
BASS_TONE_BONUS = 1.0
# Chance of a chord change at a frame start; onsets of chords and bass notes make it likelier.
CHANGE_PROB = 0.02
ONSET_CHANGE_PROB = 0.3
ONSET_WINDOW_SECONDS = 0.05
CHORD_ONSET_PITCHES = 3
# Notes below this pitch (C4) are in the bass register.
BASS_LIMIT = 60
# The bass is the lowest pitch sounding at least this share of its segment.
BASS_MIN_SHARE = 1 / 8
# Pitch classes weaker than this share of the strongest are passing notes.
MIN_WEIGHT_RATIO = 0.2
MAX_PITCH_CLASSES = 5
# Fewest pitch classes tried when the full set has no chord name.
MIN_PITCH_CLASSES = 3
CANDIDATE_COUNT = 3
# Chord qualities from most to least preferred; Tonal lists exotic readings such as m#5 for plain inversions.
QUALITY_ORDER = ('', 'm', '7', 'maj7', 'm7', 'sus4', 'sus2', '6', 'm6', 'add9', 'madd9', '9', 'maj9', 'm9',
                 '7sus4', 'dim', 'm7b5', 'dim7', 'aug', '5')
SLASH_PENALTY = 5
DROPPED_PITCH_PENALTY = 4
CHORD_NAME = re.compile(r'([A-G][#b]*)(.*?)(?:/([A-G][#b]*))?')
KIND_NAMES = {'+': 'aug'}

SHARP_NAMES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')
FLAT_NAMES = ('C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B')
# Major keys written with flats: F, Bb, Eb, Ab, Db.
FLAT_KEYS = {5, 10, 3, 8, 1}
LETTER_PITCHES = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


@dataclass(frozen=True)
class Note:
    start_tick: int
    end_tick: int
    start: float
    pitch: int
    # Where the sound stops: the note end, or the pedal release while the pedal holds it.
    sound_end_tick: int


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


def pedal_intervals(midi_file):
    """Merged sustain pedal down intervals in ticks, over all channels."""
    events = []
    for track in midi_file.tracks:
        tick = 0
        for message in track:
            tick += message.time
            if message.type == 'control_change' and message.control == 64:
                events.append((tick, message.channel, message.value >= 64))
    down_channels = set()
    intervals = []
    for tick, channel, down in sorted(events):
        was_down = bool(down_channels)
        (down_channels.add if down else down_channels.discard)(channel)
        if down_channels and not was_down:
            intervals.append([tick, None])
        elif was_down and not down_channels:
            intervals[-1][1] = tick
    return [(start, end) for start, end in intervals if end is not None]


def read_notes(midi_file, seconds):
    pedals = pedal_intervals(midi_file)
    pedal_starts = [start for start, _ in pedals]

    def sound_end(tick):
        index = bisect_right(pedal_starts, tick) - 1
        return max(tick, pedals[index][1]) if index >= 0 else tick

    notes = []
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
                        notes.append(Note(start, tick, seconds(start), message.note, sound_end(tick)))
    notes.sort(key=lambda note: (note.start_tick, note.pitch))
    return notes


def onset_groups(notes):
    """Notes starting within the onset window of the group's first note."""
    groups = []
    for note in notes:
        if groups and note.start - groups[-1][0].start <= ONSET_WINDOW_SECONDS:
            groups[-1].append(note)
        else:
            groups.append([note])
    return groups


def change_probs(notes, frame_ticks, frame_count):
    """Chord change probability at the start of each frame after the first.

    A chord onset, or a bass note below every note still sounding, makes a change likelier;
    the upper notes of a broken chord sound above its held root and do not.
    """
    probs = [CHANGE_PROB] * (frame_count - 1)
    sounding = []
    for group in onset_groups(notes):
        start = group[0].start_tick
        sounding = [note for note in sounding if note.sound_end_tick > start]
        pitches = {note.pitch for note in group}
        lowest = min(pitches)
        new_bass = lowest < BASS_LIMIT and all(lowest < note.pitch for note in sounding)
        sounding.extend(group)
        if len(pitches) >= CHORD_ONSET_PITCHES or new_bass:
            # An onset just before a frame edge belongs to the next frame.
            frame = round(group[0].start_tick / frame_ticks)
            if 0 < frame < frame_count:
                probs[frame - 1] = ONSET_CHANGE_PROB
    return probs


def frame_notes(notes, ticks_per_beat):
    """(start, end, pitch, weight) for the frame vectors: the played note, then its pedal-held tail."""
    weighted = []
    for note in notes:
        weight = BASS_WEIGHT if note.pitch < BASS_LIMIT else 1
        weighted.append((note.start_tick, note.end_tick, note.pitch, weight))
        tail_end = min(note.sound_end_tick, note.end_tick + PEDAL_TAIL_BEATS * ticks_per_beat)
        if tail_end > note.end_tick:
            weighted.append((note.end_tick, tail_end, note.pitch, weight * PEDAL_TAIL_WEIGHT))
    return weighted


def frame_basses(notes, frame_ticks, frame_count):
    """Pitch class of the lowest bass-register note played in each frame, without pedal tails."""
    lowest = [None] * frame_count
    for note in notes:
        if note.pitch >= BASS_LIMIT:
            continue
        first = int(note.start_tick // frame_ticks)
        last = min(frame_count, math.ceil(note.end_tick / frame_ticks))
        for frame in range(first, last):
            if lowest[frame] is None or note.pitch < lowest[frame]:
                lowest[frame] = note.pitch
    return [None if pitch is None else pitch % 12 for pitch in lowest]


def segment_profile(notes, start_tick, end_tick):
    """Pitch class weights and the bass pitch of a tick range."""
    weights = defaultdict(float)
    pitch_spans = defaultdict(int)
    for note in notes:
        overlap = min(note.sound_end_tick, end_tick) - max(note.start_tick, start_tick)
        if overlap > 0:
            weights[note.pitch % 12] += overlap
            pitch_spans[note.pitch] += overlap
    length = end_tick - start_tick
    bass = min((pitch for pitch, span in pitch_spans.items() if span >= length * BASS_MIN_SHARE),
               default=min(pitch_spans, default=None))
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


def detect_chords(note_groups):
    script_path = Path(__file__).with_name('detect_chords_tonal.js')
    completed = subprocess.run(
        [node_executable(), str(script_path)],
        input=json.dumps({'maxCandidates': CANDIDATE_COUNT, 'noteGroups': note_groups}),
        text=True, encoding='utf-8', capture_output=True, check=True,
    )
    return json.loads(completed.stdout)


def pitch_class(note_name):
    """Pitch class of a note name, including Tonal spellings such as Fb or E##."""
    return (LETTER_PITCHES[note_name[0]] + note_name.count('#') - note_name.count('b')) % 12


def normalize_chord_name(name):
    """Write Tonal's major-based qualities without the M: BMadd9 -> Badd9."""
    root, quality, bass = CHORD_NAME.fullmatch(name).groups()
    if quality.startswith('M') and not quality.startswith('Maj'):
        quality = quality[1:]
    return root + quality + ('/' + bass if bass else '')


def chord_score(name, dropped):
    _root, quality, bass = CHORD_NAME.fullmatch(name).groups()
    rank = QUALITY_ORDER.index(quality) if quality in QUALITY_ORDER else 2 * len(QUALITY_ORDER)
    return rank + (SLASH_PENALTY if bass else 0) + DROPPED_PITCH_PENALTY * dropped


def name_chord(results, chord, names, bass):
    """Tonal names that keep the inferred root, best first; the inferred chord when none does.

    Returns (candidates, index of the pitch class set the first name came from).
    """
    root = chord[0]
    scored = {}
    for dropped, found in enumerate(results):
        for name in map(normalize_chord_name, found):
            if pitch_class(CHORD_NAME.fullmatch(name).group(1)) != root:
                continue
            score = chord_score(name, dropped)
            if name not in scored or score < scored[name][0]:
                scored[name] = (score, dropped)
    ranked = sorted(scored, key=lambda name: scored[name][0])
    if ranked:
        return tuple(ranked[:CANDIDATE_COUNT]), scored[ranked[0]][1]
    name = names[root] + KIND_NAMES.get(chord[1], chord[1])
    if bass % 12 != root:
        name += '/' + names[bass % 12]
    return (name,), None


def chord_runs(key_chords):
    """(first frame, end frame, key, chord) for each run of one chord."""
    runs = []
    for frame, (key, chord) in enumerate(key_chords):
        if runs and runs[-1][3] == chord:
            runs[-1][1] = frame + 1
        else:
            runs.append([frame, frame + 1, key, chord])
    return runs


def first_bar_tick(notes, ticks_per_beat):
    """The first bar line at or after the first note; notes before it are a pickup.

    First beat alignment puts the downbeat on a bar line, so the bar grid already tells
    the pickup apart from the first full bar.
    """
    bar_ticks = BEATS_PER_BAR * ticks_per_beat
    first = notes[0].start_tick
    nearest = round(first / bar_ticks) * bar_ticks
    if abs(first - nearest) <= PICKUP_TOLERANCE_BEATS * ticks_per_beat:
        return nearest
    return math.ceil(first / bar_ticks) * bar_ticks


def analyze_chords(midi_path):
    """Return ChordSegments of a MIDI file in time order; the pickup and no-chord stretches are left out."""
    midi_file = mido.MidiFile(midi_path)
    seconds = tick_seconds(midi_file)
    notes = read_notes(midi_file, seconds)
    if not notes:
        return []
    frame_ticks = midi_file.ticks_per_beat / FRAMES_PER_BEAT
    song_end_tick = max(note.sound_end_tick for note in notes)
    frame_count = math.ceil(song_end_tick / frame_ticks)
    boundaries = [frame * frame_ticks for frame in range(frame_count + 1)]
    # Chords start at the first full bar; the pickup still sounds into it through the pedal.
    start = round(first_bar_tick(notes, midi_file.ticks_per_beat) / frame_ticks)
    if start >= frame_count:
        return []
    key_chords = infer_key_chords(
        frame_pitch_vectors(frame_notes(notes, midi_file.ticks_per_beat), boundaries)[start:],
        change_probs(notes, frame_ticks, frame_count)[start:],
        frame_basses(notes, frame_ticks, frame_count)[start:],
        chord_note_concentration=CHORD_NOTE_CONCENTRATION,
        bass_root_bonus=BASS_ROOT_BONUS, bass_tone_bonus=BASS_TONE_BONUS)

    runs = []
    queries = []
    for first, end, key, chord in chord_runs(key_chords):
        first, end = first + start, end + start
        if chord == NO_CHORD:
            continue
        start_tick = round(first * frame_ticks)
        end_tick = min(round(end * frame_ticks), song_end_tick)
        weights, bass = segment_profile(notes, start_tick, end_tick)
        if bass is None:
            continue
        names = FLAT_NAMES if key in FLAT_KEYS else SHARP_NAMES
        sets = pitch_class_sets(weights, bass)
        named_sets = [tuple(names[pc] for pc in pitch_classes) for pitch_classes in sets]
        runs.append((start_tick, end_tick, chord, names, bass, named_sets, len(queries)))
        queries.extend(list(named) for named in named_sets)

    results = detect_chords(queries)
    segments = []
    for start_tick, end_tick, chord, names, bass, named_sets, first in runs:
        candidates, chosen = name_chord(results[first:first + len(named_sets)], chord, names, bass)
        tones = named_sets[chosen] if chosen is not None else tuple(names[pc] for pc in chord_pitch_classes(chord))
        segments.append(ChordSegment(start_tick, end_tick, seconds(start_tick), seconds(end_tick),
                                     candidates[0], candidates, names[bass % 12], tones))
    return segments


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
