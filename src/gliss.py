"""Glissando correction: find runs in a transcription, keep them out of the other steps, and put them back repaired.

What it answers: which fast one-direction notes are a single gesture on one key colour, all white or all black keys, so
they should share one length, one hand and, on request, the keys the model missed. A run on another major scale is still
found, so that its notes are not read as a white or black run with stray notes, but it is left as it is: those were
mostly fast fingered scales. The exception is D-flat, G-flat and B major, which hold every black key: a hand sliding over
the black keys catches the white keys in the gaps between their groups, so such a run is a black-key glissando with those
white keys in it. The evidence is in docs/gliss-correction-design.md.

What it must not do: change any other note, except that notes struck during a run move to the other hand and notes the
run's hand still holds when it starts end there. Raw note length cannot separate a run from the chords it crosses, because
the pedal carries both to the same end, so length is deliberately not used to decide membership. Notes are only taken
out of the MIDI here and put back after the other steps, so those steps need no exceptions.
"""

import json
from pathlib import Path
import statistics

import mido
import numpy as np

from clean_midi_rules import rebuild_track_without_messages, tick_seconds
from hand_split_channels import CHANNELS, collect_notes
from trim_overlaps import write_note_ends

WHITE_KEYS = (0, 2, 4, 5, 7, 9, 11)
BLACK_KEYS = (1, 3, 6, 8, 10)
MAJOR_STEPS = (0, 2, 4, 5, 7, 9, 11)
NOTE_NAMES = ('C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B')
# Runs on these scales are corrected; a run on any other scale of scale_candidates is only reported, unless the scale holds
# every black key (black_with_gaps).
CORRECTED_SCALES = ('white', 'black')

MIN_DEGREES = 8
# DuET's test on 148 hand-made scores found 6-7 note runs to be false; the shortest hand-checked run has 8 notes.
MIN_NOTES = 8
MAX_SECONDS_PER_DEGREE = 0.075
MIN_SEED_SECONDS = 0.08
MAX_SEED_SECONDS = 3.0
MIN_COVERAGE = 0.7
# Lowest center and top pitch of a run; below them the lines found were octave-doubled chords.
MIN_CENTER_PITCH = 60
MIN_TOP_PITCH = 72
# In degrees from the line: a note on it and a note extending the run past its end.
LINE_TOLERANCE = 1.0
GROW_TOLERANCE = 2.0
# How far a note may lie from the line when its degree would otherwise be empty, so it cannot displace a note on the
# line. Late is wide because onsets near the end of a fast run come out late and out of order (Dream Believers m136:
# E4 G4 C4 F4 D4 B3). Early is narrow because a note struck before the run reaches it is not part of the run; the
# hand-corrected first song keeps E5 of m9, about 5 degrees early, in the chord it crosses.
CLAIM_LATE_TOLERANCE = 5.0
CLAIM_EARLY_TOLERANCE = 2.0
# Up to this many notes past a gap of this many median gaps do not belong to the run. Inside a run such gaps stay,
# because they are the notes the model missed.
MAX_END_GAP_RATIO = 2.0
MAX_END_NOTES = 2
# An end note struck with this many notes off the line, within this time, belongs to their chord.
END_CHORD_NOTES = 2
END_CHORD_SECONDS = 0.015
# Repeated chord test: the same chord within this many beats means a note on the line can be a chord note.
REPEAT_BEATS = 2.0
REPEAT_GROUP_SECONDS = 0.03
REPEAT_MIN_PITCHES = 2
# A run with this share of repeated chord notes is a repeated chord pattern, not a glissando.
MAX_REPEATED_SHARE = 0.3
# Hand-corrected lengths were 1.1-1.33 median gaps, on two runs only.
LENGTH_GAP_RATIO = 1.25
MAX_LENGTH_BEATS = 0.25
SPLIT_PITCH = 60


def major_scale(root):
    return tuple(sorted((root + step) % 12 for step in MAJOR_STEPS))


def scale_candidates():
    """Scales in the order they are tried: a run on white keys is never reported as a major scale that contains it.

    Major scales are tried only so that a major scale run wins over the white or black keys it contains; it is not corrected.
    """
    scales = [('white', WHITE_KEYS), ('black', BLACK_KEYS)]
    scales += [(NOTE_NAMES[root] + ' major', major_scale(root)) for root in range(1, 12)]
    return scales


