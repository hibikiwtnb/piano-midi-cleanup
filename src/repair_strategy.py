"""Read-only repair routing for the per-measure review.

Every issue code that measure_quality can emit has one strategy here: what it means, which
candidate actions to compare, what the program computes for each candidate, what the Agent must
judge, when the answer is "keep", and what must never be applied automatically. The program part
is deterministic: it builds the candidates for a flagged onset group and measures each one
(playability, leaps, pattern match, pitch-class support, velocity and timing of the touched note).
These numbers are evidence for the Agent, not a probability that a note is wrong. Nothing here
writes to the MIDI or MusicXML.
"""

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

import mido

import measure_quality
from hand_split_channels import collect_notes, group_notes
from midi_stats import note_name


CONTEXT_GROUPS = 3

# Candidate kinds. 'notation_only' changes the score file only; 'simplify' is a playable
# reduction, a musical choice that is never a correction.
KEEP, DELETE, OCTAVE, SEMITONE, ADD_OCTAVE, MOVE_HAND, NOTATION, SIMPLIFY = (
    'keep', 'delete', 'octave', 'semitone', 'add_octave', 'move_hand', 'notation_only', 'simplify')

_PITCH_EVIDENCE = 'pitch-class support in context, leap to neighbours, pattern match, velocity and onset offset of the note'
_NOTATION_VERIFY = 'Re-run the review with the edited MusicXML: the code disappears and the MIDI hash is unchanged.'
_PITCH_VERIFY = ('Re-run the review on the versioned copy: the code is gone or explained, no new '
                 'unplayable_onset_group appears nearby, and the change record lists event, old and new pitch.')

