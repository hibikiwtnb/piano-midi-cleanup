"""Infer the meter of a performance among the meters the pipeline supports.

The one-line audio-to-MIDI flow must not ask a listener to name a time signature. Bars, the
first beat, key signatures and the hand split all need one, so it is inferred from the
performance: the detected pulse, how that pulse subdivides, and how strongly accents repeat at
each candidate bar period.

This is an inference, not a reading of the notation. Flat accents, a displaced accent, a short
excerpt or several bars of one chord can fit more than one meter; then no meter is reported and
the caller keeps the MIDI's own signature instead of presenting a guess as a result.

Only the meters the pipeline supports are compared. A performance whose pulse subdivides in
threes is 6/8. Otherwise the bar is three or four quarter beats; music that is really 2/4 is read
as 4/4 and a 6/8 whose pulse was detected as a quarter (not a dotted quarter) is read as 3/4.
Both keep the correct bar length in quarter beats, so bars, the first beat, key signatures and
the hand split still count the same length of music.
"""
import cmath
import math

import mido

DEFAULT_MIDI_TEMPO = 500000
BASS_MAX_PITCH = 54
BASS_WEIGHT = 2.0
MIN_ONSETS = 24

# Implementation details; chapter 2 of docs/spec explains the signals and their limits.
COMPOUND_RATIO = 1.05
MIN_SUBDIVISION = 0.5
MIN_BAR_COHERENCE = 0.10
MIN_MARGIN = 0.04
HIGH_MARGIN = 0.20

SIMPLE_REASONS = {
    '4/4': 'accents repeat every four beats',
    '3/4': 'accents repeat every three beats',
}


def read_onsets(midi_path):
    """(seconds, weight, pitch) of every note start; loud and bass notes weigh more."""
    midi_file = mido.MidiFile(midi_path)
    seconds = 0.0
    onsets = []
    for message in midi_file:
        seconds += message.time
        if message.type == 'note_on' and message.velocity > 0:
            onsets.append((seconds, message.velocity, message.note))
    if not onsets:
        return []
    loudest = max(velocity for _, velocity, _ in onsets)
    return [(start, (velocity / loudest) ** 2 * (BASS_WEIGHT if pitch <= BASS_MAX_PITCH else 1.0), pitch)
            for start, velocity, pitch in onsets]


def accent_coherence(onsets, period):
    """Share of the accent weight whose timing repeats with this period (0 to 1).

    The first Fourier coefficient at the period, so no phase has to be chosen first: a bar line
    anywhere in the file counts, which matches how estimate_first_beat later finds it.
    """
    total = sum(weight for _, weight, _ in onsets)
    if not total or period <= 0:
        return 0.0
    phasor = sum(weight * cmath.exp(2j * math.pi * start / period) for start, weight, _ in onsets)
    return abs(phasor) / total


def _result(meter, confidence, evidence, reason):
    return {'meter': meter, 'confidence': confidence, 'evidence': evidence, 'reason': reason}


def detect_meter(midi_path, bpm):
    """Return the inferred meter with its evidence, or no meter when the performance is unclear.

    bpm is the detected pulse. In 6/8 that pulse is usually a dotted quarter; the caller turns it
    into the quarter-note tempo once 6/8 is chosen.
    """
    onsets = read_onsets(midi_path)
    evidence = {'notes': len(onsets), 'pulse_seconds': 60.0 / bpm if bpm > 0 else 0.0}
    if not math.isfinite(bpm) or bpm <= 0 or len(onsets) < MIN_ONSETS:
        return _result(None, 'undetermined', evidence, 'too few notes to compare bars')

    beat = 60.0 / bpm
    evidence['subdivision_3'] = accent_coherence(onsets, beat / 3)
    evidence['subdivision_2'] = accent_coherence(onsets, beat / 2)
    if evidence['subdivision_3'] > evidence['subdivision_2'] * COMPOUND_RATIO \
            and evidence['subdivision_3'] >= MIN_SUBDIVISION:
        # The pulse subdivides in threes, so it is a dotted-quarter beat and the 6/8 bar holds two.
        scores = {'6/8': accent_coherence(onsets, 2 * beat), '9/8': accent_coherence(onsets, 3 * beat)}
        evidence['bar_scores'] = scores
        if scores['9/8'] > scores['6/8'] + MIN_MARGIN:
            return _result(None, 'undetermined', evidence, 'a three-part pulse with a three-beat bar (9/8)')
        confidence = 'high' if evidence['subdivision_3'] >= 0.75 else 'medium'
        return _result((6, 8), confidence, evidence, 'a three-part pulse with accents every two beats')

    scores = {'4/4': accent_coherence(onsets, 4 * beat), '3/4': accent_coherence(onsets, 3 * beat)}
    evidence['bar_scores'] = scores
    best = max(scores, key=scores.get)
    runner_up = min(scores.values())
    margin = scores[best] - runner_up
    evidence['margin'] = margin
    if scores[best] < MIN_BAR_COHERENCE or margin < MIN_MARGIN:
        return _result(None, 'undetermined', evidence, 'no bar length stands out clearly')
    return _result((4, 4) if best == '4/4' else (3, 4),
                   'high' if margin >= HIGH_MARGIN else 'medium', evidence, SIMPLE_REASONS[best])


def format_detection(detection):
    """The outcome line and the evidence line of the BPMFix report."""
    evidence = detection.get('evidence', {})
    parts = []
    if 'subdivision_3' in evidence:
        parts.append('3 vs 2 per beat {:.2f} / {:.2f}'.format(evidence['subdivision_3'],
                                                             evidence['subdivision_2']))
    scores = evidence.get('bar_scores') or {}
    if scores:
        parts.append('accents per period ' + ', '.join('{} {:.2f}'.format(name, score)
                                                       for name, score in scores.items()))
    meter = detection.get('meter')
    if meter:
        outcome = '{}/{} (auto, {}, {} notes)'.format(*meter, detection['confidence'],
                                                      evidence.get('notes'))
    else:
        outcome = 'undetermined (auto, {}), bars use the MIDI\'s own'.format(detection['reason'])
    return outcome, '; '.join(parts)
