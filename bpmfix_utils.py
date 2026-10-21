import cmath
import json
import math
import os
import subprocess
from pathlib import Path

import mido

from node_utils import node_executable


DEFAULT_BPM_ENV = Path.home() / '.local' / 'share' / 'pianotrans-tools-env'
BPM_ENV = Path(os.environ.get('PIANOTRANS_BPM_ENV', DEFAULT_BPM_ENV)).expanduser()
DEFAULT_BPM_PYTHON = BPM_ENV / 'Scripts' / 'python.exe' if os.name == 'nt' else BPM_ENV / 'bin' / 'python'
BPM_PYTHON = os.environ.get('PIANOTRANS_BPM_PYTHON', str(DEFAULT_BPM_PYTHON))
DEFAULT_MIDI_TEMPO = 500000
ROUND_MAX_END_DRIFT_MS = 50.0

# Automatic first beat: 8th-note phase from velocity-weighted onsets, on-beat half by
# quarter-grid weight, downbeat by left-hand / bass onset weight per beat.
BEATS_PER_BAR = 4
ON_BEAT_WINDOW_SECONDS = 0.03
DOWNBEAT_WINDOW_SECONDS = 0.04
BASS_MAX_PITCH = 54
LEFT_HAND_CHANNEL = 2


def bpmfix_path_for(midi_path):
    filename, ext = os.path.splitext(midi_path)
    return filename + '_bpmfix' + ext


def bpmfix_report_path_for(midi_path):
    filename, _ = os.path.splitext(midi_path)
    return filename + '_stats.txt'


def detect_bpm(audio_path):
    if os.name == 'nt':
        return detect_bpm_essentia_js(audio_path)
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
        encoding='utf-8',
    )
    bpm_info = json.loads(result.stdout)
    bpm_info.update(choose_applied_bpm(bpm_info['phase_bpm'], bpm_info['duration']))
    return bpm_info


def detect_bpm_essentia_js(audio_path):
    import librosa
    import numpy as np

    sample_rate = 44100
    audio, _ = librosa.load(audio_path, sr=sample_rate, mono=True)
    script_path = Path(__file__).with_name('detect_beats_essentia.js')
    result = subprocess.run(
        [node_executable('Essentia BPM detection'), str(script_path)],
        input=np.asarray(audio, dtype='<f4').tobytes(),
        capture_output=True,
        check=True,
    )
    detected = json.loads(result.stdout.decode('utf-8'))
    beats = np.asarray(detected['beats'])
    bpm = float(detected['bpm'])

    # Phase coherence at half-beat resolution tolerates off-beat jumps in the beat track.
    candidates = np.arange(bpm - 1.0, bpm + 1.0, 0.0005)
    phases = np.exp(4j * np.pi * np.outer(candidates / 60.0, beats))
    coherence = np.abs(phases.mean(axis=1))
    best = int(coherence.argmax())

    bpm_info = {
        'raw_bpm': bpm,
        'phase_bpm': float(candidates[best]),
        'phase_coherence': float(coherence[best]),
        'confidence': float(detected['confidence']),
        'beats': len(beats),
        'duration': len(audio) / sample_rate,
    }
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


def midi_source_tempo(midi_file):
    for track in midi_file.tracks:
        for message in track:
            if message.type == 'set_tempo':
                return message.tempo
    return DEFAULT_MIDI_TEMPO


def fix_midi_bpm(midi_path, output_path, target_bpm):
    """Rescale ticks from the MIDI's own tempo to target_bpm, keeping playback time."""
    midi_file = mido.MidiFile(midi_path)
    source_tempo = midi_source_tempo(midi_file)
    target_tempo = int(mido.bpm2tempo(target_bpm))
    has_tempo = False

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
                has_tempo = True

    if not has_tempo:
        midi_file.tracks[0].insert(0, mido.MetaMessage('set_tempo', tempo=target_tempo, time=0))

    midi_file.save(output_path)
    return mido.tempo2bpm(source_tempo)


def wrapped_distance(value, period):
    return abs(((value + period / 2) % period) - period / 2)


def estimate_first_beat(midi_path):
    """Estimate the bar line nearest to 0 s (within half a bar) of a fixed-tempo, hand-split MIDI."""
    midi_file = mido.MidiFile(midi_path)
    beat = midi_source_tempo(midi_file) / 1e6
    bar = beat * BEATS_PER_BAR
    seconds = 0.0
    onsets = []
    for message in midi_file:
        seconds += message.time
        if message.type == 'note_on' and message.velocity > 0:
            onsets.append((seconds, message.velocity, message.note, message.channel))
    if not onsets:
        return None

    max_velocity = max(velocity for _, velocity, _, _ in onsets)
    weighted = [(start, velocity / max_velocity, pitch, channel) for start, velocity, pitch, channel in onsets]

    eighth = beat / 2
    phasor = sum(weight * cmath.exp(2j * math.pi * start / eighth) for start, weight, _, _ in weighted)
    eighth_phase = (cmath.phase(phasor) / (2 * math.pi) * eighth) % eighth
    coherence = abs(phasor) / sum(weight for _, weight, _, _ in weighted)

    def on_grid_weight(origin, period, window, notes):
        return sum(weight for start, weight, _, _ in notes
                   if wrapped_distance(start - origin, period) < window)

    beat_phase = max(
        (eighth_phase, eighth_phase + eighth),
        key=lambda origin: on_grid_weight(origin, beat, ON_BEAT_WINDOW_SECONDS, weighted),
    )

    bass = [note for note in weighted if note[3] == LEFT_HAND_CHANNEL or note[2] <= BASS_MAX_PITCH]
    weights = [
        on_grid_weight(beat_phase + index * beat, bar, DOWNBEAT_WINDOW_SECONDS, bass)
        for index in range(BEATS_PER_BAR)
    ]
    total = sum(weights) or 1.0
    downbeat = max(range(BEATS_PER_BAR), key=lambda index: weights[index])
    # Bass share of beats 1-4 counted from the chosen downbeat.
    shares = [weights[(downbeat + index) % BEATS_PER_BAR] / total for index in range(BEATS_PER_BAR)]
    # The bar line nearest to 0, so an already aligned MIDI is moved by almost nothing.
    first_beat = ((beat_phase + downbeat * beat + bar / 2) % bar) - bar / 2

    return {
        'first_beat_seconds': first_beat,
        'eighth_coherence': coherence,
        'bass_shares': shares,
    }


def format_auto_first_beat_stats(midi_path, estimate, result):
    return '\n'.join([
        'MIDI auto first beat stats',
        'MIDI: {}'.format(midi_path),
        'Estimated first beat: {:.3f}s'.format(estimate['first_beat_seconds']),
        '8th-grid onset coherence: {:.3f}'.format(estimate['eighth_coherence']),
        'Bass onset share on beats 1-4: {}'.format(
            ' '.join('{:.2f}'.format(share) for share in estimate['bass_shares'])),
        'Shift ticks: {}'.format(result['shift_ticks']),
        'Padding bars: {}'.format(result['padding_bars']),
    ])


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
        'Original BPM: {:.2f}'.format(bpm_info['original_bpm']),
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
    bpm_info['original_bpm'] = fix_midi_bpm(midi_path, output_path, target_bpm)
    write_bpmfix_report(
        audio_path,
        midi_path,
        output_path,
        bpm_info,
        report_path=report_path,
        append=append_report,
    )
    return output_path, bpm_info
