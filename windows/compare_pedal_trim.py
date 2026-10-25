"""Rebuild local MIDI outputs and compare legacy and pedal-aware trimming."""

import contextlib
import hashlib
import io
import json
import math
import shutil
import subprocess
import sys
import types
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_REVISION = '3abc825397427cd60bab231cb6dc5d272c9b68fc'
sys.path.insert(0, str(ROOT / 'src'))

import mido
import hand_split_channels as split
import postprocess
import trim_overlaps as trim
from clean_midi_rules import tick_seconds
from midi_stats import source_stats_path_for
from report_utils import update_report


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def note_map(path):
    midi = mido.MidiFile(path)
    tempo = split.collect_tempo_changes(midi)
    occurrences = Counter()
    result = {}
    for note in split.collect_notes(midi):
        key = (note['track_index'], note['start_ticks'], note['note'],
               note['start_message'].channel, note['velocity'])
        occurrences[key] += 1
        result[key + (occurrences[key],)] = {
            'end': note['end_ticks'],
            'end_s': split.tick_to_second(note['end_ticks'], midi.ticks_per_beat, tempo),
        }
    return result


def pedal_events(path):
    events = []
    for track in mido.MidiFile(path).tracks:
        tick = 0
        for message in track:
            tick += message.time
            if message.type == 'control_change' and message.control == 64:
                events.append((tick, message.channel, message.value))
    return sorted(events)


def compare(before_path, after_path):
    before, after = note_map(before_path), note_map(after_path)
    if before.keys() != after.keys():
        raise AssertionError('Note onsets, pitches, velocities, or hands changed: {}'.format(after_path))
    changed = []
    shorter = longer = 0
    seconds = 0.0
    for key, old in before.items():
        new = after[key]
        if old['end'] == new['end']:
            continue
        delta = new['end_s'] - old['end_s']
        shorter += delta < 0
        longer += delta > 0
        seconds += abs(delta)
        changed.append({'track': key[0], 'start_tick': key[1], 'pitch': key[2],
                        'channel': key[3], 'old_end_tick': old['end'],
                        'new_end_tick': new['end'], 'delta_ms': round(delta * 1000, 3)})
    return {'notes': len(before), 'changed': len(changed), 'shorter': shorter, 'longer': longer,
            'sum_absolute_duration_change_s': round(seconds, 3),
            'mean_changed_duration_ms': round(seconds * 1000 / len(changed), 3) if changed else 0,
            'details': changed}


def sustain_end_changes(comparison, path, tolerance=0.0):
    """Count damping times under binary MIDI sustain that move earlier by more than tolerance seconds;
    not acoustic differences."""
    pedals = pedal_events(path)
    seconds = tick_seconds(mido.MidiFile(path))

    def sustain_end(end, channel):
        down = False
        for tick, event_channel, value in pedals:
            if event_channel != channel:
                continue
            if tick <= end:
                down = value >= 64
            elif down and value < 64:
                return tick
            elif not down:
                return end
        return None if down else end

    def shift(item):
        old = sustain_end(item['old_end_tick'], item['channel'])
        new = sustain_end(item['new_end_tick'], item['channel'])
        if old == new:
            return 0.0
        return math.inf if old is None or new is None else seconds(old) - seconds(new)

    return sum(shift(item) > tolerance for item in comparison['details'])


def arguments(path, settings):
    preset = settings['last']
    argv = [str(path), '--hands', str(preset['hands'])]
    for name, flag in (('clean', '--no-clean'), ('split', '--no-split'), ('trim', '--no-trim')):
        if not preset['switches'][name]:
            argv.append(flag)
    for name, flag in (('semitone_pairs', '--semitone-pairs'), ('playable', '--playable')):
        if preset['switches'][name]:
            argv.append(flag)
    argv += ['--fast-figure-hands'] + preset['fast_figure_hands']
    for key, value in preset['parameters'].items():
        argv += ['--set', '{}={}'.format(key, value)]
    args = postprocess.build_parser().parse_args(argv)
    return args


