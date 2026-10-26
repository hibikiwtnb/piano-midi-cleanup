import argparse
import bisect
import itertools
import os
import re
import sys
import tkinter as tk
from collections import Counter, defaultdict
from tkinter import filedialog, messagebox

import mido
from report_utils import update_report


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

MAX_FINGERS = 5
EXTRA_FINGER_COST = 20.0

# Register line between the lower and upper blob of notes, one per beat, chosen by Viterbi
# to run through the widest empty pitch band around each beat.
BLOB_LOWEST_LINE = 21
BLOB_HIGHEST_LINE = 108
BLOB_CONTEXT_BEATS = 1
BLOB_MAX_GAP = 12
BLOB_LINE_MOVE_COST = 0.5
# Per note on the wrong side of the line: player 1 below it, player 2 above it.
# With two hands, left stays below and right above. Scaled down where the empty band
# is narrower than BLOB_CLEAR_GAP semitones on each side.
BLOB_SIDE_COST = 4.0
BLOB_CLEAR_GAP = 4

# With three or four hands, each note given to the second player's hands costs this,
# so the additional player is used only when needed.
SECOND_HAND_NOTE_COST = 1.0

# Optional repeated-note continuity experiment; zero preserves the established split.
REPEAT_NOTE_COST = 0.0
REPEAT_CONTEXT_BEATS = 2.0
MELODIC_CONTINUITY_COST = 0.0
MELODIC_MAX_STEP = 5

# Opt-in three-hand material grouping. Zero keeps the established onset search.
MATERIAL_GROUPING_BARS = 0
MATERIAL_REST_BEATS = 2
MATERIAL_MELODY_MIN_ONSETS = 3
MATERIAL_MELODY_OVERLAP = 0.5
MATERIAL_HANDOFF_COST = 4.0

# Three-hand mode: the second player's right hand is enabled in whole-bar segments,
# never just for single onsets. An ON segment must last at least this many bars.
# PROJECT INVARIANT: do not turn this into per-onset activation without a separate
# design change and regression tests.
SECOND_PLAYER_MIN_BARS = 4
SECOND_PLAYER_ACTIVATION_COST = 8.0
SECOND_PLAYER_BAR_COST = 1.0
SECOND_RIGHT_MIN_PITCH = 0
SECOND_RIGHT_REGISTER_COST = 10.0
SECOND_PLAYER_SPARSE_NOTES = 2
FIRST_PLAYER_MAX_NOTES = 4
FIRST_PLAYER_MAX_SPAN = 12
BEATS_PER_BAR = 4

RIGHT_HAND_CHANNEL = 0
SECOND_RIGHT_HAND_CHANNEL = 1
LEFT_HAND_CHANNEL = 2
SECOND_LEFT_HAND_CHANNEL = 3
# Hands from low to high; each onset group gives each hand a contiguous slice of its sorted pitches.
# Player 1 occupies the lower two slices; player 2, when present, sits to the right and occupies higher slices.
# PROJECT INVARIANT: this order and player ownership are fixed musical semantics, not refactoring details.
# Do not change them without a separate design change and regression tests.
HANDS = {
    2: ('left', 'right'),
    3: ('left', 'right', 'second_right'),
    4: ('left', 'right', 'second_left', 'second_right'),
}
CHANNELS = {
    'left': LEFT_HAND_CHANNEL,
    'second_left': SECOND_LEFT_HAND_CHANNEL,
    'second_right': SECOND_RIGHT_HAND_CHANNEL,
    'right': RIGHT_HAND_CHANNEL,
}


def parse_midi_pitch(value):
    """Accept MIDI 0-127 or scientific pitch notation with middle C = C4."""
    text = str(value).strip()
    try:
        pitch = int(text)
    except ValueError:
        match = re.fullmatch(r'([A-Ga-g])([#b]?)(-?\d+)', text)
        if not match:
            raise ValueError('Enter a note such as C5 or a MIDI pitch from 0 to 127 (C4 = 60).')
        name, accidental, octave = match.groups()
        pitch = (int(octave) + 1) * 12 + {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}[name.upper()]
        pitch += {'': 0, '#': 1, 'b': -1}[accidental]
    if not 0 <= pitch <= 127:
        raise ValueError('MIDI pitch must be between 0 and 127 (C4 = 60).')
    return pitch


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


def line_scores(pitches):
    """Half-width of the empty band around each line, or 0 when one side has no notes."""
    scores = []
    for line in range(BLOB_LOWEST_LINE, BLOB_HIGHEST_LINE + 1):
        index = bisect.bisect_left(pitches, line)
        if index == 0 or index == len(pitches):
            scores.append(0.0)
        else:
            scores.append(min(line - 0.5 - pitches[index - 1], pitches[index] - line + 0.5, BLOB_MAX_GAP))
    return scores


