import os
from http.client import HTTPException
from pathlib import Path
import shutil
import sys
import tempfile
import tkinter as tk
from tkinter import filedialog
from urllib.request import urlopen

import librosa
import torch

from bpmfix_utils import apply_bpm_fix, bpmfix_path_for
from clean_midi_rules import apply_rule_cleaning, cleaned_path_for
from hand_split_channels import apply_hand_split
from midi_stats import stats_path_for, write_midi_stats
from piano_transcription_inference import PianoTranscription, sample_rate


AUDIO_EXTENSIONS = {'.flac', '.mp3', '.wav', '.ape', '.m4a', '.aif', '.aiff'}
CHECKPOINT_NAME = 'note_F1=0.9677_pedal_F1=0.9186.pth'
CHECKPOINT_URL = ('https://zenodo.org/records/4034264/files/'
                  'CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1')
MIN_CHECKPOINT_BYTES = 160000000


def ensure_checkpoint():
    """Download the upstream checkpoint without wget; publish only a complete file."""
    checkpoint = Path.home() / 'piano_transcription_inference_data' / CHECKPOINT_NAME
    if checkpoint.is_file() and checkpoint.stat().st_size >= MIN_CHECKPOINT_BYTES:
        return str(checkpoint)
    temporary = None
    try:
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        print('Downloading piano transcription model (~165 MB)...', flush=True)
        with urlopen(CHECKPOINT_URL, timeout=60) as response:
            length = response.headers.get('Content-Length')
            with tempfile.NamedTemporaryFile(dir=checkpoint.parent, suffix='.download', delete=False) as output:
                temporary = Path(output.name)
                shutil.copyfileobj(response, output)
        size = temporary.stat().st_size
        if size < MIN_CHECKPOINT_BYTES or (length is not None and size != int(length)):
            raise ValueError('The model download is incomplete.')
        temporary.replace(checkpoint)
        return str(checkpoint)
    except (OSError, ValueError, HTTPException) as error:
        raise RuntimeError('Could not download the piano model. Check your connection and retry. '
                           'Model location: {}. {}'.format(checkpoint, error)) from error
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def select_device():
    if torch.cuda.is_available():
        return torch.device('cuda')
    return torch.device('cpu')


def midi_path_for(audio_path):
    filename, ext = os.path.splitext(audio_path)
    if ext.lower() not in AUDIO_EXTENSIONS:
        return None
    return filename + '.mid'


def transcribe_file(audio_path, transcriptor):
    midi_path = midi_path_for(audio_path)
    if not midi_path:
        print('Skip non-audio file: {}'.format(audio_path))
        return False

    if os.path.exists(midi_path):
        print('MIDI already exists: {}'.format(midi_path))
        if not os.path.exists(stats_path_for(midi_path)):
            write_midi_stats(midi_path)
        if not os.path.exists(bpmfix_path_for(midi_path)):
            bpmfix_path, _ = apply_bpm_fix(
                audio_path,
                midi_path,
                report_path=stats_path_for(midi_path),
                append_report=True,
            )
            cleaned_path, _, _ = apply_rule_cleaning(
                bpmfix_path,
                report_path=stats_path_for(midi_path),
                append_report=True,
            )
            apply_hand_split(
                cleaned_path,
                report_path=stats_path_for(midi_path),
                append_report=True,
            )
        elif not os.path.exists(cleaned_path_for(bpmfix_path_for(midi_path))):
            cleaned_path, _, _ = apply_rule_cleaning(
                bpmfix_path_for(midi_path),
                report_path=stats_path_for(midi_path),
                append_report=True,
            )
            apply_hand_split(
                cleaned_path,
                report_path=stats_path_for(midi_path),
                append_report=True,
            )
        return False

    print('Loading {}'.format(audio_path))
    audio, _ = librosa.load(audio_path, sr=sample_rate, mono=True)
    print('Writing {}'.format(midi_path))
    transcriptor.transcribe(audio, midi_path)
    write_midi_stats(midi_path, reset_report=True)
    bpmfix_path, _ = apply_bpm_fix(
        audio_path,
        midi_path,
        report_path=stats_path_for(midi_path),
        append_report=True,
    )
    cleaned_path, _, _ = apply_rule_cleaning(
        bpmfix_path,
        report_path=stats_path_for(midi_path),
        append_report=True,
    )
    apply_hand_split(
        cleaned_path,
        report_path=stats_path_for(midi_path),
        append_report=True,
    )
    return True


def choose_audio_files():
    root = tk.Tk()
    root.withdraw()

    return filedialog.askopenfilenames(
        title='Choose audio file(s) to transcribe',
        filetypes=[
            ('Audio files', '*.flac *.mp3 *.wav *.ape *.m4a *.aif *.aiff'),
            ('All files', '*.*'),
        ],
    )


def main():
    print('--------------------------------------------------------------------------------')
    print('Pianotrans transcription script based on GiantMIDI-Piano')
    print('Choose one or more audio files. MIDI files are written next to audio files.')
    print('--------------------------------------------------------------------------------')

    selected_paths = choose_audio_files()
    if not selected_paths:
        print('No input selected.')
        return 1

    audio_files = [path for path in selected_paths if midi_path_for(path)]
    if not audio_files:
        print('No supported audio files found.')
        return 1

    device = select_device()
    print('Using {} for inference.'.format(device))
    try:
        transcriptor = PianoTranscription(device=device, checkpoint_path=ensure_checkpoint())
    except RuntimeError as error:
        print(error)
        return 1

    converted = 0
    for audio_path in audio_files:
        if transcribe_file(audio_path, transcriptor):
            converted += 1

    print('Done. Converted {} file(s).'.format(converted))
    return 0


if __name__ == '__main__':
    sys.exit(main())