STRATEGIES = {
    'unplayable_onset_group': {
        'meaning': 'An onset group cannot be split into two hands of at most 4 notes and 12 semitones.',
        'causes': 'pedal-sustained notes grouped as one onset, an octave error on an outer note, a leaked '
                  'note from another instrument, or a genuine two-handed cluster written for one player.',
        'candidates': [KEEP, DELETE, OCTAVE, MOVE_HAND],
        'evidence': 'best split span after each candidate, ' + _PITCH_EVIDENCE,
        'agent': 'Which note is not part of the voicing: the one whose deletion or octave move makes the '
                 'group playable while every remaining pitch class is still supported by the bar. Prefer an '
                 'octave move when the pitch class is needed; prefer deletion when the pitch class is absent '
                 'from the context and the note is soft or late.',
        'keep_when': 'Every pitch class is structural and no single change restores playability; then it is '
                     'an arrangement question (simplify), not an error.',
        'never_auto': 'Deleting or moving a pitch.',
        'verify': _PITCH_VERIFY,
    },
    'dense_chord': {
        'meaning': 'Seven or more notes start together but still fit two hands.',
        'causes': 'full-texture climax chords (often real), doubled octaves from separation leakage.',
        'candidates': [KEEP, DELETE],
        'evidence': 'duplicate pitch classes, velocity of each note against the group median, onset offset',
        'agent': 'Only a note that duplicates nothing, is unsupported by the harmony and is clearly softer '
                 'or late is a deletion candidate. Doubled octaves alone are normal piano texture.',
        'keep_when': 'All pitch classes belong to the chord and the split is playable (default).',
        'never_auto': 'Thinning a chord.',
        'verify': _PITCH_VERIFY,
    },
    'wide_hand_span': {
        'meaning': 'Five or more notes need a reach of ten or more semitones in one hand after the best split.',
        'causes': 'an outer note in the wrong octave, a note better taken by the other hand, a real wide chord.',
        'candidates': [KEEP, MOVE_HAND, OCTAVE, DELETE],
        'evidence': 'best split span after each candidate, hand channel of each note, ' + _PITCH_EVIDENCE,
        'agent': 'First try re-assigning hands (sound unchanged). Change a pitch only when the outer note also '
                 'breaks the local register pattern.',
        'keep_when': 'A tenth is reachable or the chord is rolled; spans of 10-12 are hard, not wrong.',
        'never_auto': 'Pitch changes; hand re-assignment is a notation decision for the Agent.',
        'verify': _PITCH_VERIFY,
    },
    'unusual_chord_spacing': {
        'meaning': 'A four-note chord forms two close pairs with a wider middle gap.',
        'causes': 'an inner note recognised a step off, or a deliberate open voicing split between hands.',
        'candidates': [KEEP, DELETE, SEMITONE, OCTAVE],
        'evidence': _PITCH_EVIDENCE + ', pattern match with neighbouring chords of the same size',
        'agent': 'Compare with the neighbouring chords: if they use the same two-pair shape, keep. If one inner '
                 'note is the only pitch class absent from the context, test delete and the semitone change.',
        'keep_when': 'Neighbouring chords repeat the shape, or the pairs belong to different hands.',
        'never_auto': 'Any pitch change.',
        'verify': _PITCH_VERIFY,
    },
    'sustained_overlap': {
        'meaning': 'More notes are held than are struck together; pedal or note length, not a chord to press.',
        'causes': 'pedal-lengthened transcription note ends.',
        'candidates': [KEEP, NOTATION],
        'evidence': 'max held notes against max onset notes (in the issue text)',
        'agent': 'Nothing to decide musically. Shorten written durations in the score only if they hurt '
                 'readability.',
        'keep_when': 'Always keep the MIDI; the owner\'s note ends and pedal stay.',
        'never_auto': 'Shortening MIDI note ends.',
        'verify': _NOTATION_VERIFY,
    },
    'suspected_missing_octave_note': {
        'meaning': 'A single note sits between octave patterns at a regular interval.',
        'causes': 'the octave partner was not transcribed, or the line really thins out for one note.',
        'candidates': [KEEP, ADD_OCTAVE, DELETE],
        'evidence': 'pattern match of each candidate with the surrounding octave groups, leap, hand span after adding',
        'agent': 'Add the partner only when the surrounding run is strictly in octaves on both sides and the '
                 'added note is reachable; when audio exists, require a peak at the partner that is not just '
                 'the harmonic of the single note (an upper partner shares that frequency, so audio cannot '
                 'confirm it).',
        'keep_when': 'The phrase alternates single notes and octaves elsewhere, or audio shows no partner onset.',
        'never_auto': 'Adding notes.',
        'verify': _PITCH_VERIFY,
    },
    'fast_parallel_double_stops': {
        'meaning': 'Three or more octave or fifth double stops follow within 0.4 s each.',
        'causes': 'a real octave passage, or doubled transcription of one line.',
        'candidates': [KEEP, SIMPLIFY],
        'evidence': 'run length, interval, seconds between onsets (in the issue)',
        'agent': 'Octave runs at this speed are standard piano technique. Treat as a correction only if the '
                 'doubling appears nowhere else in the phrase and audio shows a single line.',
        'keep_when': 'Default: keep. Offer simplification only for an easier version the user asked for.',
        'never_auto': 'Removing one side of the double stops.',
        'verify': _PITCH_VERIFY,
    },
    'fast_hand_shift': {
        'meaning': 'One hand moves twelve or more semitones within 0.35 s.',
        'causes': 'a hand assignment error, an outer note in the wrong octave, or a real jump.',
        'candidates': [KEEP, MOVE_HAND, OCTAVE],
        'evidence': 'leap after each candidate, hand channels, ' + _PITCH_EVIDENCE,
        'agent': 'If the other hand is free and closer, re-assign. Change octave only if the note also breaks '
                 'the register of its own line.',
        'keep_when': 'The jump lands on a beat with a rest or staccato before it, or the line is a real leap.',
        'never_auto': 'Pitch changes.',
        'verify': _PITCH_VERIFY,
    },
    'melodic_doubling': {
        'meaning': 'A single-note line becomes a two-note chord for one onset and returns to single notes.',
        'causes': 'a harmonic or bleed note, or an intended accent dyad.',
        'candidates': [KEEP, DELETE],
        'evidence': 'velocity of each note against its neighbours, whether each pitch continues the line '
                    '(leap to previous and next note), pitch-class support',
        'agent': 'The melody note is the one that continues the line by step or small leap; the other is the '
                 'deletion candidate only if it is softer and its pitch class is not in the harmony.',
        'keep_when': 'The added note is a chord tone on a strong beat or repeats in the parallel phrase.',
        'never_auto': 'Deletion.',
        'verify': _PITCH_VERIFY,
    },
    'single_note_semitone_shift': {
        'meaning': 'A repeated chord moves only one constituent by a semitone (and maybe back).',
        'causes': 'a pitch-recognition error, or a chromatic neighbour or passing tone.',
        'candidates': [KEEP, SEMITONE],
        'evidence': 'offset and return flag (in the issue), pitch-class support of old and new pitch',
        'agent': 'A neighbour that returns on a weak beat with an accepted key-related pitch is normal voice '
                 'leading. Correct only when the shifted pitch class is foreign to both the key and the bar.',
        'keep_when': 'The shift is diatonic, prepared or resolves by step.',
        'never_auto': 'Pitch changes.',
        'verify': _PITCH_VERIFY,
    },
    'semitone_dyad_candidate': {
        'meaning': 'Two notes a semitone or tone apart start together.',
        'causes': 'a real cluster or added second, or one pitch recognised a step off.',
        'candidates': [KEEP, DELETE, SEMITONE],
        'evidence': 'pitch-class support of each pitch, velocity of each, pattern match',
        'agent': 'Keep seconds that belong to the chord (add9, sus, 7ths in close position). Test a change only '
                 'for the pitch whose class is missing from the surrounding bar.',
        'keep_when': 'Both pitch classes are supported, or the clash repeats in the parallel phrase.',
        'never_auto': 'Deleting or shifting either note.',
        'verify': _PITCH_VERIFY,
    },
    'octave_run_single_note': {
        'meaning': 'A single note appears beside an octave run.',
        'causes': 'a missing octave partner, or a stray note inside the run.',
        'candidates': [KEEP, ADD_OCTAVE, DELETE],
        'evidence': 'pattern match after adding each partner, leap, pitch-class support',
        'agent': 'Same test as suspected_missing_octave_note; also consider deletion when the single note is '
                 'off the run\'s pitch path.',
        'keep_when': 'The note is a melodic connector between runs or audio shows only one pitch.',
        'never_auto': 'Adding or deleting.',
        'verify': _PITCH_VERIFY,
    },
    'octave_run_thickened': {
        'meaning': 'An octave pattern gains an extra note.',
        'causes': 'a chord tone filling the octave (common), or a stray note.',
        'candidates': [KEEP, DELETE],
        'evidence': 'which notes form the octave pair, velocity and pitch-class support of the extra notes, '
                    'whether the same filled shape repeats',
        'agent': 'Delete the extra only if its pitch class is unsupported and the filled shape does not repeat.',
        'keep_when': 'The extra is a chord tone, or the filled octave recurs.',
        'never_auto': 'Deletion.',
        'verify': _PITCH_VERIFY,
    },
    'rapid_chord_changes': {
        'meaning': 'Three or more different chords of three or more notes change within one bar.',
        'causes': 'fast arpeggiated accompaniment transcribed as block chords, or real fast changes.',
        'candidates': [KEEP, SIMPLIFY, NOTATION],
        'evidence': 'chord count, onset spacing (in the issue), pitch-class sets of each chord',
        'agent': 'If the chords share one harmony, the passage is a figuration: keep pitches and consider a '
                 'cleaner rhythm in the score. Difficulty alone is not an error.',
        'keep_when': 'Default: keep.',
        'never_auto': 'Merging or thinning chords.',
        'verify': _PITCH_VERIFY,
    },
    'fast_short_burst_candidate': {
        'meaning': 'Three or more distinct single notes within about a sixteenth note.',
        'causes': 'a real run or ornament, or hallucinated notes from a transient.',
        'candidates': [KEEP, DELETE],
        'evidence': 'velocity of each note against the burst median, leap inside the burst, pitch-class support',
        'agent': 'A scalar or chord-tone path is real. A note that leaps out of the path, is soft and is '
                 'unsupported is the deletion candidate.',
        'keep_when': 'The notes form a scale, arpeggio or turn.',
        'never_auto': 'Deletion.',
        'verify': _PITCH_VERIFY,
    },
    'suspicious_two_note_arpeggio': {
        'meaning': 'A two-note figure is isolated (seven or more semitones) from the nearby pattern.',
        'causes': 'one note belongs to the other hand, or a stray note.',
        'candidates': [KEEP, MOVE_HAND, DELETE],
        'evidence': 'hand channels, leap of each note to neighbours, pitch-class support',
        'agent': 'Re-assign when one note continues the other hand\'s line; delete only an unsupported soft note.',
        'keep_when': 'Both notes continue some line.',
        'never_auto': 'Deletion.',
        'verify': _PITCH_VERIFY,
    },
    'octave_pattern_outlier': {
        'meaning': 'A wide two-note figure has one note twelve or more semitones from the nearby pattern.',
        'causes': 'an octave recognition error (most common), or a real register jump.',
        'candidates': [KEEP, OCTAVE, DELETE],
        'evidence': 'leap and pattern match after moving the outlier one or two octaves, velocity',
        'agent': 'Prefer the octave move that brings the outlier into its line while keeping its pitch class; '
                 'delete only when the pitch class itself is unsupported.',
        'keep_when': 'The register jump repeats in the parallel phrase or starts a new section.',
        'never_auto': 'Pitch changes.',
        'verify': _PITCH_VERIFY,
    },
    'grace_note_candidate': {
        'meaning': 'The score shows a grace note.',
        'causes': 'a slightly early chord note quantised as a grace, a leftover note from the previous onset, '
                  'or a real ornament.',
        'candidates': [KEEP, NOTATION],
        'evidence': 'whether the MIDI has a matching onset just before the main note (compare the score with '
                    'the note text of that beat)',
        'agent': 'If the MIDI has no separate early onset, the grace is a notation artefact: remove or merge it '
                 'in the score. If it has one and audio agrees, keep the ornament.',
        'keep_when': 'The MIDI has a distinct early onset of that pitch.',
        'never_auto': 'Editing the MIDI; the fix is in the score.',
        'verify': _NOTATION_VERIFY,
    },
    'isolated_extra_voice': {
        'meaning': 'A rare voice holds one to three isolated notes.',
        'causes': 'a duration overlap forced a second voice.',
        'candidates': [KEEP, NOTATION],
        'evidence': 'voice usage statistics (in the issue)',
        'agent': 'Merge into the main voice unless the note is held under a moving line (real polyphony).',
        'keep_when': 'A held note sounds under a moving line.',
        'never_auto': 'MIDI edits.',
        'verify': _NOTATION_VERIFY,
    },
    'fragmented_extra_voice': {
        'meaning': 'A short, fragmented extra voice beside the main voice.',
        'causes': 'overlapping durations in the transcription.',
        'candidates': [KEEP, NOTATION],
        'evidence': 'voice coverage (in the issue)',
        'agent': 'As isolated_extra_voice.',
        'keep_when': 'Real polyphony.',
        'never_auto': 'MIDI edits.',
        'verify': _NOTATION_VERIFY,
    },
    'sparse_extra_voice': {
        'meaning': 'An extra voice with only one or two short notes.',
        'causes': 'one overlapping note forced a voice.',
        'candidates': [KEEP, NOTATION],
        'evidence': 'pitches and beats (in the issue)',
        'agent': 'As isolated_extra_voice.',
        'keep_when': 'Real polyphony.',
        'never_auto': 'MIDI edits.',
        'verify': _NOTATION_VERIFY,
    },
    'unnecessary_extra_voice': {
        'meaning': 'An extra voice with three or more notes that is not established in the piece.',
        'causes': 'overlapping durations, or a real inner line.',
        'candidates': [KEEP, NOTATION],
        'evidence': 'voice statistics, pitches and beats (in the issue)',
        'agent': 'Keep a voice that carries an independent rhythm; merge one that only duplicates the main '
                 'voice\'s rhythm.',
        'keep_when': 'Independent rhythm or held notes under movement.',
        'never_auto': 'MIDI edits.',
        'verify': _NOTATION_VERIFY,
    },
    'unnecessary_beam_break': {
        'meaning': 'Beamed notes inside one beat are split.',
        'causes': 'export artefact.',
        'candidates': [NOTATION],
        'evidence': 'beat positions (in the issue)',
        'agent': 'None; rebeam per beat.',
        'keep_when': 'The break marks a deliberate phrasing.',
        'never_auto': 'MIDI edits.',
        'verify': _NOTATION_VERIFY,
    },
    'unnecessary_tie_chain': {
        'meaning': 'One pitch is split into three or more tied segments inside a measure.',
        'causes': 'quantisation of a long note across beat boundaries.',
        'candidates': [NOTATION],
        'evidence': 'segment count and beats (in the issue)',
        'agent': 'None; rewrite as the fewest notes the meter allows.',
        'keep_when': 'Ties are needed to show the beat in syncopation.',
        'never_auto': 'MIDI edits.',
        'verify': _NOTATION_VERIFY,
    },
    'short_rest_fragmentation': {
        'meaning': 'A sixteenth or shorter rest inside a phrase.',
        'causes': 'a note end a little before the next onset.',
        'candidates': [KEEP, NOTATION],
        'evidence': 'rest type and beat (in the issue)',
        'agent': 'Extend the previous note in the score unless the rest is an audible articulation.',
        'keep_when': 'Staccato or a real breath.',
        'never_auto': 'MIDI edits.',
        'verify': _NOTATION_VERIFY,
    },
    'high_note_on_lower_staff': {
        'meaning': 'The lower staff reaches A4 or above.',
        'causes': 'a hand assignment error, or a wrong octave.',
        'candidates': [KEEP, MOVE_HAND, OCTAVE],
        'evidence': 'pitches and beats (in the issue); for the MIDI side, use the onset-group candidates',
        'agent': 'Move the note to the upper staff when the right hand is free; change octave only with the '
                 'pitch evidence of octave_pattern_outlier.',
        'keep_when': 'The left hand crosses over by design.',
        'never_auto': 'Pitch changes.',
        'verify': _NOTATION_VERIFY,
    },
    'low_note_on_upper_staff': {
        'meaning': 'The upper staff reaches C2 or below.',
        'causes': 'a hand assignment error, or a wrong octave.',
        'candidates': [KEEP, MOVE_HAND, OCTAVE],
        'evidence': 'pitches (in the issue)',
        'agent': 'As high_note_on_lower_staff.',
        'keep_when': 'Crossing by design.',
        'never_auto': 'Pitch changes.',
        'verify': _NOTATION_VERIFY,
    },
}


