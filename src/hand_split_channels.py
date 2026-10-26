import argparse
import bisect
import itertools
import os
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
# Per note on the wrong side of the line for the first player's left/right split.
# Scaled down where the empty band is narrower than BLOB_CLEAR_GAP semitones on each side.
BLOB_SIDE_COST = 4.0
BLOB_CLEAR_GAP = 4

# With three or four hands, each note given to the second player's hands costs this,
# so the additional player is used only when needed.
SECOND_HAND_NOTE_COST = 1.0

# Optional repeated-note continuity experiment; zero preserves the established split.
REPEAT_NOTE_COST = 0.0
REPEAT_CONTEXT_BEATS = 2.0

# Three-hand split: the second player's right hand joins for whole-bar passages rather than
# single onsets. Activation is paid per OFF -> ON change, upkeep per active bar, and an ON
# passage must last at least PRIMO_ACTIVATION_BARS bars.
PRIMO_ACTIVATION_COST = 8.0
PRIMO_BAR_COST = 1.0
PRIMO_ACTIVATION_BARS = 4
BEATS_PER_BAR = 4

RIGHT_HAND_CHANNEL = 0
SECOND_RIGHT_HAND_CHANNEL = 1
LEFT_HAND_CHANNEL = 2
SECOND_LEFT_HAND_CHANNEL = 3
# Hands from low to high; each onset group gives each hand a contiguous slice of its sorted pitches.
# Player 1 occupies the lower two slices; player 2, when present, sits to the right and occupies higher slices.
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


def blob_lines(notes, ticks_per_beat):
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
    return [(BLOB_LOWEST_LINE + line, min(scores[line], BLOB_CLEAR_GAP) / BLOB_CLEAR_GAP)
            for line, scores in zip(path, beat_scores)]


def blob_cost(slices, blob):
    """Keep the first player's left/right hands on the expected sides of the blob line.

    Extra hands belong to player 2 and are intentionally ignored here; their use is controlled
    by hand feasibility and SECOND_HAND_NOTE_COST rather than forcing the upper blob into them.
    """
    line, clarity = blob
    left, right = slices[:2]
    wrong = sum(1 for pitch in right if pitch < line)
    wrong += sum(1 for pitch in left if pitch >= line)
    return wrong * BLOB_SIDE_COST * clarity


def split_cost(slices, onset, state, blob):
    cost = sum(hand_cost(pitches, onset, state['pitch'][hand], state['onset'][hand], state['run'][hand])
               for hand, pitches in enumerate(slices))
    cost += register_cost(slices[0], slices[1])
    cost += sum(len(pitches) for pitches in slices[2:]) * SECOND_HAND_NOTE_COST
    return cost + blob_cost(slices, blob)


