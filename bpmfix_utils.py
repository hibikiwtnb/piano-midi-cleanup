import json
import os
import subprocess
from pathlib import Path

import mido


DEFAULT_BPM_ENV = Path.home() / '.local' / 'share' / 'pianotrans-tools-env'
BPM_PYTHON = os.environ.get(
    'PIANOTRANS_BPM_PYTHON',
    str(Path(os.environ.get('PIANOTRANS_BPM_ENV', DEFAULT_BPM_ENV)).expanduser() / 'bin' / 'python3'),
)
ORIGINAL_BPM = 120.0
ROUND_MAX_END_DRIFT_MS = 50.0


def bpmfix_path_for(midi_path):
    filename, ext = os.path.splitext(midi_path)
    return filename + '_bpmfix' + ext


def bpmfix_report_path_for(midi_path):
    filename, _ = os.path.splitext(midi_path)
    return filename + '_stats.txt'


def detect_bpm(audio_path):
    if not os.path.exists(BPM_PYTHON):
        raise RuntimeError('BPM environment not found: {}'.format(BPM_PYTHON))

    code = r'''
import json
import sys
import numpy as np
import essentia.standard as es

audio_path = sys.argv[1]
sample_rate = 44100
audio = es.MonoLoader(filename=audio_path, sampleRate=sample_rate)()
extractor = es.RhythmExtractor2013(method='multifeature')
bpm, beats, confidence, estimates, bpm_intervals = extractor(audio)

# Phase coherence at half-beat resolution tolerates off-beat jumps in the beat track.
candidates = np.arange(bpm - 1.0, bpm + 1.0, 0.0005)
phases = np.exp(4j * np.pi * np.outer(candidates / 60.0, np.asarray(beats)))
coherence = np.abs(phases.mean(axis=1))
best = int(coherence.argmax())

print(json.dumps({
    'raw_bpm': float(bpm),
    'phase_bpm': float(candidates[best]),
    'phase_coherence': float(coherence[best]),
    'confidence': float(confidence),
    'beats': len(beats),
    'duration': len(audio) / sample_rate,
}))
'''
    result = subprocess.run(
        [BPM_PYTHON, '-c', code, audio_path],
        check=True,
        capture_output=True,
        text=True,
    )
    bpm_info = json.loads(result.stdout)
    bpm_info.update(choose_applied_bpm(bpm_info['phase_bpm'], bpm_info['duration']))
    return bpm_info


def end_drift_ms(duration, true_bpm, applied_bpm):
    return abs(true_bpm / applied_bpm - 1) * duration * 1000


def choose_applied_bpm(phase_bpm, duration):
    rounded_bpm = float(round(phase_bpm))
    drift = end_drift_ms(duration, phase_bpm, rounded_bpm)
    if drift <= ROUND_MAX_END_DRIFT_MS:
        return {'bpm': rounded_bpm, 'end_drift_ms': drift, 'rounded': True}

    applied_bpm = round(phase_bpm, 2)
    return {
        'bpm': applied_bpm,
        'end_drift_ms': end_drift_ms(duration, phase_bpm, applied_bpm),
        'rounded': False,
    }


def bpm_info_from_manual_bpm(bpm):
    bpm = float(bpm)
    return {
        'bpm': round(bpm, 2),
        'raw_bpm': None,
        'phase_bpm': None,
        'phase_coherence': None,
        'confidence': None,
        'beats': None,
        'duration': None,
        'end_drift_ms': None,
        'rounded': None,
        'source': 'manual',
    }


def fix_midi_bpm(midi_path, output_path, target_bpm):
    midi_file = mido.MidiFile(midi_path)
    source_tempo = int(mido.bpm2tempo(ORIGINAL_BPM))
    target_tempo = int(mido.bpm2tempo(target_bpm))

    for track in midi_file.tracks:
        source_absolute_tick = 0
        scaled_previous_tick = 0
        for message in track:
            source_absolute_tick += message.time
            scaled_absolute_tick = (
                source_absolute_tick * source_tempo + target_tempo // 2
            ) // target_tempo
            message.time = scaled_absolute_tick - scaled_previous_tick
            scaled_previous_tick = scaled_absolute_tick
            if message.type == 'set_tempo':
                message.tempo = target_tempo

    midi_file.save(output_path)


