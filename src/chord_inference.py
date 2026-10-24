# Copyright 2024 The Magenta Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Modified for Piano MIDI Cleanup from note_seq/chord_inference.py
# (https://github.com/magenta/note-seq): works on plain note tuples and tick
# frames instead of NoteSequence protos, adds an optional bass observation,
# lets the chord change probability vary per frame, and returns frame
# indices instead of text annotations.

"""Key and chord inference over pitch class frames with a hidden Markov model."""

import itertools

import numpy as np


NO_CHORD = 'N.C.'

# Pitch classes in a key (rooted at zero).
KEY_PITCHES = (0, 2, 4, 5, 7, 9, 11)

# Pitch classes in each chord kind (rooted at zero).
CHORD_KIND_PITCHES = {
    '': (0, 4, 7),
    'm': (0, 3, 7),
    '+': (0, 4, 8),
    'dim': (0, 3, 6),
    '7': (0, 4, 7, 10),
    'maj7': (0, 4, 7, 11),
    'm7': (0, 3, 7, 10),
    'm7b5': (0, 3, 6, 10),
}

# All usable chords, including no-chord.
CHORDS = [NO_CHORD] + list(itertools.product(range(12), CHORD_KIND_PITCHES))

# All key-chord pairs.
KEY_CHORDS = list(itertools.product(range(12), CHORDS))


def chord_pitch_classes(chord):
    root, kind = chord
    return [(root + offset) % 12 for offset in CHORD_KIND_PITCHES[kind]]


def frame_pitch_vectors(notes, boundaries):
    """Unit pitch class vectors per frame, weighted by how long each note sounds in the frame.

    notes are (start, end, pitch, weight) in any time unit; boundaries are the frame edges in
    that unit, from the first frame's start to the last frame's end.
    """
    vectors = np.zeros([len(boundaries) - 1, 12])
    edges = np.asarray(boundaries, dtype=float)
    for start, end, pitch, weight in notes:
        first = max(0, np.searchsorted(edges, start, side='right') - 1)
        last = min(len(vectors), np.searchsorted(edges, end, side='left'))
        for frame in range(first, last):
            overlap = min(end, edges[frame + 1]) - max(start, edges[frame])
            if overlap > 0:
                vectors[frame, pitch % 12] += overlap * weight
    norms = np.linalg.norm(vectors, axis=1)
    vectors[norms > 0] /= norms[norms > 0, np.newaxis]
    return vectors


def _key_chord_distribution(chord_pitch_out_of_key_prob):
    """Probability distribution over chords for each key."""
    in_key = np.zeros([12, len(CHORDS)], dtype=np.int32)
    out_of_key = np.zeros([12, len(CHORDS)], dtype=np.int32)
    for key in range(12):
        key_pitches = {(key + offset) % 12 for offset in KEY_PITCHES}
        for index, chord in enumerate(CHORDS[1:]):
            pitches = set(chord_pitch_classes(chord))
            in_key[key, index + 1] = len(pitches & key_pitches)
            out_of_key[key, index + 1] = len(pitches - key_pitches)
    matrix = ((1 - chord_pitch_out_of_key_prob) ** in_key
              * chord_pitch_out_of_key_prob ** out_of_key)
    matrix /= matrix.sum(axis=1)[:, np.newaxis]
    return matrix


def _key_chord_transitions(key_chord_distribution, key_change_prob, chord_change_prob):
    """Transition probabilities between key-chord pairs."""
    chord_count = len(CHORDS)
    keys = np.repeat(np.arange(12), chord_count)
    chords = np.tile(np.arange(chord_count), 12)
    same_key = keys[:, np.newaxis] == keys[np.newaxis, :]
    same_chord = same_key & (chords[:, np.newaxis] == chords[np.newaxis, :])
    target = key_chord_distribution[keys, chords][np.newaxis, :]
    # A known change redistributes the mass of the previous chord over the others.
    previous = key_chord_distribution[keys[np.newaxis, :], chords[:, np.newaxis]]
    matrix = np.where(same_key, 1 - key_change_prob, key_change_prob / 11 * target)
    changed = chord_change_prob * (target + previous / (chord_count - 1))
    return matrix * np.where(same_chord, 1 - chord_change_prob, np.where(same_key, changed, 1.0))


def _chord_pitch_vectors():
    """Unit vectors over pitch classes for all chords; no-chord is all zeros."""
    vectors = np.zeros([len(CHORDS), 12])
    for index, chord in enumerate(CHORDS[1:]):
        vectors[index + 1, chord_pitch_classes(chord)] = 1
    vectors[1:] /= np.linalg.norm(vectors[1:], axis=1)[:, np.newaxis]
    return vectors


def _bass_log_likelihood(bass_classes, root_bonus, tone_bonus):
    """Per frame and chord: root_bonus when the frame's bass is the chord root, tone_bonus for another chord tone."""
    loglik = np.zeros([len(bass_classes), len(CHORDS)])
    for index, chord in enumerate(CHORDS[1:]):
        tones = chord_pitch_classes(chord)
        for frame, bass in enumerate(bass_classes):
            if bass == tones[0]:
                loglik[frame, index + 1] = root_bonus
            elif bass in tones:
                loglik[frame, index + 1] = tone_bonus
    return loglik


def infer_key_chords(pitch_vectors, change_probs, bass_classes=None, key_change_prob=0.001,
                     chord_pitch_out_of_key_prob=0.01, chord_note_concentration=100.0,
                     bass_root_bonus=0.0, bass_tone_bonus=0.0):
    """Most likely (key, chord) per frame by the Viterbi algorithm.

    change_probs gives, for every frame after the first, the probability that the chord
    changes at the start of that frame; frames sharing a value share a transition matrix.
    bass_classes gives each frame's bass pitch class, or None, for the bass bonuses.
    """
    frame_count = len(pitch_vectors)
    if frame_count == 0:
        return []
    chord_count = len(CHORDS)
    distribution = _key_chord_distribution(chord_pitch_out_of_key_prob)
    key_chord_loglik = np.log(distribution)
    frame_loglik = chord_note_concentration * pitch_vectors @ _chord_pitch_vectors().T
    if bass_classes is not None:
        frame_loglik += _bass_log_likelihood(bass_classes, bass_root_bonus, bass_tone_bonus)
    transitions = {}

    loglik = (-np.log(12) + key_chord_loglik + frame_loglik[0][np.newaxis, :]).reshape(-1).astype(np.float32)
    frame_loglik = frame_loglik.astype(np.float32)
    paths = np.zeros([frame_count, len(KEY_CHORDS)], dtype=np.int32)
    for frame in range(1, frame_count):
        probability = change_probs[frame - 1]
        if probability not in transitions:
            # float32 halves the time of the per-frame max over all state pairs.
            transitions[probability] = np.log(_key_chord_transitions(
                distribution, key_change_prob, probability)).astype(np.float32)
        scores = loglik[:, np.newaxis] + transitions[probability]
        paths[frame] = scores.argmax(axis=0)
        loglik = scores[paths[frame], np.arange(len(KEY_CHORDS))] + np.tile(frame_loglik[frame], 12)

    path = [int(np.argmax(loglik))]
    for frame in range(frame_count - 1, 0, -1):
        path.append(int(paths[frame, path[-1]]))
    return [(index // chord_count, CHORDS[index % chord_count]) for index in reversed(path)]
