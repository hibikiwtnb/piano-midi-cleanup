"""Compare transcribed MIDIs with an answer MIDI note by note and record the errors for the roll.

Rule: same pitch and onset within 50 ms is a hit, matched one-to-one. Every
model note in the answer's time window without a match is an extra note; every
answer note without a match is a missed note. Duration, velocity and pedal are
not judged.

The answer may run on its own clock (for example a score excerpt starting at 0).
It is mapped onto the models' timeline with one straight line,
model = offset + rate * answer, fitted to the pooled onsets of all given models
so the line cannot bend toward any one of them; or pass --offset/--rate.
Each model gets <model>.answer.json beside it, read by the roll and by
pianoroll_video.py. All models must share one timeline (same audio, same
post-processing shift).

    python compare_answer.py answer.mid model_a_bpmfix_cleaned.mid model_b_bpmfix_cleaned.mid
"""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import mido
import numpy as np
from scipy.optimize import linear_sum_assignment

from clean_midi_rules import tick_seconds
from hand_split_channels import collect_notes

TOLERANCE = 0.05
SEARCH_TOLERANCE = 0.08
RATES = np.arange(0.97, 1.0301, 0.0025)


def answer_path(midi_path):
    return Path(midi_path).with_suffix('.answer.json')


def fingerprint(midi_path):
    return hashlib.sha256(Path(midi_path).read_bytes()).hexdigest()


def read_notes(midi_path):
    midi = mido.MidiFile(midi_path)
    seconds = tick_seconds(midi)
    notes = [dict(start=seconds(note['start_ticks']), end=seconds(note['end_ticks']), pitch=note['note'],
                  velocity=note['velocity']) for note in collect_notes(midi)]
    notes.sort(key=lambda note: (note['start'], note['pitch']))
    return notes, midi.length


def count_hits(answer, pooled, offset, rate, tolerance=TOLERANCE):
    hits = 0
    for pitch, times in answer.items():
        model = pooled.get(pitch)
        if model is None:
            continue
        mapped = offset + rate * times
        index = np.searchsorted(model, mapped)
        before = np.abs(mapped - model[np.clip(index - 1, 0, len(model) - 1)])
        after = np.abs(mapped - model[np.clip(index, 0, len(model) - 1)])
        hits += int(np.sum(np.minimum(before, after) <= tolerance))
    return hits


def fit_alignment(answer_notes, model_notes):
    """Grid search over rate and offset, then least squares on pairs within 80 ms (twice)."""
    answer = defaultdict(list)
    for note in answer_notes:
        answer[note['pitch']].append(note['start'])
    answer = {pitch: np.array(times) for pitch, times in answer.items()}
    pooled = defaultdict(list)
    for notes in model_notes:
        for note in notes:
            pooled[note['pitch']].append(note['start'])
    pooled = {pitch: np.sort(np.array(times)) for pitch, times in pooled.items()}
    end = max((note['start'] for notes in model_notes for note in notes), default=0.0)

    best = (-1, 0.0, 1.0)
    for rate in RATES:
        for offset in np.arange(-answer_notes[0]['start'], end, 0.01):
            hits = count_hits(answer, pooled, offset, rate, SEARCH_TOLERANCE)
            if hits > best[0]:
                best = (hits, offset, rate)
    _, offset, rate = best
    for _ in range(2):
        xs, ys = [], []
        for pitch, times in answer.items():
            model = pooled.get(pitch)
            if model is None:
                continue
            for time in times:
                mapped = offset + rate * time
                nearest = model[np.argmin(np.abs(model - mapped))]
                if abs(nearest - mapped) <= SEARCH_TOLERANCE:
                    xs.append(time)
                    ys.append(nearest)
        if len(xs) >= 4:
            rate, offset = np.polyfit(xs, ys, 1)
    return float(offset), float(rate), count_hits(answer, pooled, offset, rate)