def emitted_codes(source=None):
    """Issue codes measure_quality can emit, read from its source so new codes are found."""
    text = Path(source or measure_quality.__file__).read_text(encoding='utf-8')
    return set(re.findall(r"_issue\(\s*'[a-z]+',\s*'([a-z_]+)'", text))


def uncovered_codes():
    return sorted(emitted_codes() - set(STRATEGIES))


def _pitch_classes(groups):
    return {note['note'] % 12 for group in groups for note in group}


def _jaccard(first, second):
    first, second = set(first), set(second)
    return len(first & second) / len(first | second) if first or second else 1.0


def _metrics(pitches, previous, following, support):
    """Objective numbers for one candidate result of the flagged onset group.

    support: pitch classes sounding within one bar either side (the flagged group excluded); a
    pitch class absent there is a lead for a wrong pitch, but modulations and passing tones break it.
    """
    pitches = sorted(pitches)
    neighbours = previous[-1:] + following[:1]
    best = measure_quality._best_split(pitches) if pitches else (0, 0)
    leap = max((min(abs(pitch - other['note']) for group in neighbours for other in group)
                for pitch in pitches), default=0) if neighbours else None
    context = previous + following
    pattern = max((_jaccard(pitches, [note['note'] for note in group]) for group in context), default=0.0)
    pattern_pc = max((_jaccard({pitch % 12 for pitch in pitches}, {note['note'] % 12 for note in group})
                      for group in context), default=0.0)
    return {
        'notes': len(pitches),
        'playable': best is not None,
        'widest_hand_span': None if best is None else best[0],
        'max_leap_to_neighbours': leap,
        'best_pattern_match': round(pattern, 3),
        'best_pitch_class_match': round(pattern_pc, 3),
        'unsupported_pitch_classes': sorted(note_name(pitch) for pitch in pitches if pitch % 12 not in support),
    }