def best_previous_lines(totals):
    """For each line, the previous line maximising total minus the move cost (linear distance transform)."""
    best = list(range(len(totals)))

    def value(previous, line):
        return totals[previous] - BLOB_LINE_MOVE_COST * abs(line - previous)

    for line in range(1, len(totals)):
        if value(best[line - 1], line) > value(best[line], line):
            best[line] = best[line - 1]
    for line in range(len(totals) - 2, -1, -1):
        if value(best[line + 1], line) > value(best[line], line):
            best[line] = best[line + 1]
    return best


def blob_lines(notes, ticks_per_beat, hands=2):
    """Per beat (line pitch, clarity): notes below the line form the lower blob, the rest the upper blob."""
    if not notes:
        return []
    beats = max(note['start_ticks'] for note in notes) // ticks_per_beat + 1
    onsets = [[] for _ in range(beats)]
    for note in notes:
        onsets[note['start_ticks'] // ticks_per_beat].append(note['note'])

    beat_scores = []
    for beat in range(beats):
        window = onsets[max(0, beat - BLOB_CONTEXT_BEATS):beat + BLOB_CONTEXT_BEATS + 1]
        beat_scores.append(line_scores(sorted(pitch for pitches in window for pitch in pitches)))

    totals = list(beat_scores[0])
    back = []
    for scores in beat_scores[1:]:
        best = best_previous_lines(totals)
        back.append(best)
        totals = [totals[previous] - BLOB_LINE_MOVE_COST * abs(line - previous) + scores[line]
                  for line, previous in enumerate(best)]
    path = [max(range(len(totals)), key=lambda line: totals[line])]
    for best in reversed(back):
        path.append(best[path[-1]])
    path.reverse()
    blobs = [(BLOB_LOWEST_LINE + line, min(scores[line], BLOB_CLEAR_GAP) / BLOB_CLEAR_GAP)
             for line, scores in zip(path, beat_scores)]
    if hands == 3:
        # A short rest in one blob is missing evidence, not a new player boundary.
        # Bridge only gaps bracketed by reliable evidence for the identical line;
        # longer solos and moving/contradictory boundaries remain uncertain.
        index = 0
        while index < len(blobs):
            line, clarity = blobs[index]
            if clarity:
                index += 1
                continue
            start = index
            while index < len(blobs) and blobs[index] == (line, 0.0):
                index += 1
            if (start > 0 and index < len(blobs) and index - start <= 2 * BLOB_CONTEXT_BEATS
                    and blobs[start - 1][0] == line == blobs[index][0]):
                confidence = min(blobs[start - 1][1], blobs[index][1])
                for beat in range(start, index):
                    blobs[beat] = (line, confidence)
    return blobs


def blob_cost(slices, blob):
    """Keep player 1's two hands in the lower blob and player 2 above the line.

    Two hands still split left/right across the line. With three or four hands,
    ``left`` and ``right`` form the first player's lower blob, while the second
    player's hands (including ``second_right``) form the upper blob.

    PROJECT INVARIANT: do not reduce this to a player-1 left/right boundary or
    change the player ownership without a separate design change and regression tests.
    """
    line, clarity = blob
    if len(slices) == 2:
        left, right = slices
        wrong = sum(1 for pitch in right if pitch < line)
        wrong += sum(1 for pitch in left if pitch >= line)
    else:
        first_player = slices[:2]
        second_player = slices[2:]
        wrong = sum(1 for pitches in first_player for pitch in pitches if pitch >= line)
        wrong += sum(1 for pitches in second_player for pitch in pitches if pitch < line)
    return wrong * BLOB_SIDE_COST * clarity


def preferred_register_cost(slices, second_player_active=False):
    """Soft register preference; a small excursion is cheaper than a large one."""
    if not SECOND_RIGHT_MIN_PITCH or len(slices) < 3:
        return 0.0
    upper = slices[HANDS[len(slices)].index('second_right')]
    distance_squared = sum(max(0, SECOND_RIGHT_MIN_PITCH - pitch) ** 2 for pitch in upper)
    if len(slices) == 3 and second_player_active:
        # During joint playing, player one's right hand stays below the upper register.
        distance_squared += sum(max(0, pitch - SECOND_RIGHT_MIN_PITCH + 1) ** 2 for pitch in slices[1])
    return SECOND_RIGHT_REGISTER_COST * distance_squared


def split_cost(slices, onset, state, blob, second_player_active=False):
    cost = sum(hand_cost(pitches, onset, state['pitch'][hand], state['onset'][hand], state['run'][hand])
               for hand, pitches in enumerate(slices))
    cost += register_cost(slices[0], slices[1])
    cost += sum(len(pitches) for pitches in slices[2:]) * SECOND_HAND_NOTE_COST
    cost += preferred_register_cost(slices, second_player_active)
    return cost + blob_cost(slices, blob)


def next_state(state, slices, onset, cost, cuts, parent):
    new_state = {
        'pitch': list(state['pitch']),
        'onset': list(state['onset']),
        'run': list(state['run']),
        'cost': state['cost'] + cost,
        'cuts': cuts,
        'parent': parent,
    }
    for hand, pitches in enumerate(slices):
        if pitches:
            new_state['pitch'][hand] = sum(pitches) / len(pitches)
            new_state['run'][hand] = next_run_length(pitches, onset, state['onset'][hand], state['run'][hand])
            new_state['onset'][hand] = onset
    return new_state


def cut_options(size, hands):
    return list(itertools.combinations_with_replacement(range(size + 1), hands - 1))


def hand_fits(pitches, max_notes, max_span):
    """Whether sorted pitches fit one hand's simultaneous-note limits."""
    return len(pitches) <= max_notes and (not pitches or pitches[-1] - pitches[0] <= max_span)


def repeated_note_cost(slices, recent, tick, context_ticks, unique_pitches):
    """Soft cost for changing a recent repeated pitch's hand, fading over two beats.

    Simultaneous duplicate pitches are ambiguous and provide no continuity evidence.
    """
    cost = 0.0
    for hand, pitches in enumerate(slices):
        for pitch in pitches:
            if pitch not in unique_pitches or pitch not in recent:
                continue
            previous_hand, previous_tick = recent[pitch]
            if hand != previous_hand:
                cost += REPEAT_NOTE_COST * max(0.0, 1.0 - (tick - previous_tick) / context_ticks)
    return cost


def second_player_modes(state, bar, first_of_bar, minimum_bars):
    """ON/OFF choices for the second player at a three-hand bar boundary.

    A bar keeps the state chosen at its first onset. Turning ON pays activation
    and the first bar's upkeep; staying ON pays one bar at a time. An ON segment
    cannot turn OFF until it has lasted at least ``minimum_bars`` bars.
    """
    active = state['second_player_active']
    if not first_of_bar:
        return ((active, state['second_player_start_bar'], state['second_player_last_bar'], 0.0),)
    if not active:
        return (
            (False, None, None, 0.0),
            (True, bar, bar, SECOND_PLAYER_ACTIVATION_COST + SECOND_PLAYER_BAR_COST),
        )
    start = state['second_player_start_bar']
    last = state['second_player_last_bar']
    modes = [(True, start, bar, SECOND_PLAYER_BAR_COST * (bar - last))]
    if bar - start >= minimum_bars:
        # Turning OFF here still owes upkeep for bars up to the one before it.
        modes.append((False, None, None, SECOND_PLAYER_BAR_COST * max(0, bar - 1 - last)))
    return tuple(modes)


def melodic_links(groups, ticks_per_beat):
    """Mutual nearest neighbors in pitch/time form short, nonbranching voice links.

    Inspect both earlier and later notes; simultaneous and duplicate pitches in a
    group provide no link. Links expire after the existing two-beat context.
    """
    events = []
    for group_index, group in enumerate(groups):
        pitches = sorted(note['note'] for note in group)
        counts = Counter(pitches)
        events.extend((group_index, index, pitch, group[0]['start_ticks'])
                      for index, pitch in enumerate(pitches) if counts[pitch] == 1)
    forward, backward = {}, {}
    forward_scores, backward_scores = {}, {}
    context = REPEAT_CONTEXT_BEATS * ticks_per_beat
    for i, a in enumerate(events):
        for j in range(i + 1, len(events)):
            b = events[j]
            age = b[3] - a[3]
            if age >= context:
                break
            if b[0] == a[0] or age <= 0 or abs(a[2] - b[2]) > MELODIC_MAX_STEP:
                continue
            score = (abs(a[2] - b[2]) + 2 * age / ticks_per_beat, age)
            if i not in forward_scores or score < forward_scores[i]:
                forward_scores[i], forward[i] = score, j
            if j not in backward_scores or score < backward_scores[j]:
                backward_scores[j], backward[j] = score, i
    incoming = [[] for _ in groups]
    for i, j in forward.items():
        if backward.get(j) != i:
            continue
        a, b = events[i], events[j]
        weight = (1 - abs(a[2] - b[2]) / (MELODIC_MAX_STEP + 1)) * (1 - (b[3] - a[3]) / context)
        incoming[b[0]].append(((a[0], a[1]), b[1], weight))
    return incoming


def material_groups(groups, tpb, context_bars=4, limits=None):
    """Cluster adjacent pitches using repeated co-onsets and local melodic motion.

    Group identity is local to a bar. Context at 1/2/4 bars supplies evidence,
    never hand labels. A group can move over more than an octave over time, but
    must fit one hand at every actual onset. No MIDI times or notes are changed.
    """
    max_notes, max_span = limits or (FIRST_PLAYER_MAX_NOTES, FIRST_PLAYER_MAX_SPAN)
    bars = defaultdict(list)
    for index, notes in enumerate(groups):
        bars[max(n['start_ticks'] for n in notes) // (BEATS_PER_BAR * tpb)].append(index)
    episodes = []
    for bar, indices in sorted(bars.items()):
        current = []
        for index in indices:
            if current and groups[index][0]['start_ticks'] - groups[current[-1]][0]['start_ticks'] >= MATERIAL_REST_BEATS * tpb:
                episodes.append((bar, current))
                current = []
            current.append(index)
        episodes.append((bar, current))
    result = []
    for bar, indices in episodes:
        pitches = sorted({n['note'] for i in indices for n in groups[i]})
        evidence = defaultdict(float)
        for width in (1, 2, 4):
            if width > context_bars:
                break
            # Sliding overlapping windows avoid fixed two/four-bar block seams.
            for first in range(bar - width + 1, bar + 1):
                context = sorted(i for b in range(first, first + width) for i in bars.get(b, ()))
                occurrences = {p: {i for i in context if any(n['note'] == p for n in groups[i])} for p in pitches}
                for a, b in zip(pitches, pitches[1:]):
                    common = len(occurrences[a] & occurrences[b])
                    if b - a <= max_span and common:
                        affinity = common / max(1, len(occurrences[a] | occurrences[b]))
                        evidence[a, b] = max(evidence[a, b], affinity * common)
                for earlier, later in zip(context, context[1:]):
                    if groups[later][0]['start_ticks'] - groups[earlier][0]['start_ticks'] > tpb:
                        continue
                    left = sorted({n['note'] for n in groups[earlier]})
                    right = sorted({n['note'] for n in groups[later]})
                    for a in left:
                        b = min(right, key=lambda p: (abs(p - a), p))
                        if a == min(left, key=lambda p: (abs(p - b), p)) and 0 < abs(a - b) <= MELODIC_MAX_STEP:
                            edge = tuple(sorted((a, b)))
                            if edge[0] in pitches and edge[1] in pitches:
                                evidence[edge] = max(evidence[edge], 0.5 / abs(a - b))
        clusters = [{p} for p in pitches]
        while True:
            options = []
            for j in range(len(clusters) - 1):
                merged = clusters[j] | clusters[j + 1]
                strength = max((evidence[a, b] for a in clusters[j] for b in clusters[j + 1]), default=0)
                if strength <= 0:
                    continue
                if all(hand_fits(sorted(n['note'] for n in groups[i] if n['note'] in merged),
                                 max_notes, max_span) for i in indices):
                    options.append((strength, -min(abs(a - b) for a in clusters[j] for b in clusters[j + 1]), -j))
            if not options:
                break
            _, _, negative_index = max(options)
            j = -negative_index
            clusters[j:j + 2] = [clusters[j] | clusters[j + 1]]
        result.append(dict(bar=bar, indices=indices, clusters=[sorted(c) for c in clusters]))
    return result


def search_grouped_splits(groups, blobs, tpb, minimum_bars, return_details=False, limits=None):
    """Prefer feasibility, coherent material/register, fewer third-hand bars, cost.

    This changes the objective intentionally: the old note-level objective cannot
    decide whether a third hand is needed for whole musical responsibilities.
    Availability still has the existing minimum-bar ON/OFF envelope. A player
    may rest inside that envelope. Blob retains its original lower/upper ownership
    in the final cost tier; material coherence decides participation first.
    Group ownership is soft, so individual notes can move to preserve feasibility.
    """
    max_notes, max_span = limits or (FIRST_PLAYER_MAX_NOTES, FIRST_PLAYER_MAX_SPAN)
    groups = [sorted(group, key=lambda note: note['note']) for group in groups]
    materials = material_groups(groups, tpb, MATERIAL_GROUPING_BARS, limits)
    initial = dict(pitch=[None] * 3, onset=[None] * 3, run=[0] * 3, cost=0,
                   second_player_active=False, second_player_start_bar=None,
                   second_player_last_bar=None, rank=(0, 0, 0, 0), path=[], used_bar=None, last_owners={})
    beam = [initial]
    previous = None
    for material in materials:
        bar, indices, clusters = (material[k] for k in ('bar', 'indices', 'clusters'))
        mapping = {p: j for j, cluster in enumerate(clusters) for p in cluster}
        # One isolated onset has no temporal evidence for a particular grouping.
        # Do not summon a player just to preserve an arbitrary chord subdivision.
        group_weights = {j: int(sum(any(n['note'] in cluster for n in groups[i]) for i in indices) > 1)
                         for j, cluster in enumerate(clusters)}
        links = []
        def melody(cluster, item):
            hits = [(i, [n['note'] for n in groups[i] if n['note'] in cluster]) for i in item['indices']]
            hits = [(i, ps) for i, ps in hits if ps]
            return hits if len(hits) >= MATERIAL_MELODY_MIN_ONSETS and all(len(ps) == 1 for _, ps in hits) else []
        if previous is not None:
            for j, cluster in enumerate(clusters):
                current_hits = melody(cluster, material)
                if not current_hits:
                    continue
                matches = []
                for k, old in enumerate(previous['clusters']):
                    old_hits = melody(old, previous)
                    if not old_hits:
                        continue
                    gap = groups[current_hits[0][0]][0]['start_ticks'] - groups[old_hits[-1][0]][0]['start_ticks']
                    overlap = len(set(old) & set(cluster)) / len(set(old) | set(cluster))
                    if 0 <= gap < MATERIAL_REST_BEATS * tpb and overlap >= MATERIAL_MELODY_OVERLAP:
                        matches.append((overlap, -k))
                if matches:
                    links.append((-max(matches)[1], j))
        plans = []
        onset_options = {}
        for i in indices:
            pitches = [n['note'] for n in groups[i]]
            options = []
            for cuts in cut_options(len(pitches), 3):
                bounds = (0,) + cuts + (len(pitches),)
                slices = [pitches[bounds[h]:bounds[h + 1]] for h in range(3)]
                excess = sum(max(0, len(part) - max_notes)
                             + max(0, part[-1] - part[0] - max_span)
                             for part in slices if part)
                # A resting player does not restrict the primary right hand.
                # In the structural tier, one squared register step equals one
                # note departing from its material group's preferred owner.
                debt = preferred_register_cost(slices, second_player_active=bool(slices[2])) / SECOND_RIGHT_REGISTER_COST
                options.append((slices, excess, debt))
            onset_options[i] = options
        for cuts in cut_options(len(clusters), 3):
            owners = {p: sum(j >= cut for cut in cuts) for p, j in mapping.items()}
            allow_third = any(hand == 2 for hand in owners.values())
            prepared = {}
            for i in indices:
                choices = []
                for slices, excess, debt in onset_options[i]:
                    if slices[2] and not allow_third:
                        continue
                    changed = sum(group_weights[mapping[p]] for h, part in enumerate(slices) for p in part if owners[p] != h)
                    choices.append(((excess, changed + debt), slices))
                best_rank = min(rank for rank, _ in choices)
                prepared[i] = (best_rank, [slices for rank, slices in choices if rank == best_rank])
            plans.append((owners, allow_third, prepared))
        candidates = []
        for state in beam:
            first_of_bar = previous is None or previous['bar'] != bar
            for active, start_bar, last_bar, mode_cost in second_player_modes(state, bar, first_of_bar, minimum_bars):
                for preferred, allow_third, prepared in plans:
                    if allow_third and not active:
                        continue
                    child = dict(state)
                    child['pitch'] = list(state['pitch'])
                    child['onset'] = list(state['onset'])
                    child['run'] = list(state['run'])
                    child['cost'] += mode_cost
                    actual = {}
                    owner_counts = defaultdict(Counter)
                    violation = disagreements = register_debt = 0
                    for i in indices:
                        onset = groups[i][0]['start_seconds']
                        blob = blobs[groups[i][0]['start_ticks'] // tpb]
                        choices = []
                        excess, structural = prepared[i][0]
                        for slices in prepared[i][1]:
                            cost = split_cost(slices, onset, child, blob, second_player_active=bool(slices[2]))
                            choices.append((cost, slices))
                        cost, slices = min(choices, key=lambda item: item[0])
                        changed = sum(group_weights[mapping[p]] for h, part in enumerate(slices) for p in part if preferred[p] != h)
                        violation += excess
                        disagreements += changed
                        register_debt += structural - changed
                        position = 0
                        for h, part in enumerate(slices):
                            for p in part:
                                actual[i, position] = h
                                owner_counts[mapping[p]][h] += 1
                                position += 1
                        # Avoid a parent chain for every onset inside every bar candidate.
                        updated = next_state(child, slices, onset, cost, None, None)
                        child.update({k: updated[k] for k in ('pitch', 'onset', 'run', 'cost')})
                    child.update(second_player_active=active, second_player_start_bar=start_bar,
                                 second_player_last_bar=last_bar)
                    if not active:
                        child['pitch'][2], child['onset'][2], child['run'][2] = None, None, 0
                    dominant = {j: counts.most_common(1)[0][0] for j, counts in owner_counts.items()}
                    handoffs = sum(state['last_owners'].get(old) != dominant[new] for old, new in links)
                    third = any(h == 2 for h in actual.values())
                    child['used_bar'] = bar if third else state['used_bar']
                    child['last_owners'] = dominant
                    child['rank'] = (state['rank'][0] + violation,
                                     state['rank'][1] + disagreements + register_debt + MATERIAL_HANDOFF_COST * handoffs,
                                     state['rank'][2] + int(third and state['used_bar'] != bar), child['cost'])
                    child['path'] = state['path'] + [(material, actual, active, violation)]
                    candidates.append(child)
        beam = sorted(candidates, key=lambda s: s['rank'])[:BEAM_WIDTH]
        previous = material
    chosen = beam[0]
    cuts_per_group = [None] * len(groups)
    active_per_group = [False] * len(groups)
    bars_per_group = [None] * len(groups)
    for material, owners, active, violation in chosen['path']:
        for i in material['indices']:
            hands = [owners[i, position] for position in range(len(groups[i]))]
            cuts_per_group[i] = (hands.count(0), sum(hand <= 1 for hand in hands))
            active_per_group[i] = active
            bars_per_group[i] = material['bar']
    if not return_details:
        return cuts_per_group
    return cuts_per_group, {
        'group_bars': bars_per_group,
        'second_player_active': active_per_group,
        'material_groups': materials,
        'grouped_rank': chosen['rank'],
    }


def search_splits(groups, hands, blobs, ticks_per_beat, limits=None,
                  second_player_bars=None, return_details=False):
    """Beam search over onset groups; each group cuts its sorted pitches into one slice per hand, low to high."""
    if second_player_bars is None:
        second_player_bars = SECOND_PLAYER_MIN_BARS
    if second_player_bars < 1:
        raise ValueError('second player minimum bars must be at least 1')
    if hands == 3 and MATERIAL_GROUPING_BARS:
        return search_grouped_splits(groups, blobs, ticks_per_beat, second_player_bars, return_details, limits)
    use_second_player = hands == 3
    bar_ticks = ticks_per_beat * BEATS_PER_BAR
    group_bars = ([group[0]['start_ticks'] // bar_ticks for group in groups] if use_second_player else [])
    first_of_bar = ([index == 0 or group_bars[index] != group_bars[index - 1]
                     for index in range(len(groups))] if use_second_player else [])
    voice_links = melodic_links(groups, ticks_per_beat) if MELODIC_CONTINUITY_COST else None

    beam = [{
        'pitch': [None] * hands,
        'onset': [None] * hands,
        'run': [0] * hands,
        'cost': 0.0,
        'cuts': None,
        'parent': None,
        'recent': {},
        'voice_recent': {},
        'second_player_active': False,
        'second_player_start_bar': None,
        'second_player_last_bar': None,
    }]

    for group_index, group in enumerate(groups):
        pitches = sorted(note['note'] for note in group)
        onset = group[0]['start_seconds']
        tick = group[0]['start_ticks']
        context_ticks = REPEAT_CONTEXT_BEATS * ticks_per_beat
        unique_pitches = {pitch for pitch, count in Counter(pitches).items() if count == 1}
        blob = blobs[group[0]['start_ticks'] // ticks_per_beat]
        options = []
        for cuts in cut_options(len(pitches), hands):
            bounds = (0,) + cuts + (len(pitches),)
            slices = [pitches[bounds[hand]:bounds[hand + 1]] for hand in range(hands)]
            options.append((cuts, slices))
        if limits is not None:
            fitting = [(cuts, slices) for cuts, slices in options
                       if all(hand_fits(part, *limits) for part in slices)]
            # Reduction explicitly preserves unsolved groups; keep those assignable too.
            if fitting:
                options = fitting
        candidates = []
        for state in beam:
            modes = (second_player_modes(state, group_bars[group_index], first_of_bar[group_index],
                                         second_player_bars)
                     if use_second_player else ((None, None, None, 0.0),))
            for active, start_bar, last_bar, mode_cost in modes:
                if use_second_player and not active:
                    allowed = [(cuts, slices) for cuts, slices in options if not slices[2]]
                    if not allowed:
                        continue
                else:
                    allowed = options
                for cuts, slices in allowed:
                    cost = split_cost(slices, onset, state, blob, second_player_active=active) + mode_cost
                    if REPEAT_NOTE_COST:
                        cost += repeated_note_cost(slices, state['recent'], tick, context_ticks, unique_pitches)
                    if voice_links is not None:
                        for source, index, weight in voice_links[group_index]:
                            if source in state['voice_recent']:
                                previous_hand, _ = state['voice_recent'][source]
                                hand = sum(index >= cut for cut in cuts)
                                if hand != previous_hand:
                                    cost += MELODIC_CONTINUITY_COST * weight
                    child = next_state(state, slices, onset, cost, cuts, state)
                    if use_second_player:
                        child['second_player_active'] = active
                        child['second_player_start_bar'] = start_bar
                        child['second_player_last_bar'] = last_bar
                        if not active:
                            # OFF segments must not carry stale movement or fast-run evidence.
                            child['pitch'][2] = None
                            child['onset'][2] = None
                            child['run'][2] = 0
                    candidates.append(child)
        candidates.sort(key=lambda item: item['cost'])
        beam = candidates[:BEAM_WIDTH]
        if REPEAT_NOTE_COST:
            for state in beam:
                # Only copy history for retained candidates, not every proposed split.
                recent = {pitch: previous for pitch, previous in state['parent']['recent'].items()
                          if tick - previous[1] < context_ticks and pitch not in pitches}
                for index, pitch in enumerate(pitches):
                    if pitch in unique_pitches:
                        recent[pitch] = (sum(index >= cut for cut in state['cuts']), tick)
                state['recent'] = recent
        if voice_links is not None:
            for state in beam:
                recent = {key: value for key, value in state['parent']['voice_recent'].items()
                          if tick - value[1] < context_ticks}
                for index in range(len(pitches)):
                    recent[(group_index, index)] = (sum(index >= cut for cut in state['cuts']), tick)
                state['voice_recent'] = recent

    cuts_per_group = []
    state = beam[0]
    while state['parent'] is not None:
        cuts_per_group.append(state['cuts'])
        state = state['parent']
    cuts_per_group.reverse()
    if not return_details:
        return cuts_per_group
    states = []
    state = beam[0]
    while state is not None:
        states.append(state)
        state = state['parent']
    states.reverse()
    return cuts_per_group, {
        'group_bars': group_bars,
        'second_player_active': [state['second_player_active'] for state in states[1:]],
    }


def assign_groups(groups, hands, blobs, ticks_per_beat, limits=None, second_player_bars=None):
    names = HANDS[hands]
    for group, cuts in zip(groups, search_splits(groups, hands, blobs, ticks_per_beat, limits=limits,
                                                 second_player_bars=second_player_bars)):
        ordered = sorted(group, key=lambda note: note['note'])
        for index, note in enumerate(ordered):
            note['hand'] = names[sum(1 for cut in cuts if index >= cut)]
    if hands == 3:
        consolidate_sparse_player_bars(groups, ticks_per_beat)


def consolidate_sparse_player_bars(groups, ticks_per_beat):
    """Let player two rest for an entire sparse bar if player one's chords cover it.

    The ON envelope remains available across the phrase; actual participation in
    this bar is unnecessary only when every extra note fits an existing right-hand
    chord. Never absorb solo upper-voice notes or change just part of a bar.
    """
    bars = defaultdict(list)
    for group in groups:
        # A grouped chord straddling a barline belongs to the new bar even if
        # its highest note was played a few ticks early. Do not quantize notes.
        bar = max(note['start_ticks'] for note in group) // (ticks_per_beat * BEATS_PER_BAR)
        bars[bar].append(group)
    rested = []
    for bar, bar_groups in bars.items():
        upper = [note for group in bar_groups for note in group if note['hand'] == 'second_right']
        if not 0 < len(upper) <= SECOND_PLAYER_SPARSE_NOTES:
            continue
        affected = [group for group in bar_groups if any(note['hand'] == 'second_right' for note in group)]
        for group in affected:
            right = [note for note in group if note['hand'] == 'right']
            pitches = sorted(note['note'] for note in group if note['hand'] in ('right', 'second_right'))
            if len(right) < 2 or not hand_fits(pitches, FIRST_PLAYER_MAX_NOTES, FIRST_PLAYER_MAX_SPAN):
                break
        else:
            for note in upper:
                note['hand'] = 'right'
            rested.append(bar + 1)
    return rested


def summarize_groups(groups):
    wide_span_groups = 0
    fast_run_chords = 0
    last_onset = {hand: None for hand in CHANNELS}
    run_length = {hand: 0 for hand in CHANNELS}

    for group in groups:
        onset = group[0]['start_seconds']
        wide = False
        for hand in CHANNELS:
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


PEDAL_CONTROLS = {64, 66, 69, 120, 121, 122, 123, 124, 125, 126, 127}


def map_pedal_channels(midi_file, notes):
    """Copy compatible source pedal streams to the hands before changing note channels."""
    destinations = defaultdict(set)
    for note in notes:
        destinations[note['start_message'].channel].add(CHANNELS[note['hand']])
    streams = defaultdict(list)
    for track in midi_file.tracks:
        tick = 0
        for message in track:
            tick += message.time
            if message.type == 'control_change' and message.control in PEDAL_CONTROLS:
                streams[message.channel].append((tick, message.control, message.value))
    signatures = {channel: tuple(sorted(stream, key=lambda item: item[0])) for channel, stream in streams.items()}
    sources = defaultdict(set)
    for source, targets in destinations.items():
        for target in targets:
            sources[target].add(source)
    owners = {}
    for target, channels in sources.items():
        # A controller-only destination channel must not be silently overwritten.
        existing = {target} if target not in destinations and target in streams else set()
        if len({signatures.get(source, ()) for source in channels | existing}) > 1:
            raise ValueError('Conflicting pedal controls for hand channel {}; hand split was not saved.'.format(
                target + 1))
        owners[target] = min(channels)
    routing = defaultdict(set)
    for target, source in owners.items():
        routing[source].add(target)
    rebuilt = []
    for track in midi_file.tracks:
        events = []
        tick = 0
        for message in track:
            tick += message.time
            if message.type == 'control_change' and message.control in PEDAL_CONTROLS:
                source = message.channel
                targets = routing[source] if source in destinations else ({source} if source not in sources else set())
                events.extend((tick, message.copy(channel=target)) for target in sorted(targets))
            else:
                events.append((tick, message))
        rebuilt.append(events)
    for track, events in zip(midi_file.tracks, rebuilt):
        track.clear()
        previous = 0
        for tick, message in events:
            message.time = tick - previous
            track.append(message)
            previous = tick


def apply_channels(notes):
    for note in notes:
        channel = CHANNELS[note['hand']]
        note['start_message'].channel = channel
        note['end_message'].channel = channel


def split_hands_in_place(midi_path, hands=2, limits=None, second_player_bars=None):
    if second_player_bars is None:
        second_player_bars = SECOND_PLAYER_MIN_BARS
    midi_file = mido.MidiFile(midi_path)
    notes = collect_notes(midi_file)
    groups = group_notes(notes)
    blobs = blob_lines(notes, midi_file.ticks_per_beat, hands=hands)
    assign_groups(groups, hands, blobs, midi_file.ticks_per_beat, limits=limits,
                  second_player_bars=second_player_bars)
    map_pedal_channels(midi_file, notes)
    apply_channels(notes)
    midi_file.save(midi_path)

    wide_span_groups, fast_run_chords = summarize_groups(groups)

    stats = {
        'midi_path': midi_path,
        'hands': hands,
        'total_notes': len(notes),
        'hand_notes': {hand: sum(1 for note in notes if note['hand'] == hand) for hand in HANDS[hands]},
        'onset_groups': len(groups),
        'wide_span_groups': wide_span_groups,
        'fast_run_chords': fast_run_chords,
        'second_player_bars': second_player_bars,
        'material_grouping_bars': MATERIAL_GROUPING_BARS if hands == 3 else 0,
    }
    return stats


def format_split_stats(stats):
    total_notes = stats['total_notes']
    lines = [
        'MIDI provisional hand split stats',
        'MIDI: {}'.format(stats['midi_path']),
        'Original notes: {}'.format(total_notes),
        'Hands: {}'.format(stats['hands']),
    ]
    for hand in HANDS[stats['hands']]:
        lines.append('Channel {} / {} hand notes: {} ({:.2%})'.format(
            CHANNELS[hand] + 1,
            hand.replace('_', ' '),
            stats['hand_notes'][hand],
            stats['hand_notes'][hand] / total_notes if total_notes else 0.0,
        ))
    lines += [
        'Onset groups: {}'.format(stats['onset_groups']),
        'Groups with a hand span > {} semitones: {}'.format(COMFORT_SPAN, stats['wide_span_groups']),
        'Multi-note hand inside a fast run (< {:.2f}s): {}'.format(
            FAST_RUN_SECONDS,
            stats['fast_run_chords'],
        ),
    ]
    if stats['hands'] == 3:
        lines.append('Second player minimum active bars: {}'.format(stats['second_player_bars']))
    if stats.get('material_grouping_bars'):
        lines.append('Material grouping context: up to {} bars (experimental)'.format(stats['material_grouping_bars']))
    return '\n'.join(lines)


def write_split_report(stats, report_path=None, append=False):
    report = format_split_stats(stats)
    if report_path:
        update_report(report_path, report)

    print(report)
    if report_path:
        print('Updating hand split report: {}'.format(report_path))


def apply_hand_split(midi_path, report_path=None, append_report=False, hands=2, limits=None,
                     second_player_bars=None):
    stats = split_hands_in_place(midi_path, hands=hands, limits=limits,
                                 second_player_bars=second_player_bars)
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


def positive_int(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError('must be at least 1')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description='Mark provisional piano hand split by MIDI channel.')
    parser.add_argument('input_midi', nargs='?', help='Input MIDI file to modify in place.')
    parser.add_argument('--report', help='Optional report path.')
    parser.add_argument('--append-report', action='store_true',
                        help='Update this report block while retaining other blocks (also the default).')
    parser.add_argument('--hands', type=int, choices=sorted(HANDS), default=2,
                        help='Number of hands: 3 adds a second right hand on channel 2, 4 also a second left hand on channel 4 '
                             '(default: 2).')
    parser.add_argument('--second-player-bars', type=positive_int, default=SECOND_PLAYER_MIN_BARS,
                        metavar='N', help='Three hands: keep the second player active for at least N bars '
                                          '(default: {}).'.format(SECOND_PLAYER_MIN_BARS))
    args = parser.parse_args(argv)

    midi_path = args.input_midi or choose_midi()
    if not midi_path:
        print('No input selected.')
        return 1
    if not os.path.isfile(midi_path):
        messagebox.showerror('Hand Split', 'MIDI file does not exist.')
        return 1

    try:
        apply_hand_split(midi_path, report_path=args.report, append_report=args.append_report, hands=args.hands,
                         second_player_bars=args.second_player_bars)
    except Exception as error:
        messagebox.showerror('Hand Split failed', str(error))
        raise

    if not args.input_midi:
        messagebox.showinfo('Hand Split complete', 'Updated in place:\n{}'.format(midi_path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