def next_state(state, slices, onset, cost, cuts, parent, removed=(), reduction_loss=0.0):
    new_state = {
        'pitch': list(state['pitch']),
        'onset': list(state['onset']),
        'run': list(state['run']),
        'cost': state['cost'] + cost,
        'cuts': cuts,
        'parent': parent,
        'removed': tuple(removed),
        'group_cost': cost,
        'reduction_loss': reduction_loss,
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


def primo_active_modes(state, bar, first_of_bar, minimum_bars):
    """Allowed Primo states at this onset group, with the extra cost each one adds."""
    if not first_of_bar:
        # A bar keeps the state chosen at its first onset group.
        return ((state['primo_active'], state['primo_start_bar'], state['primo_last_bar'], 0.0),)
    if state['primo_active']:
        modes = [(True, state['primo_start_bar'], bar,
                  PRIMO_BAR_COST * (bar - state['primo_last_bar']))]
        if bar - state['primo_start_bar'] >= minimum_bars:
            # Leaving at this bar still owes upkeep for any bars since the last onset.
            modes.append((False, None, None,
                          PRIMO_BAR_COST * max(0, bar - 1 - state['primo_last_bar'])))
        return modes
    return ((False, None, None, 0.0),
            (True, bar, bar, PRIMO_ACTIVATION_COST + PRIMO_BAR_COST))


def primo_bar_segments(chain, group_bars):
    """1-based inclusive bar ranges where Primo is ON, including bars without onsets."""
    segments = []
    start_bar = None
    for state, bar in zip(chain, group_bars):
        if state['primo_active']:
            if start_bar is None:
                start_bar = state['primo_start_bar']
        elif start_bar is not None:
            segments.append((start_bar + 1, bar))
            start_bar = None
    if start_bar is not None and group_bars:
        segments.append((start_bar + 1, group_bars[-1] + 1))
    return segments


def search_splits(groups, hands, blobs, ticks_per_beat, limits=None,
                  primo_activation_bars=PRIMO_ACTIVATION_BARS,
                  integrated=False, return_details=False, initial_state=None,
                  force_off=False):
    """Beam search over onset groups; each group cuts its sorted pitches into one slice per hand, low to high.

    With three hands and ``integrated``, every group compares three candidates in
    the same beam: OFF with all notes, OFF after the existing playable reduction
    (including its removal loss), and Primo ON with all notes.
    ``initial_state``/``force_off`` are used to price an OFF-only alternative for
    report diagnostics without changing the main search.
    """
    use_primo = hands == 3
    if integrated and not use_primo:
        raise ValueError('Integrated playable reduction is only available with three hands.')
    bar_ticks = ticks_per_beat * BEATS_PER_BAR
    group_bars = [group[0]['start_ticks'] // bar_ticks for group in groups] if use_primo else []
    first_of_bar = ([index == 0 or group_bars[index] != group_bars[index - 1]
                     for index in range(len(groups))] if use_primo else [])
    if integrated:
        from playable_reduction import (DEFAULT_MAX_NOTES_PER_HAND, DEFAULT_MAX_SPAN,
                                        evaluate_reduction)
        reduction_limits = limits or (DEFAULT_MAX_NOTES_PER_HAND, DEFAULT_MAX_SPAN)
    if initial_state is None:
        initial_pitch = [None] * hands
        initial_onset = [None] * hands
        initial_run = [0] * hands
        initial_recent = {}
    else:
        initial_pitch = list(initial_state['pitch'])
        initial_onset = list(initial_state['onset'])
        initial_run = list(initial_state['run'])
        initial_recent = dict(initial_state.get('recent', {}))
    beam = [{
        'pitch': initial_pitch,
        'onset': initial_onset,
        'run': initial_run,
        'cost': 0.0,
        'cuts': None,
        'parent': None,
        'recent': initial_recent,
        'primo_active': False,
        'primo_start_bar': None,
        'primo_last_bar': None,
        'removed': (),
        'group_cost': 0.0,
        'reduction_loss': 0.0,
    }]
    off_best_costs = [None] * len(groups)
    on_best_costs = [None] * len(groups)

    for index, group in enumerate(groups):
        pitches = sorted(note['note'] for note in group)
        onset = group[0]['start_seconds']
        tick = group[0]['start_ticks']
        bar = group_bars[index] if use_primo else None
        context_ticks = REPEAT_CONTEXT_BEATS * ticks_per_beat
        blob = blobs[group[0]['start_ticks'] // ticks_per_beat]

        if integrated:
            plan = evaluate_reduction(group, *reduction_limits)
            # Candidate A1: keep every note, then use the existing two-hand assignment.
            off_variants = []
            for cuts in cut_options(len(pitches), hands):
                bounds = (0,) + cuts + (len(pitches),)
                slices = [pitches[bounds[hand]:bounds[hand + 1]] for hand in range(hands)]
                if not slices[2]:
                    off_variants.append((cuts, slices, (), 0.0))
            # Candidate A2: delete the fewest low-priority notes, then assign the kept notes.
            if plan.removed:
                off_pitches = [note['note'] for note in plan.kept]
                for cut in range(len(off_pitches) + 1):
                    low = off_pitches[:cut]
                    high = off_pitches[cut:]
                    if hand_fits(low, *reduction_limits) and hand_fits(high, *reduction_limits):
                        off_variants.append(((cut, len(off_pitches)), [low, high, []],
                                             plan.removed, plan.loss))
            # Candidate B: keep every note and allow Primo.
            on_options = []
            for cuts in cut_options(len(pitches), hands):
                bounds = (0,) + cuts + (len(pitches),)
                on_options.append((cuts, [pitches[bounds[hand]:bounds[hand + 1]]
                                          for hand in range(hands)]))
        else:
            plan = None
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
            off_variants = ([(cuts, slices, (), 0.0) for cuts, slices in options if not slices[2]]
                            if use_primo else [(cuts, slices, (), 0.0) for cuts, slices in options])
            on_options = [(cuts, slices) for cuts, slices in options]

        candidates = []
        for state in beam:
            if force_off:
                modes = ((False, None, None, 0.0),)
            elif use_primo:
                modes = primo_active_modes(state, bar, first_of_bar[index], primo_activation_bars)
            else:
                modes = ((False, None, None, 0.0),)
            for active, start_bar, last_bar, extra_cost in modes:
                if active:
                    variants = [(cuts, slices, (), 0.0) for cuts, slices in on_options]
                else:
                    variants = off_variants
                if not variants:
                    continue
                for cuts, slices, removed, reduction_loss in variants:
                    cost = split_cost(slices, onset, state, blob) + extra_cost + reduction_loss
                    if REPEAT_NOTE_COST:
                        played_pitches = [pitch for part in slices for pitch in part]
                        unique_pitches = {pitch for pitch, count in Counter(played_pitches).items()
                                          if count == 1}
                        cost += repeated_note_cost(slices, state['recent'], tick, context_ticks,
                                                   unique_pitches)
                    child = next_state(state, slices, onset, cost, cuts, state,
                                       removed=removed, reduction_loss=reduction_loss)
                    if use_primo:
                        child['primo_active'] = active
                        child['primo_start_bar'] = start_bar
                        child['primo_last_bar'] = last_bar
                        if state['primo_active'] and not active:
                            # A later activation starts a new phrase for the third hand.
                            child['pitch'][2] = None
                            child['onset'][2] = None
                            child['run'][2] = 0
                    candidates.append(child)
                    if active:
                        if on_best_costs[index] is None or cost < on_best_costs[index]:
                            on_best_costs[index] = cost
                    elif use_primo:
                        if off_best_costs[index] is None or cost < off_best_costs[index]:
                            off_best_costs[index] = cost
        if not candidates:
            raise ValueError('No feasible hand assignment at onset group {}'.format(index))
        candidates.sort(key=lambda item: item['cost'])
        beam = candidates[:BEAM_WIDTH]
        if REPEAT_NOTE_COST:
            for state in beam:
                # Only copy history for retained candidates, not every proposed split.
                removed_ids = {id(note) for note in state['removed']}
                played_pitches = [note['note'] for note in group if id(note) not in removed_ids]
                unique_pitches = {pitch for pitch, count in Counter(played_pitches).items()
                                  if count == 1}
                recent = {pitch: previous for pitch, previous in state['parent']['recent'].items()
                          if tick - previous[1] < context_ticks and pitch not in pitches}
                for position, pitch in enumerate(played_pitches):
                    if pitch in unique_pitches:
                        recent[pitch] = (sum(position >= cut for cut in state['cuts']), tick)
                state['recent'] = recent

    cuts_per_group = []
    state = beam[0]
    while state['parent'] is not None:
        cuts_per_group.append(state['cuts'])
        state = state['parent']
    cuts_per_group.reverse()
    chain = []
    if use_primo or return_details:
        state = beam[0]
        while state['parent'] is not None:
            chain.append(state)
            state = state['parent']
        chain.reverse()
    segments = primo_bar_segments(chain, group_bars) if use_primo else []
    if not return_details:
        return cuts_per_group, segments
    details = {
        'group_bars': group_bars,
        'group_primo_active': [state.get('primo_active', False) for state in chain],
        'removed_per_group': [state['removed'] for state in chain],
        'reduction_losses': [state['reduction_loss'] for state in chain],
        'group_costs': [state['group_cost'] for state in chain],
        'off_best_costs': off_best_costs,
        'on_best_costs': on_best_costs,
        'total_cost': beam[0]['cost'],
        'group_states': [{'pitch': list(state['pitch']), 'onset': list(state['onset']),
                          'run': list(state['run']), 'recent': dict(state.get('recent', {}))}
                         for state in chain],
    }
    return cuts_per_group, segments, details


def assign_groups(groups, hands, blobs, ticks_per_beat, limits=None,
                  primo_activation_bars=PRIMO_ACTIVATION_BARS, integrated=False,
                  return_details=False):
    names = HANDS[hands]
    if integrated:
        cuts_per_group, primo_segments, details = search_splits(
            groups, hands, blobs, ticks_per_beat, limits=limits,
            primo_activation_bars=primo_activation_bars, integrated=True, return_details=True)
    elif return_details:
        cuts_per_group, primo_segments, details = search_splits(
            groups, hands, blobs, ticks_per_beat, limits=limits,
            primo_activation_bars=primo_activation_bars, return_details=True)
    else:
        cuts_per_group, primo_segments = search_splits(
            groups, hands, blobs, ticks_per_beat, limits=limits,
            primo_activation_bars=primo_activation_bars)
        details = None
    for index, (group, cuts) in enumerate(zip(groups, cuts_per_group)):
        removed = details['removed_per_group'][index] if details else ()
        removed_ids = {id(note) for note in removed}
        ordered = sorted(group, key=lambda note: note['note'])
        for note in ordered:
            note['hand'] = None
        kept = [note for note in ordered if id(note) not in removed_ids]
        for position, note in enumerate(kept):
            note['hand'] = names[sum(1 for cut in cuts if position >= cut)]
    if return_details:
        return primo_segments, details
    return primo_segments


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
        if note['hand'] is None:
            continue
        channel = CHANNELS[note['hand']]
        note['start_message'].channel = channel
        note['end_message'].channel = channel


def remove_note_messages(midi_file, notes):
    """Remove both messages of these notes while keeping the other message objects intact."""
    removals = defaultdict(set)
    for note in notes:
        removals[note['track_index']].update((note['start_index'], note['end_index']))
    for track_index, track in enumerate(midi_file.tracks):
        indexes = removals.get(track_index)
        if not indexes:
            continue
        rebuilt = mido.MidiTrack()
        pending = 0
        for index, message in enumerate(track):
            pending += message.time
            if index in indexes:
                continue
            message.time = pending
            rebuilt.append(message)
            pending = 0
        if pending:
            if rebuilt:
                rebuilt[-1].time += pending
            else:
                rebuilt.append(mido.MetaMessage('end_of_track', time=pending))
        midi_file.tracks[track_index] = rebuilt


def split_hands_in_place(midi_path, hands=2, limits=None,
                         primo_activation_bars=PRIMO_ACTIVATION_BARS, integrated=None):
    if integrated is None:
        integrated = hands == 3
    if integrated and hands != 3:
        raise ValueError('Integrated playable reduction is only available with three hands.')
    midi_file = mido.MidiFile(midi_path)
    notes = collect_notes(midi_file)
    groups = group_notes(notes)
    blobs = blob_lines(notes, midi_file.ticks_per_beat)
    if integrated:
        from playable_reduction import DEFAULT_MAX_NOTES_PER_HAND, DEFAULT_MAX_SPAN
        reduction_limits = limits or (DEFAULT_MAX_NOTES_PER_HAND, DEFAULT_MAX_SPAN)
        primo_segments, details = assign_groups(
            groups, hands, blobs, midi_file.ticks_per_beat, limits=reduction_limits,
            primo_activation_bars=primo_activation_bars, integrated=True, return_details=True)
    else:
        primo_segments = assign_groups(groups, hands, blobs, midi_file.ticks_per_beat, limits=limits,
                                       primo_activation_bars=primo_activation_bars)
        details = None

    kept_notes = [note for note in notes if note['hand'] is not None]
    removed_notes = [note for note in notes if note['hand'] is None]
    if removed_notes:
        remove_note_messages(midi_file, removed_notes)
    map_pedal_channels(midi_file, kept_notes)
    apply_channels(kept_notes)
    midi_file.save(midi_path)

    wide_span_groups, fast_run_chords = summarize_groups(groups)
    reduction_changes = []
    reduction_loss = 0.0
    diagnostics = []
    if details:
        reduction_changes = [(groups[index], list(removed))
                             for index, removed in enumerate(details['removed_per_group']) if removed]
        reduction_loss = sum(details['reduction_losses'])
        for start, end in primo_segments:
            indexes = [index for index, bar in enumerate(details['group_bars'])
                       if start - 1 <= bar <= end - 1 and details['group_primo_active'][index]]
            two_hand_cost = None
            if indexes:
                start_state = details['group_states'][indexes[0] - 1] if indexes[0] > 0 else None
                try:
                    _, _, alternative = search_splits(
                        groups[indexes[0]:indexes[-1] + 1], hands, blobs, midi_file.ticks_per_beat,
                        limits=reduction_limits, primo_activation_bars=primo_activation_bars,
                        integrated=True, return_details=True, initial_state=start_state,
                        force_off=True)
                    two_hand_cost = alternative['total_cost']
                except ValueError:
                    pass
            diagnostics.append({
                'start': start,
                'end': end,
                'notes': sum(1 for index in indexes for note in groups[index]
                             if note['hand'] == 'second_right'),
                'primo_cost': sum(details['group_costs'][index] for index in indexes),
                'two_hand_cost': two_hand_cost,
            })

    stats = {
        'midi_path': midi_path,
        'hands': hands,
        'total_notes': len(notes),
        'final_notes': len(kept_notes),
        'hand_notes': {hand: sum(1 for note in kept_notes if note['hand'] == hand) for hand in HANDS[hands]},
        'onset_groups': len(groups),
        'wide_span_groups': wide_span_groups,
        'fast_run_chords': fast_run_chords,
        'ticks_per_beat': midi_file.ticks_per_beat,
        'integrated_playable': integrated,
        'removed_notes': len(removed_notes),
        'removed_groups': len(reduction_changes),
        'reduction_changes': reduction_changes,
        'reduction_loss': reduction_loss,
    }
    if hands == 3:
        stats['primo_activation_bars'] = primo_activation_bars
        stats['primo_segments'] = primo_segments
        stats['primo_segment_diagnostics'] = diagnostics
    return stats


def format_split_stats(stats):
    total_notes = stats['total_notes']
    final_notes = stats.get('final_notes', total_notes)
    lines = [
        'MIDI provisional hand split stats',
        'MIDI: {}'.format(stats['midi_path']),
        'Original notes: {}'.format(total_notes),
        'Hands: {}'.format(stats['hands']),
    ]
    for hand in reversed(HANDS[stats['hands']]):
        lines.append('Channel {} / {} hand notes: {} ({:.2%})'.format(
            CHANNELS[hand] + 1,
            hand.replace('_', ' '),
            stats['hand_notes'][hand],
            stats['hand_notes'][hand] / final_notes if final_notes else 0.0,
        ))
    if stats['hands'] == 3:
        segments = stats.get('primo_segments') or []
        lines += [
            'Primo activation bars (minimum): {}'.format(stats['primo_activation_bars']),
            'Primo ON segments: {}'.format(len(segments)),
            'Primo ON ranges: {}'.format(
                ', '.join('m{}-m{}'.format(start, end) for start, end in segments) if segments else 'none'),
        ]
        if stats.get('integrated_playable'):
            lines += [
                'Notes after integrated reduction: {}'.format(final_notes),
                'Removed for two-hand playability: {} in {} onset groups'.format(
                    stats['removed_notes'], stats['removed_groups']),
                'Reduction musical-loss cost: {:.1f}'.format(stats['reduction_loss']),
            ]
        diagnostics = stats.get('primo_segment_diagnostics') or []
        if diagnostics:
            lines.append('Primo ON segment alternatives:')
            for item in diagnostics:
                two_hand = ('n/a' if item['two_hand_cost'] is None
                            else '{:.1f}'.format(item['two_hand_cost']))
                lines.append('- m{}-m{}: second_right {} notes, Primo {:.1f}, two-hand alternative {}'.format(
                    item['start'], item['end'], item['notes'], item['primo_cost'], two_hand))
        changes = stats.get('reduction_changes') or []
        if changes:
            from playable_reduction import note_name, position
            lines.append('Reduction removals:')
            for group, removed in changes:
                lines.append('- {}: removed {} from {}'.format(
                    position(group[0], stats['ticks_per_beat']),
                    ' '.join(note_name(note) for note in sorted(removed, key=lambda note: note['note'])),
                    ' '.join(note_name(note) for note in sorted(group, key=lambda note: note['note']))))
    lines += [
        'Onset groups: {}'.format(stats['onset_groups']),
        'Groups with a hand span > {} semitones: {}'.format(COMFORT_SPAN, stats['wide_span_groups']),
        'Multi-note hand inside a fast run (< {:.2f}s): {}'.format(
            FAST_RUN_SECONDS,
            stats['fast_run_chords'],
        ),
    ]
    return '\n'.join(lines)


def write_split_report(stats, report_path=None, append=False):
    report = format_split_stats(stats)
    if report_path:
        update_report(report_path, report)

    print(report)
    if report_path:
        print('Updating hand split report: {}'.format(report_path))


def apply_hand_split(midi_path, report_path=None, append_report=False, hands=2, limits=None,
                     primo_activation_bars=PRIMO_ACTIVATION_BARS, integrated=None):
    stats = split_hands_in_place(midi_path, hands=hands, limits=limits,
                                 primo_activation_bars=primo_activation_bars, integrated=integrated)
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


def positive_bars(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError('expected a positive whole number: {}'.format(text))
    if value < 1:
        raise argparse.ArgumentTypeError('expected a positive whole number: {}'.format(text))
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
    parser.add_argument('--primo-activation-bars', type=positive_bars, default=PRIMO_ACTIVATION_BARS,
                        metavar='N', help='Three hands: keep the second player active for at least N bars (default: {}).'
                        .format(PRIMO_ACTIVATION_BARS))
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
                         primo_activation_bars=args.primo_activation_bars)
    except Exception as error:
        messagebox.showerror('Hand Split failed', str(error))
        raise

    if not args.input_midi:
        messagebox.showinfo('Hand Split complete', 'Updated in place:\n{}'.format(midi_path))
    return 0


if __name__ == '__main__':
    sys.exit(main())