def _note_facts(note, group, ticks_per_beat):
    others = [item['velocity'] for item in group if item is not note]
    median = statistics.median(others) if others else note['velocity']
    return {
        'pitch': note['note'],
        'name': note_name(note['note']),
        'channel': note['start_message'].channel,
        'start_tick': note['start_ticks'],
        'velocity': note['velocity'],
        'velocity_minus_group_median': note['velocity'] - median,
        'onset_offset_ms': round((note['start_seconds'] - min(item['start_seconds'] for item in group)) * 1000, 1),
        'length_beats': round((note['end_ticks'] - note['start_ticks']) / ticks_per_beat, 3),
    }


def candidates(code, group, previous, following, ticks_per_beat, support):
    """Every candidate the strategy names, each with deterministic metrics. None of them is applied."""
    kinds = STRATEGIES[code]['candidates']
    base = [note['note'] for note in group]
    results = [{'action': KEEP, 'metrics': _metrics(base, previous, following, support)}] if KEEP in kinds else []

    def add(action, pitches, note=None, **details):
        entry = {'action': action, 'metrics': _metrics(pitches, previous, following, support)}
        if note is not None:
            entry['note'] = _note_facts(note, group, ticks_per_beat)
        entry.update(details)
        results.append(entry)

    for note in sorted(group, key=lambda item: item['note']):
        rest = [item['note'] for item in group if item is not note]
        if DELETE in kinds:
            add(DELETE, rest, note)
        if OCTAVE in kinds:
            for shift in (-12, 12):
                if 21 <= note['note'] + shift <= 108 and note['note'] + shift not in base:
                    add(OCTAVE, rest + [note['note'] + shift], note, new_pitch=note_name(note['note'] + shift))
        if SEMITONE in kinds:
            for shift in (-1, 1):
                if note['note'] + shift not in base:
                    add(SEMITONE, rest + [note['note'] + shift], note, new_pitch=note_name(note['note'] + shift))
    if ADD_OCTAVE in kinds:
        for note in group:
            for shift in (-12, 12):
                partner = note['note'] + shift
                if 21 <= partner <= 108 and partner not in base:
                    add(ADD_OCTAVE, base + [partner], note, new_pitch=note_name(partner))
    for kind in (MOVE_HAND, NOTATION, SIMPLIFY):
        if kind in kinds:
            results.append({'action': kind, 'metrics': None,
                            'note': 'sound unchanged' if kind != SIMPLIFY else 'arrangement choice, not a correction'})
    return results


