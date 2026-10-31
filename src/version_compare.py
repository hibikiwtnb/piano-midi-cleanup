"""Find the versions of one song and compare any two of them note by note for the roll.

A song's versions are the original MIDI, song_bpmfix.mid, song_bpmfix_cleaned.mid
and, when compare_answer.py has recorded one, the answer MIDI. bpmfix and cleaned
share one timeline; the original differs from it by the constant first-beat shift
(BPM correction keeps seconds), and the answer by the recorded straight-line
alignment. Both sides are mapped onto the bpmfix timeline, compared, and the
result is mapped back onto the shown file's timeline.

Notes match by compare_answer's rule (same pitch, onset within 50 ms, one-to-one).
Unmatched shown notes are extra, unmatched reference notes are missed, and matched
pairs whose ends differ by more than TAIL_TOLERANCE are shortened or lengthened.
Comparing cleaned with bpmfix uses the processing record instead when it is
current, so each difference keeps the stage that made it.
"""

from dataclasses import dataclass
import os

from compare_answer import load_record, match, read_notes
from note_changes import load_changes

TAIL_TOLERANCE = 0.03
ROLES = ('original', 'bpmfix', 'cleaned', 'answer', 'other')
TIMELINE_ROLES = ('bpmfix', 'cleaned', 'other')
SUFFIXES = {'bpmfix': '_bpmfix', 'cleaned': '_bpmfix_cleaned'}
# Preferred comparison for each shown version when the user has not picked one.
DEFAULT_REFERENCES = {'cleaned': ('bpmfix', 'answer'), 'bpmfix': ('answer',), 'other': ('answer',)}
KINDS = ('extra', 'missed', 'shortened', 'lengthened')


@dataclass(frozen=True)
class Version:
    role: str
    path: str


def song_versions(midi_path):
    """The song's versions that exist, in processing order, including the shown file."""
    base, ext = os.path.splitext(midi_path)
    for role in ('cleaned', 'bpmfix'):
        if base.endswith(SUFFIXES[role]):
            base = base[:-len(SUFFIXES[role])]
            break
    versions = [Version(role, path) for role, path in (
        ('original', base + ext), ('bpmfix', base + SUFFIXES['bpmfix'] + ext),
        ('cleaned', base + SUFFIXES['cleaned'] + ext)) if os.path.isfile(path)]
    answer = answer_alignment(versions)
    if answer is not None:
        versions.append(Version('answer', answer[0]))
    if not any(os.path.samefile(version.path, midi_path) for version in versions):
        versions.append(Version('other', midi_path))
    return versions


def answer_alignment(versions):
    """(answer path, offset, rate, window) from a current answer record on the bpmfix timeline."""
    for version in reversed(versions):
        if version.role not in TIMELINE_ROLES:
            continue
        record = load_record(version.path)
        if record is not None and os.path.isfile(record['answer']):
            alignment = record['alignment']
            return record['answer'], alignment['offset'], alignment['rate'], tuple(record['window'])
    return None


def default_reference(versions, shown_role, preferred=None):
    """The user's preferred role when available, else the usual comparison for the shown version."""
    roles = {version.role for version in versions} - {shown_role}
    if preferred == 'none':
        return None
    if preferred in roles:
        return preferred
    return next((role for role in DEFAULT_REFERENCES.get(shown_role, ()) if role in roles), None)


def timeline_maps(versions):
    """Per role, (offset, rate) mapping its seconds onto the bpmfix timeline: t' = offset + rate * t."""
    paths = {version.role: version.path for version in versions}
    maps = {role: (0.0, 1.0) for role in paths if role in TIMELINE_ROLES}
    if 'original' in paths:
        # BPM correction keeps seconds and shifts every note equally, so the earliest notes line up.
        shifted = paths.get('bpmfix') or paths.get('cleaned')
        if shifted is not None:
            original, corrected = read_notes(paths['original'])[0], read_notes(shifted)[0]
            if original and corrected:
                maps['original'] = (corrected[0]['start'] - original[0]['start'], 1.0)
        maps.setdefault('original', (0.0, 1.0))
    answer = answer_alignment(versions)
    if answer is not None:
        maps['answer'] = (answer[1], answer[2])
    return maps


def mapped(notes, offset, rate):
    return [dict(note, start=offset + rate * note['start'], end=offset + rate * note['end']) for note in notes]


def diff_notes(shown, reference):
    """Extra, missed and tail differences of shown against reference on one timeline."""
    pairs = match(reference, shown)
    hit_reference = {i for i, _ in pairs}
    hit_shown = {j for _, j in pairs}
    items = [dict(kind='extra', start=note['start'], end=note['end'], pitch=note['pitch'],
                  velocity=note['velocity']) for j, note in enumerate(shown) if j not in hit_shown]
    items += [dict(kind='missed', start=note['start'], end=note['end'], pitch=note['pitch'],
                   velocity=note['velocity']) for i, note in enumerate(reference) if i not in hit_reference]
    for i, j in pairs:
        before, after = reference[i], shown[j]
        if after['end'] < before['end'] - TAIL_TOLERANCE:
            items.append(dict(kind='shortened', start=after['end'], end=before['end'], pitch=after['pitch'],
                              velocity=after['velocity']))
        elif after['end'] > before['end'] + TAIL_TOLERANCE:
            items.append(dict(kind='lengthened', start=before['end'], end=after['end'], pitch=after['pitch'],
                              velocity=after['velocity']))
    items.sort(key=lambda item: (item['start'], item['pitch']))
    return items


def processing_items(cleaned_path):
    """The processing record as comparison items against bpmfix, or None when it is stale."""
    changes = load_changes(cleaned_path)
    if changes is None:
        return None
    return [dict(change, kind='missed' if change['kind'] == 'deleted' else 'shortened') for change in changes]


def compare_versions(versions, shown_role, reference_role):
    """Comparison items on the shown file's timeline."""
    paths = {version.role: version.path for version in versions}
    if shown_role == 'cleaned' and reference_role == 'bpmfix':
        items = processing_items(paths['cleaned'])
        if items is not None:
            return items
    maps = timeline_maps(versions)
    shown_offset, shown_rate = maps.get(shown_role, (0.0, 1.0))
    reference_offset, reference_rate = maps.get(reference_role, (0.0, 1.0))
    shown = mapped(read_notes(paths[shown_role])[0], shown_offset, shown_rate)
    reference = mapped(read_notes(paths[reference_role])[0], reference_offset, reference_rate)
    if 'answer' in (shown_role, reference_role):
        # An answer may cover only an excerpt; judge the other side inside the answer's window only.
        start, end = answer_alignment(versions)[3]
        if shown_role == 'answer':
            reference = [note for note in reference if start <= note['start'] < end]
        else:
            shown = [note for note in shown if start <= note['start'] < end]
    items = diff_notes(shown, reference)
    # Back onto the shown file's own seconds.
    return [dict(item, start=(item['start'] - shown_offset) / shown_rate,
                 end=(item['end'] - shown_offset) / shown_rate) for item in items]


def kind_counts(items):
    counts = dict.fromkeys(KINDS, 0)
    for item in items:
        counts[item['kind']] += 1
    return counts


def stage_counts(cleaned_path):
    """Deleted and shortened notes per processing stage, or None without a current record."""
    changes = load_changes(cleaned_path)
    if changes is None:
        return None
    counts = {}
    for change in changes:
        stage = counts.setdefault(change['stage'], {'deleted': 0, 'shortened': 0})
        stage[change['kind']] += 1
    return counts

