"""Read-only per-measure quality review for one player at a piano grand staff.

Three independent dimensions are reported and never merged into a single verdict:

* playability: onset-group key counts, hand spans, fast double stops and hand shifts
* naturalness: local pattern breaks such as a missing octave, a gapped four-note chord,
  a single shifted chord note, or a sudden melodic doubling
* notation: MusicXML only; extra voices, broken beams, tie chains and short rests

The module never writes to the MIDI or MusicXML it reads. Results are plain dicts so the
caller can write JSON, Markdown or its own report. A high risk is a reason to review, not
proof that a note is wrong.
"""

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ElementTree
from collections import defaultdict

import mido

from hand_split_channels import CHANNELS, collect_notes, group_notes, hand_fits
from meter import bar_beats
from midi_stats import note_name


MAX_NOTES_PER_HAND = 4
MAX_HAND_SPAN = 12
FAST_ONSET_SECONDS = 0.4
HAND_SHIFT_SECONDS = 0.35
OUTER_SHIFT_SEMITONES = 12
SUSTAINED_OVERLAP_MIN = 2
REPEATED_CHORD_WINDOW_BEATS = 1.5
OCTAVE_RUN_WINDOW_SECONDS = 0.45
GAPPED_CHORD_MIN_MIDDLE = 4
SHORT_REST_TYPES = ('16th', '32nd', '64th')
BEAMED_TYPES = ('eighth', '16th', '32nd')
DIMENSIONS = ('playability', 'naturalness', 'notation')

HAND_LABELS = {
    CHANNELS['right']: 'R',
    CHANNELS['second_right']: 'R2',
    CHANNELS['second_left']: 'L2',
    CHANNELS['left']: 'L',
}


def _local(tag):
    return tag.split('}')[-1]


def _child(element, name):
    return element.find('{*}' + name)


def _text(element, name, default=None):
    child = _child(element, name)
    return default if child is None or child.text is None else child.text


def _pitch_from_name(name):
    steps = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
    accidental = name.count('#') - name.count('b')
    digits = ''.join(character for character in name[1:] if character.isdigit() or character == '-')
    return (int(digits) + 1) * 12 + steps.get(name[0], 0) + accidental


def _midi_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(65536), b''):
            digest.update(block)
    return digest.hexdigest()


def _hand_label(channel):
    return HAND_LABELS.get(channel, '?')


def _single_note_reference(note, ticks_per_beat):
    reference = {
        'track': note['track_index'],
        'channel': note['start_message'].channel,
        'hand': _hand_label(note['start_message'].channel),
        'pitch': note['note'],
        'name': note_name(note['note']),
        'velocity': note['velocity'],
        'start_tick': note['start_ticks'],
        'end_tick': note['end_ticks'],
        'start_seconds': round(note['start_seconds'], 6),
        'length_beats': round((note['end_ticks'] - note['start_ticks']) / ticks_per_beat, 6),
    }
    return reference


def _issue(dimension, code, weight, reason, suggested_review, beat=None, time_seconds=None,
           notes=None, staff=None, voice=None, extra=None):
    if weight >= 45:
        severity = 'high'
    elif weight >= 25:
        severity = 'medium'
    elif weight > 0:
        severity = 'low'
    else:
        severity = 'info'
    issue = {
        'dimension': dimension,
        'code': code,
        'severity': severity,
        'weight': weight,
        'beat': None if beat is None else round(beat, 3),
        'time_seconds': None if time_seconds is None else round(time_seconds, 4),
        'staff': staff,
        'voice': voice,
        'reason': reason,
        'suggested_review': suggested_review,
        'notes': notes or [],
    }
    if extra:
        issue.update(extra)
    return issue


def _risk_level(score):
    if score >= 45:
        return 'high'
    if score >= 25:
        return 'medium'
    if score > 0:
        return 'low'
    return 'none'


def _best_split(pitches):
    """Smallest possible widest-hand span, or None when no split fits both hands."""
    best = None
    for cut in range(len(pitches) + 1):
        low, high = pitches[:cut], pitches[cut:]
        if not (hand_fits(low, MAX_NOTES_PER_HAND, MAX_HAND_SPAN)
                and hand_fits(high, MAX_NOTES_PER_HAND, MAX_HAND_SPAN)):
            continue
        low_span = low[-1] - low[0] if len(low) > 1 else 0
        high_span = high[-1] - high[0] if len(high) > 1 else 0
        widest = max(low_span, high_span)
        if best is None or widest < best[0]:
            best = (widest, cut)
    return best


def _octave_pairs(pitches):
    values = set(pitches)
    return sorted((pitch, pitch + 12) for pitch in values if pitch + 12 in values)


def _beat_in_bar(note, ticks_per_beat, beats_per_bar):
    return (note['start_ticks'] / ticks_per_beat) % beats_per_bar + 1