def _locate(groups, issue):
    ticks = {note.get('start_tick') for note in issue.get('notes', []) if note.get('start_tick') is not None}
    if not ticks:
        return None
    for index, group in enumerate(groups):
        if ticks & {note['start_ticks'] for note in group}:
            return index
    return None


def route(midi_path, musicxml_path=None, bar_offset=0, measures=None):
    """Review the MIDI, then attach each issue's strategy and candidate metrics."""
    review = measure_quality.analyze(midi_path, musicxml_path, bar_offset)
    midi_file = mido.MidiFile(midi_path)
    groups = group_notes(collect_notes(midi_file))
    out = []
    for measure in review['measures']:
        if measures and not measures[0] <= measure['display_measure'] <= measures[1]:
            continue
        for issue in measure['issues']:
            entry = {'display_measure': measure['display_measure'], 'code': issue['code'],
                     'beat': issue['beat'], 'reason': issue['reason'],
                     'strategy': STRATEGIES.get(issue['code'])}
            index = _locate(groups, issue)
            if entry['strategy'] and index is not None:
                group = groups[index]
                if issue['code'] in ('fast_short_burst_candidate',):
                    ticks = {note['start_tick'] for note in issue['notes']}
                    run = [item for item in groups if item[0]['start_ticks'] in ticks]
                    group = [note for item in run for note in item]
                    index = groups.index(run[0])
                    following = groups[index + len(run):index + len(run) + CONTEXT_GROUPS]
                else:
                    following = groups[index + 1:index + 1 + CONTEXT_GROUPS]
                previous = groups[max(0, index - CONTEXT_GROUPS):index]
                entry['group'] = [_note_facts(note, group, midi_file.ticks_per_beat)
                                  for note in sorted(group, key=lambda item: item['note'])]
                bar_ticks = midi_file.ticks_per_beat * review['inputs']['beats_per_bar']
                start = group[0]['start_ticks']
                window = [item for item in groups if abs(item[0]['start_ticks'] - start) <= bar_ticks
                          and not set(id(note) for note in item) & set(id(note) for note in group)]
                entry['candidates'] = candidates(issue['code'], group, previous, following,
                                                 midi_file.ticks_per_beat, _pitch_classes(window))
            elif entry['strategy']:
                # Score-only issue: the route is the strategy's action list; the score file is the evidence.
                entry['candidates'] = [{'action': kind, 'metrics': None}
                                       for kind in entry['strategy']['candidates']]
            out.append(entry)
    return {'inputs': review['inputs'], 'read_only': True, 'issues': out}