def black_with_gaps(scale):
    """Does this scale hold every black key? Then its white keys are E/F and B/C, the gaps between the black groups."""
    return set(BLACK_KEYS) < set(scale)


def scale_degree(pitch, scale):
    """The position of a scale pitch counted across octaves; None for a pitch outside the scale."""
    pitch_class = pitch % 12
    return (pitch // 12) * len(scale) + scale.index(pitch_class) if pitch_class in scale else None


def scale_pitch(degree, scale):
    octave, index = divmod(degree, len(scale))
    return octave * 12 + scale[index]


def seed_lines(notes, scale):
    """Straight lines in time and scale degree, best first and not overlapping in one direction."""
    members = [note for note in notes if note['note'] % 12 in scale]
    if len(members) < MIN_DEGREES * MIN_COVERAGE:
        return []
    times = np.array([note['start_seconds'] for note in members])
    degrees = np.array([scale_degree(note['note'], scale) for note in members])
    lines = []
    for first in range(len(members)):
        low = np.searchsorted(times, times[first] + MIN_SEED_SECONDS)
        high = np.searchsorted(times, times[first] + MAX_SEED_SECONDS)
        for last in range(low, high):
            rise = degrees[last] - degrees[first]
            if abs(rise) < MIN_DEGREES - 1:
                continue
            duration = times[last] - times[first]
            if duration / abs(rise) > MAX_SECONDS_PER_DEGREE:
                continue
            window = slice(first, last + 1)
            predicted = degrees[first] + rise * (times[window] - times[first]) / duration
            on_line = degrees[window][np.abs(degrees[window] - predicted) <= LINE_TOLERANCE]
            lowest, highest = sorted((degrees[first], degrees[last]))
            covered = len(set(on_line[(on_line >= lowest) & (on_line <= highest)].tolist()))
            coverage = covered / (abs(rise) + 1)
            if coverage >= MIN_COVERAGE:
                lines.append((covered, times[first], times[last], int(degrees[first]), int(degrees[last])))
    kept = []
    for line in sorted(lines, key=lambda item: (-item[0], item[1])):
        direction = np.sign(line[4] - line[3])
        if all(line[2] < other[1] or line[1] > other[2] or direction != np.sign(other[4] - other[3]) for other in kept):
            kept.append(line)
    return kept


def _closest_on_line(candidates, line, early, late, skip):
    """The note closest to the line for each scale degree within the tolerances, as {degree: (distance, note)}."""
    origin, seconds_per_degree, start_degree = line
    chosen = {}
    for degree, note in candidates:
        if id(note) in skip:
            continue
        predicted_time = origin + (degree - start_degree) * seconds_per_degree
        lateness = (note['start_seconds'] - predicted_time) / abs(seconds_per_degree)
        distance = abs(lateness)
        if -early <= lateness <= late and (degree not in chosen or distance < chosen[degree][0]):
            chosen[degree] = (distance, note)
    return chosen


def _fit_line(members):
    """Least squares through {degree: note}: (time at the first note, seconds per degree signed, that degree)."""
    degrees = sorted(members)
    slope, intercept = np.polyfit(degrees, [members[degree]['start_seconds'] for degree in degrees], 1)
    return intercept + slope * degrees[0], slope, degrees[0]


def _collect_members(notes, scale, seed, taken):
    covered, first_time, last_time, first_degree, last_degree = seed
    candidates = [(scale_degree(note['note'], scale), note) for note in notes if note['note'] % 12 in scale]
    lowest, highest = sorted((first_degree, last_degree))
    line = (first_time, (last_time - first_time) / (last_degree - first_degree), first_degree)
    inside = [item for item in candidates if lowest <= item[0] <= highest
              and first_time - 0.03 <= item[1]['start_seconds'] <= last_time + 0.03]
    chosen = _closest_on_line(inside, line, LINE_TOLERANCE, LINE_TOLERANCE, taken)
    if len(chosen) < 2:
        return None, None
    line = _fit_line({degree: item[1] for degree, item in chosen.items()})

    # Grow past both ends, degree by degree, allowing one missing degree.
    for step in (-1, 1):
        growing = True
        while growing:
            growing = False
            edge = max(chosen) if step > 0 else min(chosen)
            for degree in (edge + step, edge + 2 * step):
                predicted = line[0] + (degree - line[2]) * line[1]
                near = [(abs(note['start_seconds'] - predicted), note) for value, note in candidates
                        if value == degree and id(note) not in taken
                        and abs(note['start_seconds'] - predicted) <= GROW_TOLERANCE * abs(line[1])]
                if near:
                    chosen[degree] = min(near, key=lambda item: item[0])
                    growing = True
                    break

    lowest, highest = min(chosen), max(chosen)
    inside = [item for item in candidates if lowest <= item[0] <= highest]
    for degree, item in _closest_on_line(inside, line, CLAIM_EARLY_TOLERANCE, CLAIM_LATE_TOLERANCE, taken).items():
        chosen.setdefault(degree, item)
    return {degree: item[1] for degree, item in chosen.items()}, line


def _trim_gaps(members):
    """Drop a few notes at either end that sit past a gap the rest of the run does not have."""
    while len(members) > MAX_END_NOTES + 1:
        ordered = sorted(members, key=lambda degree: members[degree]['start_seconds'])
        gaps = [members[b]['start_seconds'] - members[a]['start_seconds'] for a, b in zip(ordered, ordered[1:])]
        limit = MAX_END_GAP_RATIO * statistics.median(gaps)
        for count in range(1, MAX_END_NOTES + 1):
            if gaps[count - 1] > limit:
                for degree in ordered[:count]:
                    del members[degree]
                break
            if gaps[-count] > limit:
                for degree in ordered[-count:]:
                    del members[degree]
                break
        else:
            return


def _repeated_chord_note(note, notes, member_ids, seconds_per_beat):
    """Is this note struck with notes off the line, in a chord also struck shortly before or after?"""
    start = note['start_seconds']
    chord = {other['note'] for other in notes
             if abs(other['start_seconds'] - start) <= REPEAT_GROUP_SECONDS and id(other) not in member_ids}
    if not chord:
        return False
    chord.add(note['note'])
    for other in notes:
        distance = abs(other['start_seconds'] - start)
        if other['note'] != note['note'] or id(other) in member_ids \
                or not REPEAT_GROUP_SECONDS < distance <= REPEAT_BEATS * seconds_per_beat:
            continue
        group = {third['note'] for third in notes
                 if abs(third['start_seconds'] - other['start_seconds']) <= REPEAT_GROUP_SECONDS}
        if len(group & chord) >= REPEAT_MIN_PITCHES:
            return True
    return False


def _drop_chord_ends(members, notes, seconds_per_beat):
    """An end note that belongs to a chord leaves no hole in the run, so it can go back to the chord."""
    while len(members) > 2:
        member_ids = {id(note) for note in members.values()}
        ordered = sorted(members, key=lambda degree: members[degree]['start_seconds'])
        for degree in (ordered[0], ordered[-1]):
            note = members[degree]
            struck_with = sum(1 for other in notes if id(other) not in member_ids
                              and abs(other['start_seconds'] - note['start_seconds']) <= END_CHORD_SECONDS)
            if struck_with >= END_CHORD_NOTES or _repeated_chord_note(note, notes, member_ids, seconds_per_beat):
                del members[degree]
                break
        else:
            return


def detect_runs(midi_file):
    """Glissando runs of a MIDI, in time order; each holds its member notes and what the repair needs."""
    notes = collect_notes(midi_file)
    tempo = next((message.tempo for track in midi_file.tracks for message in track
                  if message.type == 'set_tempo'), 500000)
    seconds_per_beat = tempo / 1000000
    runs = []
    rejected = []
    taken = set()
    occupied = []
    # The scale that covers the most degrees wins, so a major scale run is not read as white keys with stray notes.
    # Scales tie in the order of scale_candidates: a run that fits white keys equally well is a white-key run.
    seeds = [(seed, order, name, scale) for order, (name, scale) in enumerate(scale_candidates())
             for seed in seed_lines(notes, scale)]
    for seed, _, name, scale in sorted(seeds, key=lambda item: (-item[0][0], item[1], item[0][1])):
        direction = 1 if seed[4] > seed[3] else -1
        if any(seed[1] <= end and seed[2] >= start and direction == other for start, end, other in occupied):
            continue
        members, _ = _collect_members(notes, scale, seed, taken)
        if not members:
            continue
        _trim_gaps(members)
        _drop_chord_ends(members, notes, seconds_per_beat)
        pitches = [note['note'] for note in members.values()]
        lowest, highest = min(members), max(members)
        ordered = sorted(members.values(), key=lambda note: note['start_seconds'])
        span = highest - lowest + 1
        duration = ordered[-1]['start_seconds'] - ordered[0]['start_seconds']
        if span < MIN_DEGREES or len(members) < MIN_NOTES or len(members) / span < MIN_COVERAGE or duration <= 0 \
                or duration / (span - 1) > MAX_SECONDS_PER_DEGREE \
                or (min(pitches) + max(pitches)) / 2 < MIN_CENTER_PITCH or max(pitches) < MIN_TOP_PITCH:
            continue
        member_ids = {id(note) for note in members.values()}
        repeated = sum(1 for note in ordered if _repeated_chord_note(note, notes, member_ids, seconds_per_beat))
        summary = dict(start_tick=ordered[0]['start_ticks'], scale=name, repeated=repeated, notes=len(ordered))
        corrected = name in CORRECTED_SCALES or black_with_gaps(scale)
        if repeated / len(ordered) >= MAX_REPEATED_SHARE or not corrected:
            summary['reason'] = 'repeated' if corrected else 'scale'
            rejected.append(summary)
            occupied.append((ordered[0]['start_seconds'], ordered[-1]['start_seconds'], direction))
            continue
        # A black-key run with gap keys is filled with black keys only; the white keys it struck stay in it.
        scale_name, fill_scale = (name, scale) if name in CORRECTED_SCALES else ('black ({})'.format(name), BLACK_KEYS)
        runs.append(dict(
            members=ordered, direction=direction, scale_name=scale_name, scale=fill_scale, degree_scale=scale,
            low_degree=lowest, high_degree=highest, start_seconds=ordered[0]['start_seconds'],
            end_seconds=ordered[-1]['start_seconds'], start_tick=ordered[0]['start_ticks'],
            end_tick=ordered[-1]['start_ticks'], start_pitch=pitches_in_order(ordered, direction)[0],
            repeated=repeated, hand=None))
        taken |= member_ids
        occupied.append((ordered[0]['start_seconds'], ordered[-1]['start_seconds'], direction))
    runs.sort(key=lambda run: run['start_tick'])
    return runs, rejected


def pitches_in_order(notes, direction):
    return sorted((note['note'] for note in notes), reverse=direction < 0)


def extract_runs(input_path, output_path):
    """Write the MIDI without its runs to output_path and return the runs; input_path stays untouched."""
    midi_file = mido.MidiFile(input_path)
    runs, rejected = detect_runs(midi_file)
    removed = {}
    for run in runs:
        for note in run['members']:
            removed.setdefault(note['track_index'], set()).update((note['start_index'], note['end_index']))
    output = mido.MidiFile(type=midi_file.type, ticks_per_beat=midi_file.ticks_per_beat, charset=midi_file.charset,
                           debug=midi_file.debug, clip=midi_file.clip)
    for index, track in enumerate(midi_file.tracks):
        output.tracks.append(rebuild_track_without_messages(track, removed.get(index, set())))
    output.save(output_path)
    return runs, rejected


def assign_hands(runs, midi_path):
    """The hand of each run from its starting pitch against the other notes played during it, in place."""
    notes = collect_notes(mido.MidiFile(midi_path))
    for run in runs:
        others = [note['note'] for note in notes if run['start_tick'] <= note['start_ticks'] <= run['end_tick']]
        boundary = statistics.median(others) if others else SPLIT_PITCH
        run['hand'] = 'right' if run['start_pitch'] >= boundary else 'left'


def split_spans(runs):
    return [(run['start_tick'], run['end_tick'], run['hand']) for run in runs]


def _sounding(notes, channel, pitch, tick):
    return any(note['note'] == pitch and note['start_message'].channel == channel
               and note['start_ticks'] <= tick < note['end_ticks'] for note in notes)


def _sounding_any(notes, pitch, tick):
    return any(note['note'] == pitch and note['start_ticks'] <= tick < note['end_ticks'] for note in notes)


def repair_run(run, ticks_per_beat, existing, fill):
    """The notes to put back: sorted onto the scale, optionally filled, all one length."""
    ordered = run['members']
    pitches = pitches_in_order(ordered, run['direction'])
    ticks = [note['start_ticks'] for note in ordered]
    gaps = [b - a for a, b in zip(ticks, ticks[1:]) if b > a]
    length = round(min(LENGTH_GAP_RATIO * statistics.median(gaps) if gaps else ticks_per_beat / 8,
                       MAX_LENGTH_BEATS * ticks_per_beat))
    length = max(length, 1)
    placed = [dict(tick=tick, pitch=pitch, velocity=note['velocity'], filled=False)
              for tick, pitch, note in zip(ticks, pitches, ordered)]

    filled = []
    held = []
    if fill:
        scale = run['scale']
        result = []
        for current, following in zip(placed, placed[1:]):
            result.append(current)
            low, high = sorted((current['pitch'], following['pitch']))
            missing = [pitch for pitch in range(low + 1, high) if pitch % 12 in scale]
            if current['pitch'] > following['pitch']:
                missing.reverse()
            for position, pitch in enumerate(missing, 1):
                tick = round(current['tick'] + (following['tick'] - current['tick']) * position / (len(missing) + 1))
                if _sounding_any(existing, pitch, tick):
                    held.append(pitch)
                    continue
                velocity = round((current['velocity'] + following['velocity']) / 2)
                result.append(dict(tick=tick, pitch=pitch, velocity=velocity, filled=True))
                filled.append(pitch)
        result.append(placed[-1])
        placed = result
    for note in placed:
        note['end'] = note['tick'] + length
    return placed, filled, held, length


def _note_track(midi_file):
    """The track most notes are on; new notes go there."""
    counts = [sum(1 for message in track if message.type == 'note_on') for track in midi_file.tracks]
    return counts.index(max(counts))


def release_run_hands(midi_file, runs):
    """A hand starting a run has let go of what it held, so those notes end where the run starts.

    Without the run in the MIDI, overlap trimming had nothing to end them at. Returns the shortened notes per run.
    """
    notes = collect_notes(midi_file)
    for note in notes:
        note['new_end'] = note['end_ticks']
    released = []
    for run in runs:
        if not run['hand']:
            released.append([])
            continue
        held = [note for note in notes if note['start_message'].channel == CHANNELS[run['hand']]
                and note['start_ticks'] < run['start_tick'] < note['new_end']]
        for note in held:
            note['new_end'] = run['start_tick']
        released.append(held)
    write_note_ends(midi_file, notes)
    return released


def restore_runs(midi_path, runs, fill=False):
    """Put the repaired runs back into midi_path. Returns one report row per run."""
    midi_file = mido.MidiFile(midi_path)
    released = release_run_hands(midi_file, runs)
    existing = collect_notes(midi_file)
    additions = []
    rows = []
    for run, released_notes in zip(runs, released):
        channel = CHANNELS[run['hand']] if run['hand'] else run['members'][0]['start_message'].channel
        placed, filled, held, length = repair_run(run, midi_file.ticks_per_beat, existing, fill)
        dropped = 0
        written = []
        for note in placed:
            if _sounding(existing, channel, note['pitch'], note['tick']):
                # One key cannot be struck inside its own held note on a channel; the other note stays as it is.
                dropped += 1
                continue
            later = [other['start_ticks'] for other in existing
                     if other['note'] == note['pitch'] and other['start_message'].channel == channel
                     and other['start_ticks'] > note['tick']]
            earlier_written = [other['tick'] for other in written if other['pitch'] == note['pitch']
                               and other['tick'] > note['tick']]
            end = min([note['end']] + later + earlier_written)
            if end <= note['tick']:
                dropped += 1
                continue
            note['end'] = end
            written.append(note)
        for note in written:
            additions.append((note['tick'], 2, mido.Message('note_on', channel=channel, note=note['pitch'],
                                                            velocity=note['velocity'])))
            additions.append((note['end'], 0, mido.Message('note_off', channel=channel, note=note['pitch'], velocity=0)))
        rows.append(dict(run=run, channel=channel, placed=written, filled=filled, held=held, length=length,
                         dropped=dropped, released=released_notes))

    index = _note_track(midi_file)
    events = []
    tick = 0
    for order, message in enumerate(midi_file.tracks[index]):
        tick += message.time
        events.append((tick, 1, order, message))
    last_tick = max([tick] + [item[0] for item in additions])
    events = [(last_tick if message.type == 'end_of_track' else at, 3 if message.type == 'end_of_track' else rank,
               order, message) for at, rank, order, message in events]
    events += [(at, rank, len(events) + position, message) for position, (at, rank, message) in enumerate(additions)]
    events.sort(key=lambda item: item[:3])
    track = mido.MidiTrack()
    previous = 0
    for at, _, _, message in events:
        track.append(message.copy(time=at - previous))
        previous = at
    midi_file.tracks[index] = track
    midi_file.save(midi_path)
    return rows


def _name(pitch):
    return NOTE_NAMES[pitch % 12] + str(pitch // 12 - 1)


def run_changes(rows, midi_path):
    """Changes for the version comparison: a shorter note, or a note that left its pitch, against the transcription."""
    midi_file = mido.MidiFile(midi_path)
    seconds = tick_seconds(midi_file)
    changes = []
    for row in rows:
        kept = {(note['tick'], note['pitch']): note for note in row['placed']}
        for note in row['run']['members']:
            new = kept.get((note['start_ticks'], note['note']))
            if new is None:
                changes.append(dict(kind='deleted', stage='gliss', start=seconds(note['start_ticks']),
                                    end=seconds(note['end_ticks']), pitch=note['note'], velocity=note['velocity']))
            elif new['end'] < note['end_ticks']:
                changes.append(dict(kind='shortened', stage='gliss', start=seconds(new['end']),
                                    end=seconds(note['end_ticks']), pitch=note['note'], velocity=note['velocity']))
        for note in row['released']:
            changes.append(dict(kind='shortened', stage='gliss', start=seconds(note['new_end']),
                                end=seconds(note['end_ticks']), pitch=note['note'], velocity=note['velocity']))
    return changes


def run_records(rows, midi_path):
    """What each run is, for a later score step (DuET's --glissandi reads this): seconds are of the final MIDI."""
    midi_file = mido.MidiFile(midi_path)
    seconds = tick_seconds(midi_file)
    return [dict(start_tick=row['run']['start_tick'], end_tick=row['run']['end_tick'],
                 direction='up' if row['run']['direction'] > 0 else 'down', scale=row['run']['scale_name'],
                 hand=row['run']['hand'], ticks_per_beat=midi_file.ticks_per_beat,
                 notes=[dict(tick=note['tick'], seconds=seconds(note['tick']), pitch=note['pitch'],
                             end_tick=note['end'], filled=note['filled'])
                        for note in row['placed']])
            for row in rows]


def records_path(midi_path):
    return Path(midi_path).with_suffix('.gliss.json')


def save_records(midi_path, records):
    records_path(midi_path).write_text(json.dumps(dict(version=1, runs=records), ensure_ascii=True), encoding='utf-8')


def remove_records(midi_path):
    """A run without glissandi leaves no record that describes an earlier output."""
    path = records_path(midi_path)
    if path.exists():
        path.unlink()


def format_gliss_stats(midi_path, rows, rejected, fill, ticks_per_beat):
    bar_ticks = 4 * ticks_per_beat
    lines = ['MIDI glissando stats', 'MIDI: {}'.format(midi_path),
             'Runs: {}, rejected as repeated chords: {}, left as major scales: {}'.format(
                 len(rows), sum(item['reason'] == 'repeated' for item in rejected),
                 sum(item['reason'] == 'scale' for item in rejected)),
             'Fill missing scale notes: {}'.format('On' if fill else 'Off')]
    for row in rows:
        run = row['run']
        pitches = [note['pitch'] for note in row['placed']]
        start, end = (pitches[0], pitches[-1]) if pitches else (run['start_pitch'], run['start_pitch'])
        line = '- m{} beat {:.2f}: {} {} to {}, {}, {} hand, {} notes, length {:.3f} beat'.format(
            run['start_tick'] // bar_ticks + 1, (run['start_tick'] % bar_ticks) / ticks_per_beat + 1,
            'up' if run['direction'] > 0 else 'down', _name(start), _name(end), run['scale_name'],
            run['hand'] or 'source', len(pitches), row['length'] / ticks_per_beat)
        details = []
        if row['filled']:
            details.append('filled ' + ' '.join(_name(pitch) for pitch in row['filled']))
        if row['held']:
            details.append('not filled, key held: ' + ' '.join(_name(pitch) for pitch in row['held']))
        if row['dropped']:
            details.append('{} inside a held note, not written'.format(row['dropped']))
        if row['released']:
            details.append('released at the start: ' + ' '.join(_name(note['note']) for note in row['released']))
        lines.append(line + ('; ' + '; '.join(details) if details else ''))
    for item in rejected:
        if item['reason'] == 'scale':
            lines.append('- m{}: left as it is, {} notes on {}, not one key colour'.format(
                item['start_tick'] // bar_ticks + 1, item['notes'], item['scale']))
        else:
            lines.append('- m{}: rejected, {} of {} notes are in a repeated chord'.format(
                item['start_tick'] // bar_ticks + 1, item['repeated'], item['notes']))
    return '\n'.join(lines)