def match(answer, model):
    """One-to-one same-pitch pairs (answer index, model index) with onsets within the tolerance."""
    pairs = []
    answer_by_pitch, model_by_pitch = defaultdict(list), defaultdict(list)
    for index, note in enumerate(answer):
        answer_by_pitch[note['pitch']].append(index)
    for index, note in enumerate(model):
        model_by_pitch[note['pitch']].append(index)
    for pitch, answer_indices in answer_by_pitch.items():
        model_indices = model_by_pitch.get(pitch)
        if not model_indices:
            continue
        cost = np.array([[abs(answer[i]['start'] - model[j]['start']) for j in model_indices]
                         for i in answer_indices])
        too_far = cost > TOLERANCE
        cost[too_far] = 1e6
        rows, columns = linear_sum_assignment(cost)
        pairs += [(answer_indices[r], model_indices[c]) for r, c in zip(rows, columns) if not too_far[r, c]]
    return pairs


def compare(answer_midi, model_midis, offset=None, rate=1.0):
    answer, answer_length = read_notes(answer_midi)
    models = {path: read_notes(path)[0] for path in model_midis}
    pooled_hits = None
    if offset is None:
        offset, rate, pooled_hits = fit_alignment(answer, list(models.values()))
    mapped = [dict(note, start=offset + rate * note['start'], end=offset + rate * note['end']) for note in answer]
    window = (offset - TOLERANCE, offset + rate * answer_length - TOLERANCE)
    records = {}
    for path, notes in models.items():
        in_window = [note for note in notes if window[0] <= note['start'] < window[1]]
        pairs = match(mapped, in_window)
        hit_answer = {i for i, _ in pairs}
        hit_model = {j for _, j in pairs}
        items = [dict(kind='extra', **note) for j, note in enumerate(in_window) if j not in hit_model]
        items += [dict(kind='missed', **note) for i, note in enumerate(mapped) if i not in hit_answer]
        items.sort(key=lambda item: (item['start'], item['pitch']))
        records[path] = dict(
            version=1, midi_sha256=fingerprint(path), answer=str(Path(answer_midi).resolve()),
            rule='same pitch, onset within 50 ms, one-to-one; extra +1, missed +1',
            alignment=dict(offset=round(offset, 4), rate=round(rate, 6), pooled_hits=pooled_hits),
            window=[round(window[0], 4), round(window[1], 4)],
            counts=dict(answer=len(mapped), hits=len(pairs), extra=len(in_window) - len(hit_model),
                        missed=len(mapped) - len(hit_answer)),
            items=[dict(item, start=round(item['start'], 4), end=round(item['end'], 4)) for item in items])
    return records


def save_record(midi_path, record):
    destination = answer_path(midi_path)
    temporary = destination.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding='utf-8')
    temporary.replace(destination)


def load_record(midi_path):
    """Reject stale records rather than marking errors on a different MIDI."""
    try:
        record = json.loads(answer_path(midi_path).read_text(encoding='utf-8'))
        if record['version'] == 1 and record['midi_sha256'] == fingerprint(midi_path) and all(
                item['kind'] in ('extra', 'missed') and 0 <= item['pitch'] <= 127
                and item['start'] <= item['end'] for item in record['items']):
            return record
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('answer', help='answer MIDI')
    parser.add_argument('models', nargs='+', help='transcribed MIDIs sharing one timeline')
    parser.add_argument('--offset', type=float, help='model seconds of answer 0 s (skips the fit)')
    parser.add_argument('--rate', type=float, default=1.0, help='model seconds per answer second (with --offset)')
    args = parser.parse_args(argv)
    records = compare(args.answer, args.models, args.offset, args.rate)
    first = next(iter(records.values()))
    print('alignment: offset {offset:.3f} s, rate {rate:.4f}, pooled hits {pooled_hits}'.format(**first['alignment']))
    print('window {:.2f}-{:.2f} s, answer notes {}'.format(*first['window'], first['counts']['answer']))
    for path, record in records.items():
        save_record(path, record)
        counts = record['counts']
        print('{}: hit {hits}  extra {extra}  missed {missed}  errors {}'.format(
            Path(path).name, counts['extra'] + counts['missed'], **counts))


if __name__ == '__main__':
    main()