def strategy_markdown():
    lines = ['# Repair strategies per review code', '',
             'Generated from `src/repair_strategy.py`. Candidates are compared, never applied automatically.', '']
    for code in sorted(STRATEGIES):
        item = STRATEGIES[code]
        lines.append('## `{}`'.format(code))
        for key in ('meaning', 'causes', 'candidates', 'evidence', 'agent', 'keep_when', 'never_auto', 'verify'):
            value = ', '.join(item[key]) if key == 'candidates' else item[key]
            lines.append('- {}: {}'.format(key, value))
        lines.append('')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Attach repair strategies and candidate metrics to the '
                                                 'measure review. Read-only.')
    parser.add_argument('input_midi', nargs='?', help='Bar-aligned MIDI to review.')
    parser.add_argument('--musicxml', help='MusicXML of the same passage.')
    parser.add_argument('--bar-offset', type=int, default=0, help='display measure = MIDI measure + offset.')
    parser.add_argument('--measures', help='Display measure range such as 110-112.')
    parser.add_argument('-o', '--output', help='Write JSON here instead of printing.')
    parser.add_argument('--list-strategies', action='store_true', help='Print the strategy table as Markdown.')
    parser.add_argument('--check-coverage', action='store_true',
                        help='Exit 1 when a review code has no strategy.')
    args = parser.parse_args(argv)
    if args.check_coverage or not args.input_midi and not args.list_strategies:
        missing = uncovered_codes()
        print('Review codes without a strategy: {}'.format(', '.join(missing) if missing else 'none'))
        if missing:
            return 1
        if not args.input_midi and not args.list_strategies:
            return 0
    if args.list_strategies:
        print(strategy_markdown())
        return 0
    measures = None
    if args.measures:
        first, _, last = args.measures.partition('-')
        measures = (int(first), int(last or first))
    result = route(args.input_midi, args.musicxml, args.bar_offset, measures)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.output:
        Path(args.output).write_text(text + '\n', encoding='utf-8')
        print('Writing repair routing: {}'.format(args.output))
    else:
        print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