def _measure_number(note, ticks_per_beat, beats_per_bar):
    return int(note['start_ticks'] / ticks_per_beat // beats_per_bar) + 1


def _tempo_changes(midi_file):
    changes = [(0, 500000)]
    for track in midi_file.tracks:
        tick = 0
        for message in track:
            tick += message.time
            if message.type == 'set_tempo':
                changes.append((tick, message.tempo))
    changes.sort()
    return changes


def _note_end_seconds(note, midi_file, tempo_changes):
    return _seconds_at(note['end_ticks'], midi_file.ticks_per_beat, tempo_changes)


def _seconds_at(ticks, ticks_per_beat, tempo_changes):
    seconds = 0.0
    previous_tick, previous_tempo = tempo_changes[0]
    for tick, tempo in tempo_changes[1:]:
        if ticks <= tick:
            break
        seconds += mido.tick2second(tick - previous_tick, ticks_per_beat, previous_tempo)
        previous_tick, previous_tempo = tick, tempo
    seconds += mido.tick2second(max(0, ticks - previous_tick), ticks_per_beat, previous_tempo)
    return seconds


def _group_reference(group, ticks_per_beat):
    return [_single_note_reference(note, ticks_per_beat) for note in sorted(group, key=lambda item: item['note'])]


def _empty_measure(measure):
    return {
        'measure': measure,
        'display_measure': measure,
        'onsets': 0,
        'notes': 0,
        'no_onsets': True,
        'scores': {dimension: None for dimension in DIMENSIONS},
        'risk': 'none',
        'issues': [],
    }


def _merge_issue(measure, issue):
    measure['issues'].append(issue)


def _add_midi_issues(measure, group, ticks_per_beat, beats_per_bar):
    pitches = sorted(note['note'] for note in group)
    beat = _beat_in_bar(group[0], ticks_per_beat, beats_per_bar)
    seconds = group[0]['start_seconds']
    references = _group_reference(group, ticks_per_beat)
    count = len(pitches)
    best = _best_split(pitches)

    if best is None:
        _merge_issue(measure, _issue(
            'playability', 'unplayable_onset_group', 65,
            '{} notes starting together cannot split into two hands of at most {} notes and {} semitones.'
            .format(count, MAX_NOTES_PER_HAND, MAX_HAND_SPAN),
            '演奏困難', beat, seconds, references))
    else:
        widest, cut = best
        if count >= 7:
            _merge_issue(measure, _issue(
                'playability', 'dense_chord', 15,
                '{} notes start together; the best split is {} + {} with widest hand span {} semitones.'
                .format(count, cut, count - cut, widest),
                '演奏困難', beat, seconds, references))
        elif count >= 5 and widest >= 10:
            _merge_issue(measure, _issue(
                'playability', 'wide_hand_span', 10,
                '{} notes in one onset need a {} semitone reach even after the best two-hand split.'
                .format(count, widest),
                '演奏困難', beat, seconds, references))

    if count == 4:
        gaps = [pitches[index + 1] - pitches[index] for index in range(3)]
        if (gaps[0] <= 4 and gaps[2] <= 4 and gaps[1] >= GAPPED_CHORD_MIN_MIDDLE and gaps[1] <= 8
                and gaps[1] > gaps[0] and gaps[1] > gaps[2] and gaps[1] - max(gaps[0], gaps[2]) >= 1):
            _merge_issue(measure, _issue(
                'naturalness', 'unusual_chord_spacing', 45,
                'Four-note chord {} forms two close pairs with a wider middle gap: intervals {}, {}, {}. '
                'Compare the neighbouring chords and hand position before deciding it is wrong.'.format(
                    ', '.join(note_name(pitch) for pitch in pitches), gaps[0], gaps[1], gaps[2]),
                '疑似多餘音', beat, seconds, references))


def _playability_metrics(groups):
    metrics = {'max_onset_notes': 0, 'max_held_notes': 0, 'max_sustained_overlap': 0}
    events = []
    for group in groups:
        metrics['max_onset_notes'] = max(metrics['max_onset_notes'], len(group))
        for note in group:
            events.append((note['start_seconds'], 1, note))
            events.append((note['start_seconds'] + max(0.0, _length_seconds(note)), -1, note))
    held = 0
    for _, delta, note in sorted(events, key=lambda item: (item[0], item[1])):
        held += delta
        metrics['max_held_notes'] = max(metrics['max_held_notes'], held)
    return metrics


def _length_seconds(note):
    return note.get('length_seconds', 0.0)


def analyze_midi(midi_path, bar_offset=0):
    """Analyze the MIDI part only. MusicXML notation is not available here."""
    midi_file = mido.MidiFile(midi_path)
    ticks_per_beat = midi_file.ticks_per_beat
    beats_per_bar = bar_beats(midi_file)
    tempo_changes = _tempo_changes(midi_file)
    notes = collect_notes(midi_file)
    for note in notes:
        note['length_seconds'] = max(0.0, _note_end_seconds(note, midi_file, tempo_changes) - note['start_seconds'])
    groups = group_notes(notes)

    measures = {}
    groups_by_measure = defaultdict(list)
    for group in groups:
        measure_number = _measure_number(group[0], ticks_per_beat, beats_per_bar)
        groups_by_measure[measure_number].append(group)
        measure = measures.setdefault(measure_number, _empty_measure(measure_number))
        measure['onsets'] += 1
        measure['notes'] += len(group)
        measure['no_onsets'] = False
        _add_midi_issues(measure, group, ticks_per_beat, beats_per_bar)

    for measure_number, measure in measures.items():
        metrics = _playability_metrics(groups_by_measure[measure_number])
        measure['metrics'] = metrics
        if metrics['max_held_notes'] - metrics['max_onset_notes'] >= SUSTAINED_OVERLAP_MIN:
            measure['issues'].append(_issue(
                'playability', 'sustained_overlap', 0,
                'Held notes overlap across onsets ({} held, {} in one onset); this is likely pedal or note '
                'length, not a chord that must be pressed together.'.format(
                    metrics['max_held_notes'], metrics['max_onset_notes']),
                '僅記譜問題'))
        _score_measure(measure)

    _detect_octave_run_breaks(groups, measures, ticks_per_beat, beats_per_bar)
    _detect_fast_double_stops(groups, measures, ticks_per_beat, beats_per_bar)
    _detect_hand_shifts(groups, measures, ticks_per_beat, beats_per_bar)
    _detect_melodic_doubling(groups, measures, ticks_per_beat, beats_per_bar)
    _detect_repeated_chord_shifts(groups, measures, ticks_per_beat, beats_per_bar)
    _detect_semitone_dyads(groups, measures, ticks_per_beat, beats_per_bar)
    _detect_octave_run_singles(groups, measures, ticks_per_beat, beats_per_bar)
    _detect_octave_thickening(groups, measures, ticks_per_beat, beats_per_bar)
    _detect_rapid_chord_changes(groups, measures, ticks_per_beat, beats_per_bar)
    _detect_fast_bursts(groups, measures, ticks_per_beat, beats_per_bar)
    _detect_isolated_wide_leaps(groups, measures, ticks_per_beat, beats_per_bar)

    for measure in measures.values():
        measure['display_measure'] = measure['measure'] + bar_offset
        _score_measure(measure)
    return {
        'ticks_per_beat': ticks_per_beat,
        'beats_per_bar': beats_per_bar,
        'bar_offset': bar_offset,
        'measures': measures,
    }


def _detect_octave_run_breaks(groups, measures, ticks_per_beat, beats_per_bar):
    for index, group in enumerate(groups):
        if len(group) != 1 or index == 0 or index + 1 >= len(groups):
            continue
        previous, following = groups[index - 1], groups[index + 1]
        previous_pairs = _octave_pairs([note['note'] for note in previous])
        following_pairs = _octave_pairs([note['note'] for note in following])
        if not previous_pairs or not following_pairs:
            continue
        pitch = group[0]['note']
        if not any(abs(upper - pitch) <= 2 for _, upper in previous_pairs):
            continue
        if not any(abs(upper - pitch) <= 2 for _, upper in following_pairs):
            continue
        before_gap = group[0]['start_seconds'] - previous[0]['start_seconds']
        after_gap = following[0]['start_seconds'] - group[0]['start_seconds']
        if not (0 < before_gap <= OCTAVE_RUN_WINDOW_SECONDS and 0 < after_gap <= OCTAVE_RUN_WINDOW_SECONDS):
            continue
        if abs(before_gap - after_gap) > 0.35 * max(before_gap, after_gap):
            continue
        measure_number = _measure_number(group[0], ticks_per_beat, beats_per_bar)
        measure = measures.setdefault(measure_number, _empty_measure(measure_number))
        partner = pitch - 12 if pitch - 12 >= 0 else pitch + 12
        _merge_issue(measure, _issue(
            'naturalness', 'suspected_missing_octave_note', 50,
            'Single note {} sits between octave patterns {} and {} at a regular interval; the octave partner {} '
            'may be missing. Do not add it without checking the source.'.format(
                note_name(pitch),
                ', '.join(note_name(item['note']) for item in previous),
                ', '.join(note_name(item['note']) for item in following),
                note_name(partner)),
            '疑似漏音', _beat_in_bar(group[0], ticks_per_beat, beats_per_bar), group[0]['start_seconds'],
            _group_reference(group, ticks_per_beat),
            extra={'missing_partner': {'pitch': partner, 'name': note_name(partner)}}))
        _score_measure(measure)


def _detect_fast_double_stops(groups, measures, ticks_per_beat, beats_per_bar):
    run = []

    def flush():
        if len(run) < 3:
            return
        first_group = run[0][0]
        first_note = first_group[0]
        measure_number = _measure_number(first_note, ticks_per_beat, beats_per_bar)
        measure = measures.setdefault(measure_number, _empty_measure(measure_number))
        interval = run[0][1]
        _merge_issue(measure, _issue(
            'playability', 'fast_parallel_double_stops', 35,
            '{} consecutive {} double stops follow within {} seconds each; check whether this passage is '
            'comfortable at the real tempo.'.format(len(run), 'octave' if interval == 12 else 'fifth',
                                                     FAST_ONSET_SECONDS),
            '演奏困難', _beat_in_bar(first_note, ticks_per_beat, beats_per_bar), first_note['start_seconds'],
            _group_reference(first_group, ticks_per_beat),
            extra={'run_length': len(run), 'interval_semitones': interval}))
        _score_measure(measure)

    for group in groups:
        pitches = [note['note'] for note in group]
        interval = pitches[1] - pitches[0] if len(pitches) == 2 else None
        fast = interval in (7, 12) and (not run or group[0]['start_seconds'] - run[-1][0][0]['start_seconds']
                                         <= FAST_ONSET_SECONDS)
        same = bool(run) and interval == run[-1][1]
        if fast and same:
            run.append((group, interval))
        elif fast:
            flush()
            run = [(group, interval)]
        else:
            flush()
            run = []
    flush()


def _detect_hand_shifts(groups, measures, ticks_per_beat, beats_per_bar):
    previous = {}
    for group in groups:
        by_channel = defaultdict(list)
        for note in group:
            by_channel[note['start_message'].channel].append(note['note'])
        current = {}
        for channel, pitches in by_channel.items():
            current[channel] = (min(pitches), max(pitches))
        for channel, (low, high) in current.items():
            if channel not in previous:
                continue
            previous_low, previous_high, previous_seconds = previous[channel]
            gap = group[0]['start_seconds'] - previous_seconds
            if not (0 < gap <= HAND_SHIFT_SECONDS):
                continue
            move = max(abs(low - previous_low), abs(high - previous_high))
            if move < OUTER_SHIFT_SEMITONES:
                continue
            measure_number = _measure_number(group[0], ticks_per_beat, beats_per_bar)
            measure = measures.setdefault(measure_number, _empty_measure(measure_number))
            _merge_issue(measure, _issue(
                'playability', 'fast_hand_shift', 20,
                'Hand {} moves {} semitones within {:.2f} seconds; check the hand assignment and tempo.'.format(
                    _hand_label(channel), move, gap),
                '演奏困難', _beat_in_bar(group[0], ticks_per_beat, beats_per_bar),
                group[0]['start_seconds'], _group_reference(group, ticks_per_beat),
                extra={'semitones': move, 'seconds': round(gap, 4)}))
            _score_measure(measure)
        for channel, (low, high) in current.items():
            previous[channel] = (low, high, group[0]['start_seconds'])


def _detect_melodic_doubling(groups, measures, ticks_per_beat, beats_per_bar):
    for index in range(1, len(groups) - 1):
        previous, group, following = groups[index - 1], groups[index], groups[index + 1]
        if len(previous) != 1 or len(group) != 2 or len(following) != 1:
            continue
        if abs(group[1]['note'] - group[0]['note']) in (7, 12):
            continue
        gap_before = group[0]['start_seconds'] - previous[0]['start_seconds']
        gap_after = following[0]['start_seconds'] - group[0]['start_seconds']
        if not (0 < gap_before <= FAST_ONSET_SECONDS and 0 < gap_after <= FAST_ONSET_SECONDS):
            continue
        measure_number = _measure_number(group[0], ticks_per_beat, beats_per_bar)
        measure = measures.setdefault(measure_number, _empty_measure(measure_number))
        _merge_issue(measure, _issue(
            'naturalness', 'melodic_doubling', 15,
            'A single melodic line becomes a two-note chord for one onset and returns to one note; check whether '
            'the extra note is part of the music or a transcription artefact.',
            '疑似多餘音', _beat_in_bar(group[0], ticks_per_beat, beats_per_bar), group[0]['start_seconds'],
            _group_reference(group, ticks_per_beat)))
        _score_measure(measure)


def _detect_repeated_chord_shifts(groups, measures, ticks_per_beat, beats_per_bar):
    for index in range(len(groups) - 1):
        first, second = groups[index], groups[index + 1]
        if len(first) < 3 or len(first) != len(second):
            continue
        if second[0]['start_ticks'] - first[0]['start_ticks'] > REPEATED_CHORD_WINDOW_BEATS * ticks_per_beat:
            continue
        first_pitches = sorted(note['note'] for note in first)
        second_pitches = sorted(note['note'] for note in second)
        differences = [later - earlier for earlier, later in zip(first_pitches, second_pitches)]
        changed = [difference for difference in differences if difference]
        if len(changed) != 1 or abs(changed[0]) != 1:
            continue
        returns = False
        if index + 2 < len(groups):
            third = groups[index + 2]
            if (len(third) == len(first)
                    and third[0]['start_ticks'] - second[0]['start_ticks'] <= REPEATED_CHORD_WINDOW_BEATS * ticks_per_beat
                    and sorted(note['note'] for note in third) == first_pitches):
                returns = True
        measure_number = _measure_number(second[0], ticks_per_beat, beats_per_bar)
        measure = measures.setdefault(measure_number, _empty_measure(measure_number))
        _merge_issue(measure, _issue(
            'naturalness', 'single_note_semitone_shift', 40 if returns else 30,
            'A repeated {} note chord moves only one constituent by {} semitone{}; the pattern {} {}. Check '
            'whether that note is a pitch-recognition error.'.format(
                len(first), abs(changed[0]), '' if abs(changed[0]) == 1 else 's',
                'returns to the first chord' if returns else 'does not return',
                'afterwards' if returns else ''),
            '疑似音高辨識錯誤', _beat_in_bar(second[0], ticks_per_beat, beats_per_bar),
            second[0]['start_seconds'], _group_reference(second, ticks_per_beat),
            extra={'offset_semitones': changed[0], 'returns': returns}))
        _score_measure(measure)


def _detect_semitone_dyads(groups, measures, ticks_per_beat, beats_per_bar):
    for group in groups:
        pitches = sorted(note['note'] for note in group)
        if len(pitches) > 4:
            continue
        pair = next(((pitches[index], pitches[index + 1]) for index in range(len(pitches) - 1)
                     if pitches[index + 1] - pitches[index] <= 2), None)
        if pair is None:
            continue
        measure_number = _measure_number(group[0], ticks_per_beat, beats_per_bar)
        measure = measures.setdefault(measure_number, _empty_measure(measure_number))
        selected = [note for note in group if note['note'] in pair]
        _merge_issue(measure, _issue(
            'naturalness', 'semitone_dyad_candidate', 20,
            'A close-interval dyad {} starts together; keep it if the harmony needs it, otherwise check whether '
            'one pitch was recognised a semitone off.'.format(' / '.join(note_name(pitch) for pitch in pair)),
            '疑似音高辨識錯誤', _beat_in_bar(group[0], ticks_per_beat, beats_per_bar),
            group[0]['start_seconds'], _group_reference(selected, ticks_per_beat),
            extra={'interval_semitones': 1}))
        _score_measure(measure)


def _detect_octave_run_singles(groups, measures, ticks_per_beat, beats_per_bar):
    for index, group in enumerate(groups):
        if len(group) != 1 or index == 0 or index + 1 >= len(groups):
            continue
        before, after = groups[index - 1], groups[index + 1]
        if after[0]['start_seconds'] - group[0]['start_seconds'] > OCTAVE_RUN_WINDOW_SECONDS:
            continue
        pitches = [note['note'] for note in before + after]
        pairs = _octave_pairs(pitches)
        if not pairs:
            continue
        pitch = group[0]['note']
        if not any(abs(pitch - value) <= 5 for pair in pairs for value in pair):
            continue
        nearby = [entry for entry in groups
                  if abs(entry[0]['start_seconds'] - group[0]['start_seconds']) <= 1.0
                  and _octave_pairs([note['note'] for note in entry])]
        if len(nearby) < 2:
            continue
        measure_number = _measure_number(group[0], ticks_per_beat, beats_per_bar)
        measure = measures.setdefault(measure_number, _empty_measure(measure_number))
        if any(issue['code'] == 'suspected_missing_octave_note' and issue['beat'] ==
               round(_beat_in_bar(group[0], ticks_per_beat, beats_per_bar), 3) for issue in measure['issues']):
            continue
        _merge_issue(measure, _issue(
            'naturalness', 'octave_run_single_note', 40,
            'Single note {} appears beside an octave run; check whether the other octave note is missing or '
            'this single note is a stray note.'.format(note_name(pitch)),
            '疑似漏音', _beat_in_bar(group[0], ticks_per_beat, beats_per_bar), group[0]['start_seconds'],
            _group_reference(group, ticks_per_beat)))
        _score_measure(measure)


def _detect_octave_thickening(groups, measures, ticks_per_beat, beats_per_bar):
    for index, group in enumerate(groups):
        pitches = [note['note'] for note in group]
        if len(pitches) < 3 or not _octave_pairs(pitches):
            continue
        if index == 0:
            continue
        previous = groups[index - 1]
        previous_pitches = [note['note'] for note in previous]
        if not _octave_pairs(previous_pitches):
            continue
        if group[0]['start_seconds'] - previous[0]['start_seconds'] > OCTAVE_RUN_WINDOW_SECONDS:
            continue
        measure_number = _measure_number(group[0], ticks_per_beat, beats_per_bar)
        measure = measures.setdefault(measure_number, _empty_measure(measure_number))
        _merge_issue(measure, _issue(
            'naturalness', 'octave_run_thickened', 30,
            'An octave pattern gains an extra note, becoming {}; check whether the added note is part of '
            'the chord or a stray note.'.format(', '.join(note_name(pitch) for pitch in sorted(pitches))),
            '疑似多餘音', _beat_in_bar(group[0], ticks_per_beat, beats_per_bar),
            group[0]['start_seconds'], _group_reference(group, ticks_per_beat)))
        _score_measure(measure)


def _detect_rapid_chord_changes(groups, measures, ticks_per_beat, beats_per_bar):
    by_measure = defaultdict(list)
    for group in groups:
        if len(group) >= 3:
            by_measure[_measure_number(group[0], ticks_per_beat, beats_per_bar)].append(group)
    for measure_number, chords_groups in by_measure.items():
        if len(chords_groups) < 4:
            continue
        chords = {frozenset(note['note'] for note in group) for group in chords_groups}
        if len(chords) < 3:
            continue
        span_ticks = chords_groups[-1][0]['start_ticks'] - chords_groups[0][0]['start_ticks']
        if span_ticks > 4 * ticks_per_beat:
            continue
        measure = measures.setdefault(measure_number, _empty_measure(measure_number))
        first = chords_groups[0][0]
        _merge_issue(measure, _issue(
            'playability', 'rapid_chord_changes', 40,
            '{} different chords of three or more notes change within one bar; check the hand movement and '
            'whether the passage is comfortable at the real tempo.'.format(len(chords)),
            '演奏困難', _beat_in_bar(first, ticks_per_beat, beats_per_bar), first['start_seconds'],
            _group_reference(chords_groups[0], ticks_per_beat),
            extra={'chord_count': len(chords), 'group_count': len(chords_groups)}))
        _score_measure(measure)


def _detect_fast_bursts(groups, measures, ticks_per_beat, beats_per_bar):
    run = []

    def flush():
        if len(run) < 3:
            return
        pitches = [group[0]['note'] for group in run]
        if len(set(pitches)) < 2:
            return
        first = run[0][0]
        measure_number = _measure_number(first, ticks_per_beat, beats_per_bar)
        measure = measures.setdefault(measure_number, _empty_measure(measure_number))
        _merge_issue(measure, _issue(
            'naturalness', 'fast_short_burst_candidate', 30,
            '{} distinct single notes follow within about a sixteenth-note burst; compare the audio before '
            'deciding whether they are real or hallucinated notes.'.format(len(run)),
            '疑似多餘音', _beat_in_bar(first, ticks_per_beat, beats_per_bar), first['start_seconds'],
            _group_reference([note for group in run for note in group], ticks_per_beat), extra={'note_count': len(run)}))
        _score_measure(measure)

    for group in groups:
        if len(group) != 1:
            flush()
            run = []
            continue
        fast = not run or group[0]['start_seconds'] - run[-1][0]['start_seconds'] <= 0.2
        if fast:
            run.append(group)
        else:
            flush()
            run = [group]
    flush()


def _detect_isolated_wide_leaps(groups, measures, ticks_per_beat, beats_per_bar):
    for index, group in enumerate(groups):
        pitches = sorted(note['note'] for note in group)
        if len(pitches) != 2:
            continue
        interval = pitches[1] - pitches[0]
        if 3 <= interval <= 11:
            nearby = [note['note'] for offset in (-1, 1)
                      for other in (index + offset,) if 0 <= other < len(groups)
                      for note in groups[other]]
            if nearby and max(abs(pitch - value) for pitch in pitches for value in nearby) >= 7:
                measure_number = _measure_number(group[0], ticks_per_beat, beats_per_bar)
                measure = measures.setdefault(measure_number, _empty_measure(measure_number))
                _merge_issue(measure, _issue(
                    'naturalness', 'suspicious_two_note_arpeggio', 25,
                    'Two-note figure {} is isolated from the nearby pattern; check the hand assignment and '
                    'whether one note belongs to the other hand.'.format(
                        ' / '.join(note_name(pitch) for pitch in pitches)),
                    '演奏困難', _beat_in_bar(group[0], ticks_per_beat, beats_per_bar),
                    group[0]['start_seconds'], _group_reference(group, ticks_per_beat),
                    extra={'tags': ['suspicious_two_note_arpeggio']}))
                _score_measure(measure)
            continue
        if interval < 19:
            continue
        nearby = []
        for offset in (-2, -1, 1, 2):
            other = index + offset
            if 0 <= other < len(groups):
                nearby.extend(note['note'] for note in groups[other])
        if not nearby:
            continue
        outlier = max(pitches, key=lambda pitch: min(abs(pitch - value) for value in nearby))
        distance = min(abs(outlier - value) for value in nearby)
        if distance < 12:
            continue
        measure_number = _measure_number(group[0], ticks_per_beat, beats_per_bar)
        measure = measures.setdefault(measure_number, _empty_measure(measure_number))
        _merge_issue(measure, _issue(
            'naturalness', 'octave_pattern_outlier', 45,
            'Two-note figure {} has one note {} semitones away from the nearby pattern; check whether this '
            'is a wrong pitch or a hand-assignment artefact.'.format(
                ' / '.join(note_name(pitch) for pitch in pitches), distance),
            '疑似音高辨識錯誤', _beat_in_bar(group[0], ticks_per_beat, beats_per_bar),
            group[0]['start_seconds'], _group_reference(group, ticks_per_beat),
            extra={'tags': ['isolated_wide_leap', 'suspicious_two_note_arpeggio'],
                   'outlier_pitch': outlier, 'distance_semitones': distance}))
        _score_measure(measure)


def _score_measure(measure):
    weights = {dimension: [] for dimension in DIMENSIONS}
    for issue in measure['issues']:
        if issue['weight'] > 0:
            weights[issue['dimension']].append(issue['weight'])
    for dimension in DIMENSIONS:
        values = weights[dimension]
        if values:
            score = max(values) + 5 * (len(values) - 1)
            measure['scores'][dimension] = min(100, score)
        elif dimension == 'notation' and not measure.get('available', {}).get('notation', True):
            measure['scores'][dimension] = None
        else:
            measure['scores'][dimension] = 0
    available = [measure['scores'][dimension] for dimension in DIMENSIONS
                 if measure['scores'][dimension] is not None and measure.get('available', {}).get(dimension, True)]
    measure['risk_score'] = max(available) if available else 0
    measure['risk'] = _risk_level(measure['risk_score'])


def parse_musicxml(path):
    """Read the notation structure needed by the cleanliness checks. One part, all staves."""
    tree = ElementTree.parse(path)
    root = tree.getroot()
    parts = [element for element in root.iter() if _local(element.tag) == 'part']
    if not parts:
        raise ValueError('MusicXML has no part to read: {}'.format(path))
    part = parts[0]
    divisions = 1
    measures = []
    for index, measure_element in enumerate((element for element in part if _local(element.tag) == 'measure'), 1):
        try:
            number = int(measure_element.get('number', index))
        except (TypeError, ValueError):
            number = index
        attributes = _child(measure_element, 'attributes')
        if attributes is not None:
            division_text = _text(attributes, 'divisions')
            if division_text:
                divisions = int(division_text)
        position = 0
        last_onset = 0
        notes = []
        rests = []
        for element in measure_element:
            name = _local(element.tag)
            if name == 'backup':
                position -= int(_text(element, 'duration') or 0)
                continue
            if name == 'forward':
                position += int(_text(element, 'duration') or 0)
                continue
            if name != 'note':
                continue
            duration = int(_text(element, 'duration') or 0)
            chord = _child(element, 'chord') is not None
            if not chord:
                last_onset = position
                position += duration
            onset = last_onset
            staff = _text(element, 'staff') or '1'
            voice = _text(element, 'voice') or '1'
            note_type = _text(element, 'type') or ''
            if _child(element, 'rest') is not None:
                rests.append({'onset': onset, 'duration': duration, 'staff': staff, 'voice': voice,
                              'type': note_type})
                continue
            pitch_element = _child(element, 'pitch')
            if pitch_element is None:
                continue
            step = _text(pitch_element, 'step') or ''
            octave = _text(pitch_element, 'octave') or ''
            alter = int(_text(pitch_element, 'alter') or 0)
            pitch_name = '{}{}{}'.format(step, '#' * alter if alter > 0 else 'b' * -alter, octave)
            beams = [_text(beam, '') for beam in ()]
            beam_values = []
            for beam in element:
                if _local(beam.tag) == 'beam' and beam.text:
                    beam_values.append((beam.get('number', '1'), beam.text))
            tie_types = [tie.get('type', '') for tie in element if _local(tie.tag) == 'tie']
            for notation in element:
                if _local(notation.tag) == 'notations':
                    for tied in notation:
                        if _local(tied.tag) == 'tied':
                            tie_types.append(tied.get('type', ''))
            notes.append({
                'onset': onset,
                'duration': duration,
                'staff': staff,
                'voice': voice,
                'type': note_type,
                'pitch_name': pitch_name,
                'grace': _child(element, 'grace') is not None,
                'beams': beam_values,
                'tie': tie_types,
            })
        measures.append({'number': number, 'divisions': divisions, 'notes': notes, 'rests': rests})
    return measures


def analyze_musicxml_structure(xml_measures):
    """Per-measure notation issues, keyed by MusicXML measure number."""
    voice_stats = defaultdict(lambda: {'measures': 0, 'notes': 0})
    total_notes = 0
    for measure in xml_measures:
        seen = set()
        for note in measure['notes']:
            key = (note['staff'], note['voice'])
            voice_stats[key]['notes'] += 1
            total_notes += 1
            seen.add(key)
        for key in seen:
            voice_stats[key]['measures'] += 1
    rare_measure_limit = max(2, int(len(xml_measures) * 0.03))
    results = {}
    for measure in xml_measures:
        number = measure['number']
        divisions = measure['divisions']
        result = {'issues': [], 'voices': {}, 'notes': len(measure['notes'])}
        by_staff_voice = defaultdict(lambda: defaultdict(list))
        for note in measure['notes']:
            by_staff_voice[note['staff']][note['voice']].append(note)
        for staff, voices in by_staff_voice.items():
            result['voices'][staff] = sorted(voices)
            if '1' in voices and len(voices['1']) >= 2:
                dominant = '1'
            else:
                dominant = max(voices, key=lambda voice: (len(voices[voice]),
                                                          max(note['onset'] + note['duration'] for note in voices[voice])))
            for voice, notes in voices.items():
                if voice == dominant:
                    continue
                stats = voice_stats[(staff, voice)]
                rare = stats['measures'] <= rare_measure_limit or stats['notes'] <= max(3, total_notes * 0.01)
                coverage = (max(note['onset'] + note['duration'] for note in notes)
                            - min(note['onset'] for note in notes))
                if rare and len(notes) <= 3:
                    result['issues'].append(_issue(
                        'notation', 'isolated_extra_voice', 55,
                        'Staff {} voice {} is a rare voice with {} isolated note(s) while voice {} carries '
                        'the passage; check whether it is needed or merely a stray note.'.format(
                            staff, voice, len(notes), dominant),
                        '僅記譜問題', staff=staff, voice=voice,
                        extra={'pitches': [note['pitch_name'] for note in notes],
                               'beats': [round(note['onset'] / divisions + 1, 3) for note in notes]}))
                elif rare and len(notes) <= 6 and coverage < 0.4 * 4 * divisions:
                    result['issues'].append(_issue(
                        'notation', 'fragmented_extra_voice', 25,
                        'Staff {} voice {} is short and fragmented next to the main voice {}.'.format(
                            staff, voice, dominant),
                        '僅記譜問題', staff=staff, voice=voice,
                        extra={'pitches': [note['pitch_name'] for note in notes]}))
                elif len(notes) <= 2 and coverage < 0.5 * 4 * divisions:
                    result['issues'].append(_issue(
                        'notation', 'sparse_extra_voice', 30,
                        'Staff {} voice {} has only {} short note(s) beside voice {}; check whether an '
                        'isolated note forced an extra voice.'.format(staff, voice, len(notes), dominant),
                        '僅記譜問題', staff=staff, voice=voice,
                        extra={'pitches': [note['pitch_name'] for note in notes],
                               'beats': [round(note['onset'] / divisions + 1, 3) for note in notes]}))
                elif len(notes) >= 3:
                    established = (stats['measures'] >= max(3, len(xml_measures) * 0.2)
                                   and coverage >= 0.6 * 4 * divisions)
                    if not established:
                        result['issues'].append(_issue(
                            'notation', 'unnecessary_extra_voice', 45,
                            'Staff {} has {} notes in voice {} beside the main voice {}; check whether this '
                            'extra voice is needed or makes the passage hard to read.'.format(
                                staff, len(notes), voice, dominant),
                            '僅記譜問題', staff=staff, voice=voice,
                            extra={'pitches': [note['pitch_name'] for note in notes],
                                   'beats': [round(note['onset'] / divisions + 1, 3) for note in notes]}))
        _xml_beam_breaks(measure, result)
        _xml_tie_chains(measure, result)
        _xml_short_rests(measure, result)
        _xml_gapped_chords(measure, result)
        _xml_grace_notes(measure, result)
        _xml_extreme_staff_register(measure, result)
        results[number] = result
    return results


def _xml_beam_breaks(measure, result):
    divisions = measure['divisions']
    by_staff_voice = defaultdict(list)
    for note in measure['notes']:
        if note['type'] in BEAMED_TYPES:
            by_staff_voice[(note['staff'], note['voice'])].append(note)
    for (staff, voice), notes in by_staff_voice.items():
        notes.sort(key=lambda note: note['onset'])
        for first, second in zip(notes, notes[1:]):
            if first['onset'] // divisions != second['onset'] // divisions:
                continue
            first_ends = any(level == '1' and kind == 'end' for level, kind in first['beams'])
            second_begins = any(level == '1' and kind == 'begin' for level, kind in second['beams'])
            if first_ends and second_begins:
                result['issues'].append(_issue(
                    'notation', 'unnecessary_beam_break', 25,
                    'Beamed {} notes at beats {:.2f} and {:.2f} are split although they share a beat.'.format(
                        first['type'], first['onset'] / divisions + 1, second['onset'] / divisions + 1),
                    '僅記譜問題', beat=second['onset'] / divisions + 1, staff=staff, voice=voice,
                    extra={'pitches': [first['pitch_name'], second['pitch_name']]}))


def _xml_tie_chains(measure, result):
    divisions = measure['divisions']
    by_staff_voice_pitch = defaultdict(list)
    for note in measure['notes']:
        if 'start' in note['tie'] or 'stop' in note['tie']:
            by_staff_voice_pitch[(note['staff'], note['voice'], note['pitch_name'])].append(note)
    for (staff, voice, pitch), notes in by_staff_voice_pitch.items():
        notes.sort(key=lambda note: note['onset'])
        chain = []
        for note in notes:
            if chain and 'stop' in note['tie'] and note['onset'] == chain[-1]['onset'] + chain[-1]['duration']:
                chain.append(note)
            else:
                _emit_tie_chain(chain, staff, voice, pitch, divisions, result)
                chain = [note]
        _emit_tie_chain(chain, staff, voice, pitch, divisions, result)


def _emit_tie_chain(chain, staff, voice, pitch, divisions, result):
    if len(chain) < 3:
        return
    result['issues'].append(_issue(
        'notation', 'unnecessary_tie_chain', 30,
        '{} is split into {} tied segments inside one measure; check whether the tie chain is needed or '
        'merely hard to read.'.format(pitch, len(chain)),
        '僅記譜問題', beat=chain[0]['onset'] / divisions + 1, staff=staff, voice=voice,
        extra={'segments': len(chain), 'beats': [round(note['onset'] / divisions + 1, 3) for note in chain]}))


def _xml_short_rests(measure, result):
    divisions = measure['divisions']
    by_staff_voice = defaultdict(list)
    for note in measure['notes']:
        by_staff_voice[(note['staff'], note['voice'])].append(('note', note))
    for rest in measure['rests']:
        by_staff_voice[(rest['staff'], rest['voice'])].append(('rest', rest))
    for (staff, voice), events in by_staff_voice.items():
        events.sort(key=lambda item: item[1]['onset'])
        for index, (kind, event) in enumerate(events):
            if kind != 'rest' or event['type'] not in SHORT_REST_TYPES:
                continue
            before = any(previous_kind == 'note' for previous_kind, _ in events[:index])
            after = any(following_kind == 'note' for following_kind, _ in events[index + 1:])
            if before and after:
                result['issues'].append(_issue(
                    'notation', 'short_rest_fragmentation', 20,
                    'A {} rest inside the phrase fragments the rhythm.'.format(event['type']),
                    '僅記譜問題', beat=event['onset'] / divisions + 1, staff=staff, voice=voice))


def _xml_grace_notes(measure, result):
    divisions = measure['divisions']
    for note in measure['notes']:
        if not note.get('grace'):
            continue
        result['issues'].append(_issue(
            'naturalness', 'grace_note_candidate', 30,
            'Grace note {} before the main note; check the onset spacing of the chord and the audio before '
            'treating it as a real ornament.'.format(note['pitch_name']),
            '疑似多餘音', beat=note['onset'] / divisions + 1, staff=note['staff'], voice=note['voice'],
            extra={'pitches': [note['pitch_name']]}))


def _xml_extreme_staff_register(measure, result):
    divisions = measure['divisions']
    high = [note for note in measure['notes']
            if note['staff'] == '2' and _pitch_from_name(note['pitch_name']) >= 69]
    low = [note for note in measure['notes']
           if note['staff'] == '1' and _pitch_from_name(note['pitch_name']) <= 36]
    if high:
        result['issues'].append(_issue(
            'playability', 'high_note_on_lower_staff',
            45 if max(_pitch_from_name(note['pitch_name']) for note in high) >= 72 else 35,
            'The lower staff reaches {}; check whether this note belongs to the right hand or is a wrong '
            'pitch.'.format(', '.join(note['pitch_name'] for note in high)),
            '演奏困難', beat=high[0]['onset'] / divisions + 1, staff='2', voice=high[0]['voice'],
            extra={'pitches': [note['pitch_name'] for note in high],
                   'beats': [round(note['onset'] / divisions + 1, 3) for note in high]}))
    if low:
        result['issues'].append(_issue(
            'playability', 'low_note_on_upper_staff', 35,
            'The upper staff reaches {}; check the hand assignment.'.format(
                ', '.join(note['pitch_name'] for note in low)),
            '演奏困難', beat=low[0]['onset'] / divisions + 1, staff='1', voice=low[0]['voice'],
            extra={'pitches': [note['pitch_name'] for note in low]}))


def _xml_gapped_chords(measure, result):
    divisions = measure['divisions']
    by_onset = defaultdict(list)
    for note in measure['notes']:
        by_onset[(note['onset'], note['staff'])].append(note)
    for (onset, staff), notes in by_onset.items():
        by_pitch = {}
        for note in notes:
            pitch = _pitch_from_name(note['pitch_name'])
            by_pitch.setdefault(pitch, note)
        if len(by_pitch) != 4:
            continue
        paired = sorted(by_pitch.items(), key=lambda item: item[0])
        pitches = [pitch for pitch, _ in paired]
        gaps = [pitches[index + 1] - pitches[index] for index in range(3)]
        if (gaps[0] <= 4 and gaps[2] <= 4 and gaps[1] >= GAPPED_CHORD_MIN_MIDDLE and gaps[1] <= 8
                and gaps[1] > gaps[0] and gaps[1] > gaps[2] and gaps[1] - max(gaps[0], gaps[2]) >= 1):
            result['issues'].append(_issue(
                'naturalness', 'unusual_chord_spacing', 45,
                'Four-note chord {} forms two close pairs with a wider middle gap: intervals {}, {}, {}.'.format(
                    ', '.join(note_name(pitch) for pitch in pitches), gaps[0], gaps[1], gaps[2]),
                '疑似多餘音', beat=onset / divisions + 1, staff=staff,
                notes=[{'staff': note['staff'], 'voice': note['voice'], 'name': note['pitch_name'],
                        'pitch': pitch} for pitch, note in paired]))


def analyze(midi_path, musicxml_path=None, bar_offset=0):
    """Full read-only analysis; returns a JSON-ready dict."""
    midi = analyze_midi(midi_path, bar_offset=bar_offset)
    measures = midi['measures']
    dimensions = {'playability': True, 'naturalness': True, 'notation': bool(musicxml_path)}
    notation = None
    if musicxml_path:
        xml_measures = parse_musicxml(musicxml_path)
        notation = analyze_musicxml_structure(xml_measures)
        for xml_number, xml_result in notation.items():
            midi_number = xml_number - bar_offset
            measure = measures.setdefault(midi_number, _empty_measure(midi_number))
            measure['display_measure'] = xml_number
            measure.setdefault('available', {})['notation'] = True
            for issue in xml_result['issues']:
                _merge_issue(measure, issue)
            _score_measure(measure)

    for measure in measures.values():
        measure.setdefault('available', {})['playability'] = True
        measure['available'].setdefault('naturalness', True)
        measure['available']['notation'] = bool(musicxml_path)
        if not musicxml_path:
            measure['scores']['notation'] = None
        _score_measure(measure)

    ordered = [measures[number] for number in sorted(measures)]
    priority = sorted(ordered, key=lambda measure: (-measure['risk_score'], measure['display_measure']))
    priority = [measure for measure in priority if measure['risk_score'] > 0]
    return {
        'tool': {
            'name': 'piano-midi-cleanup measure quality review',
            'read_only': True,
            'profile': {
                'players': 1,
                'hands': 2,
                'max_notes_per_hand': MAX_NOTES_PER_HAND,
                'max_hand_span_semitones': MAX_HAND_SPAN,
            },
        },
        'inputs': {
            'midi': midi_path,
            'midi_sha256': _midi_sha256(midi_path),
            'musicxml': musicxml_path,
            'musicxml_sha256': _midi_sha256(musicxml_path) if musicxml_path else None,
            'ticks_per_beat': midi['ticks_per_beat'],
            'beats_per_bar': midi['beats_per_bar'],
            'bar_offset': bar_offset,
            'display_bar_rule': 'display_measure = MIDI measure + bar_offset; MusicXML uses display measures',
        },
        'dimensions': dimensions,
        'measure_count': len(ordered),
        'measures': ordered,
        'priority': priority,
    }


def write_json(result, path):
    with open(path, 'w', encoding='utf-8') as output:
        json.dump(result, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write('\n')


def _issue_lines(measure):
    lines = []
    for issue in measure['issues']:
        if issue['weight'] <= 0:
            continue
        location = []
        if issue['beat'] is not None:
            location.append('beat {:.2f}'.format(issue['beat']))
        if issue['staff'] is not None:
            location.append('staff {}'.format(issue['staff']))
        if issue['voice'] is not None:
            location.append('voice {}'.format(issue['voice']))
        if issue['notes']:
            location.append('notes ' + ', '.join(
                note.get('name') or note.get('pitch_name') or str(note.get('pitch'))
                for note in issue['notes']))
        lines.append('- {} [{}] {}: {}{}'.format(
            issue['suggested_review'], issue['severity'], issue['code'], issue['reason'],
            ' ({})'.format('; '.join(location)) if location else ''))
    return lines


def write_markdown(result, path, top=15):
    inputs = result['inputs']
    lines = [
        '# Per-measure quality review',
        '',
        'Read-only. Risk is a reason to review, not proof that a note is wrong.',
        '',
        '- MIDI: `{}`'.format(inputs['midi']),
        '- MusicXML: {}'.format('`{}`'.format(inputs['musicxml']) if inputs['musicxml'] else 'not supplied (notation not evaluated)'),
        '- Meter: {} quarter beats per bar; bar rule: {}'.format(
            inputs['beats_per_bar'], inputs['display_bar_rule']),
        '',
        '## Priority measures',
        '',
    ]
    for measure in result['priority'][:top]:
        lines.append('### Measure {} (MIDI {}) - risk {} ({})'.format(
            measure['display_measure'], measure['measure'], measure['risk'], measure['risk_score']))
        if measure['no_onsets']:
            lines.append('- No MIDI onsets in this measure; sustained notes may continue from earlier bars.')
        lines.extend(_issue_lines(measure))
        lines.append('')
    if not result['priority']:
        lines.append('No measure crossed the review threshold.')
        lines.append('')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Print a read-only per-measure quality review. Does not modify the MIDI or MusicXML.',
    )
    parser.add_argument('input_midi', help='Bar-aligned MIDI to review.')
    parser.add_argument('--musicxml', help='MusicXML of the same passage; enables notation checks.')
    parser.add_argument('--bar-offset', type=int, default=0,
                        help='display measure = MIDI measure + this offset (for example -2 with two lead bars).')
    parser.add_argument('--json', help='Write the structured result here.')
    parser.add_argument('--markdown', help='Write the human-readable report here.')
    parser.add_argument('--top', type=int, default=15, help='Measures in the Markdown priority list.')
    args = parser.parse_args(argv)
    result = analyze(args.input_midi, args.musicxml, args.bar_offset)
    if args.json:
        write_json(result, args.json)
        print('Writing quality JSON: {}'.format(args.json))
    if args.markdown:
        text = write_markdown(result, args.markdown, args.top)
        with open(args.markdown, 'w', encoding='utf-8') as output:
            output.write(text + '\n')
        print('Writing quality report: {}'.format(args.markdown))
    if not args.json and not args.markdown:
        sys.stdout.write(write_markdown(result, None, args.top) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