def main():
    settings = json.loads((Path.home() / '.piano-midi-cleanup-panel.json').read_text(encoding='utf-8'))
    if settings['source'] != 'reuse' or not settings['last']['switches']['trim']:
        raise ValueError('This comparison requires the saved reuse-BPM and overlap-trim settings.')
    data_root = ROOT.parent / 'piano-midi-cleanup-data'
    sources = sorted(data_root.glob('*_bpmfix.mid'))
    if not sources:
        raise ValueError('No BPM-fixed MIDI files found.')
    output = data_root / ('pedal-trim-comparison-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    output.mkdir()
    old_source = subprocess.run(['git', 'show', LEGACY_REVISION + ':src/trim_overlaps.py'], cwd=ROOT,
                                check=True, capture_output=True, encoding='utf-8').stdout
    if 'class PedalGuard' in old_source:
        raise ValueError('The selected legacy revision already includes the pedal guard.')
    (output / 'legacy_trim_overlaps.py').write_text(old_source, encoding='utf-8')
    legacy = types.ModuleType('legacy_trim_overlaps')
    exec(compile(old_source, str(output / 'legacy_trim_overlaps.py'), 'exec'), legacy.__dict__)
    for name in ('FAST_FIGURE_BEATS', 'FAST_FIGURE_MIN_ONSETS', 'HARMONY_MIN_PITCH_CLASSES',
                 'OUTER_STEP_SEMITONES', 'MAX_FINGERS', 'HARD_SPAN'):
        parameter = {'FAST_FIGURE_BEATS': 'trim.fast_figure_beats',
                     'FAST_FIGURE_MIN_ONSETS': 'trim.fast_figure_onsets',
                     'HARMONY_MIN_PITCH_CLASSES': 'trim.harmony_pitch_classes',
                     'OUTER_STEP_SEMITONES': 'trim.outer_step', 'MAX_FINGERS': 'trim.max_fingers',
                     'HARD_SPAN': 'trim.max_span'}[name]
        setattr(legacy, name, postprocess.PARAMETER_BY_KEY[parameter].kind(settings['last']['parameters'][parameter]))
    source_hashes = {str(path): digest(path) for path in sources}
    real_trim = postprocess.apply_overlap_trim
    real_mapping = split.map_pedal_channels
    rows = []
    for number, source in enumerate(sources, 1):
        song_dir = output / '{:02d}'.format(number)
        song_dir.mkdir()
        base = Path(postprocess.source_midi_path(str(source))).stem
        report = Path(source_stats_path_for(str(source)))
        destination = source.with_name(base + '_bpmfix_cleaned.mid')
        for label, path in (('previous_cleaned.mid', destination), ('previous_stats.txt', report)):
            if path.exists():
                shutil.copy2(path, song_dir / label)
        paths = {}
        new_stats = {}
        for mode in ('legacy', 'latest'):
            folder = song_dir / mode
            folder.mkdir()
            copied = folder / source.name
            shutil.copy2(source, copied)
            if report.exists():
                shutil.copy2(report, Path(source_stats_path_for(str(copied))))
            def trim_step(midi_path, report_path=None, fast_figure_hands=trim.DEFAULT_FAST_FIGURE_HANDS,
                          protect_pedal=True):
                before = song_dir / (mode + '_before_trim.mid')
                shutil.copy2(midi_path, before)
                if mode == 'legacy':
                    stats = legacy.trim_overlaps_in_place(midi_path, fast_figure_hands=fast_figure_hands)
                    update_report(report_path, legacy.format_trim_stats(stats))
                    return stats
                old_trim_path = song_dir / 'mapped_legacy_trim.mid'
                shutil.copy2(midi_path, old_trim_path)
                legacy.trim_overlaps_in_place(str(old_trim_path), fast_figure_hands=fast_figure_hands)
                stats = real_trim(midi_path, report_path=report_path, fast_figure_hands=fast_figure_hands)
                new_stats.update(stats)
                return stats
            split.map_pedal_channels = (lambda midi, notes: None) if mode == 'legacy' else real_mapping
            postprocess.apply_overlap_trim = trim_step
            buffer = io.StringIO()
            try:
                with contextlib.redirect_stdout(buffer):
                    postprocess.run_postprocess(arguments(copied, settings))
            finally:
                split.map_pedal_channels = real_mapping
                postprocess.apply_overlap_trim = real_trim
                (folder / 'run.log').write_text(buffer.getvalue(), encoding='utf-8')
            paths[mode] = copied.with_name(base + '_bpmfix_cleaned.mid')
        original = song_dir / 'latest_before_trim.mid'
        old_output = song_dir / 'mapped_legacy_trim.mid'
        row = {'file': source.name,
               'legacy_trim': compare(original, old_output),
               'latest_trim': compare(original, paths['latest']),
               'trim_logic_difference': compare(old_output, paths['latest']),
               'full_pipeline_difference': compare(paths['legacy'], paths['latest']),
               'pedal_events_legacy': len(pedal_events(paths['legacy'])),
               'pedal_events_latest': len(pedal_events(paths['latest'])),
               'pedal_unchanged_during_trim': pedal_events(original) == pedal_events(paths['latest']),
               'sustain_kept': new_stats['sustain_kept'], 'uncertain_kept': new_stats['uncertain_kept'],
               'peak_keys_before': new_stats['peaks_before'], 'peak_keys_after': new_stats['peaks_after']}
        for label in ('legacy_trim', 'latest_trim'):
            row[label]['changed_basic_sustain_ends'] = sustain_end_changes(row[label], original)
        if not row['pedal_unchanged_during_trim']:
            raise AssertionError('Trimming changed pedal events.')
        if sustain_end_changes(row['latest_trim'], original, trim.SUSTAIN_TOLERANCE_SECONDS):
            raise AssertionError('A guarded trim ended the estimated MIDI sustain beyond the tolerance.')
        rows.append(row)
        (song_dir / 'comparison.json').write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'completed': number, 'file': source.name,
                          'legacy_trimmed': row['legacy_trim']['changed'],
                          'latest_trimmed': row['latest_trim']['changed'],
                          'different': row['trim_logic_difference']['changed']}, ensure_ascii=False), flush=True)
    if any(digest(Path(path)) != value for path, value in source_hashes.items()):
        raise AssertionError('A BPM-fixed source changed.')
    for number, (source, row) in enumerate(zip(sources, rows), 1):
        base = Path(postprocess.source_midi_path(str(source))).stem
        folder = output / '{:02d}'.format(number) / 'latest'
        staged_midi = folder / (base + '_bpmfix_cleaned.mid')
        staged_report = Path(source_stats_path_for(str(staged_midi)))
        destination = source.with_name(staged_midi.name)
        # The audit log retains isolated paths; the published report names real files.
        report_text = staged_report.read_text(encoding='utf-8-sig')
        report_text = report_text.replace(str(folder), str(source.parent))
        update_report(source_stats_path_for(str(source)), report_text, replace=True)
        shutil.copy2(staged_midi, destination)
        row['published'] = True
    totals = {label: sum(row[label]['changed'] for row in rows)
              for label in ('legacy_trim', 'latest_trim', 'trim_logic_difference')}
    (output / 'summary.json').write_text(json.dumps({'settings': settings, 'source_sha256': source_hashes,
        'totals': totals,
        'legacy_revision': LEGACY_REVISION, 'results': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Pedal-aware trim comparison', '',
             'Same BPM-fixed inputs and saved GUI settings. No audio transcription or rendering.', '',
             '| File | Notes | Legacy trimmed | Latest trimmed | Different ends | Sustain kept | Uncertain kept | Published |',
             '|---|---:|---:|---:|---:|---:|---:|---|']
    for row in rows:
        lines.append('| {} | {} | {} | {} | {} | {} | {} | {} |'.format(
            row['file'], row['latest_trim']['notes'], row['legacy_trim']['changed'],
            row['latest_trim']['changed'], row['trim_logic_difference']['changed'],
            row['sustain_kept'], row['uncertain_kept'], row['published']))
    lines += ['', 'Legacy trim is also run on the newly mapped input to isolate the trim logic.',
              'Full legacy outputs retain the previous missing pedal-channel mapping.',
              'Previous outputs, reports, per-note differences and complete run logs are retained.',
              'No listening comparison was performed. Duration changes are not perceptual scores.']
    (output / 'comparison.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('COMPARISON_DIR=' + str(output), flush=True)


if __name__ == '__main__':
    main()