def format_bpmfix_stats(audio_path, midi_path, output_path, bpm_info):
    confidence = bpm_info.get('confidence')
    beats = bpm_info.get('beats')
    source = bpm_info.get('source', 'detected')
    raw_bpm = bpm_info.get('raw_bpm')
    phase_bpm = bpm_info.get('phase_bpm')
    phase_coherence = bpm_info.get('phase_coherence')
    drift = bpm_info.get('end_drift_ms')
    rounded = bpm_info.get('rounded')
    if rounded is None:
        rounding = 'N/A (manual BPM)'
    elif rounded:
        rounding = 'applied (end drift <= {:.0f} ms)'.format(ROUND_MAX_END_DRIFT_MS)
    else:
        rounding = 'skipped (rounded BPM end drift > {:.0f} ms)'.format(ROUND_MAX_END_DRIFT_MS)

    return '\n'.join([
        'MIDI BPMFix stats',
        'Audio: {}'.format(audio_path or 'N/A'),
        'Input MIDI: {}'.format(midi_path),
        'Output MIDI: {}'.format(output_path),
        'Original BPM: {:.2f}'.format(ORIGINAL_BPM),
        'BPM source: {}'.format(source),
        'Raw detected BPM: {}'.format('N/A' if raw_bpm is None else '{:.6f}'.format(raw_bpm)),
        'Beat-phase BPM: {}'.format('N/A' if phase_bpm is None else '{:.4f}'.format(phase_bpm)),
        'Beat-phase coherence: {}'.format('N/A' if phase_coherence is None else '{:.3f}'.format(phase_coherence)),
        'Rounding: {}'.format(rounding),
        'Applied BPM: {bpm:.2f}'.format(**bpm_info),
        'Estimated end drift: {}'.format('N/A (manual BPM)' if drift is None else '{:.0f} ms'.format(drift)),
        'Detection confidence: {}'.format('N/A (manual BPM)' if confidence is None else '{:.3f}'.format(confidence)),
        'Detected beats: {}'.format('N/A (manual BPM)' if beats is None else beats),
    ])


def write_bpmfix_report(audio_path, midi_path, output_path, bpm_info, report_path=None, append=False):
    if report_path is None:
        report_path = bpmfix_report_path_for(midi_path)

    report = format_bpmfix_stats(audio_path, midi_path, output_path, bpm_info)
    mode = 'a' if append else 'w'
    with open(report_path, mode, encoding='utf-8') as file:
        if append:
            file.write('\n\n')
        file.write(report)
        file.write('\n')

    print(report)
    if append:
        print('Appending BPMFix report: {}'.format(report_path))
    else:
        print('Writing BPMFix report: {}'.format(report_path))
    return report_path


def apply_bpm_fix(audio_path, midi_path, report_path=None, append_report=False, target_bpm=None):
    output_path = bpmfix_path_for(midi_path)
    if target_bpm is None:
        print('Detecting BPM for {}'.format(audio_path))
        bpm_info = detect_bpm(audio_path)
        bpm_info['source'] = 'detected'
    else:
        bpm_info = bpm_info_from_manual_bpm(target_bpm)

    target_bpm = bpm_info['bpm']
    print(
        'Using BPM: {bpm:.2f} (source: {source})'.format(**bpm_info)
    )
    print('Writing BPM-fixed MIDI: {}'.format(output_path))
    fix_midi_bpm(midi_path, output_path, target_bpm)
    write_bpmfix_report(
        audio_path,
        midi_path,
        output_path,
        bpm_info,
        report_path=report_path,
        append=append_report,
    )
    return output_path, bpm_info
