"""Command line: from a song or piano recording to a cleaned MIDI, with default settings.

    python src/audio_to_midi.py song.flac [more audio ...]

Does what the Separate & transcribe tab and the Processing tab do with their defaults:
separate the piano with BS-RoFormer SW, transcribe it with ByteDance Piano Transcription, then run
postprocess.py with tempo detected from the original audio. Missing models are downloaded without
asking. Files that already exist are reused unless --regenerate is given.
"""

import argparse
import os
import sys

import model_store
import postprocess
from transcribe_tab import AUDIO_EXTENSIONS, TranscribeTab, output_paths


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('audio', nargs='+', help='Songs or piano recordings ({}).'.format(', '.join(AUDIO_EXTENSIONS)))
    parser.add_argument('--no-separate', action='store_true',
                        help='The audio is already solo piano (files named *_piano are never separated).')
    parser.add_argument('--output-dir', default='', help='Folder for the outputs (default: next to the audio).')
    parser.add_argument('--cpu', action='store_true', help='Do not use a GPU.')
    parser.add_argument('--regenerate', action='store_true', help='Redo separation and transcription even if their files exist.')
    parser.add_argument('--no-process', action='store_true', help='Stop after transcription; do not run postprocess.py.')
    return parser


def convert(audio, args):
    stem = os.path.splitext(os.path.basename(audio))[0]
    separate = not args.no_separate and not stem.lower().endswith('_piano')
    piano, midi = output_paths(audio, separate, args.output_dir)
    device = 'cpu' if args.cpu else 'auto'
    models = [model_store.MODELS['bytedance']]
    if separate:
        import separation
        package = separation.missing_package()
        if package:
            raise RuntimeError('Separation needs the Python package {0}: python -m pip install {0}'.format(package))
        models.insert(0, separation.required_model())
    for model in models:
        if not model.ready():
            model_store.download(model, log=print)
            print('Saved to {}'.format(model.path))

    if piano:
        if os.path.exists(piano) and not args.regenerate:
            print('{} exists; separation skipped.'.format(piano))
        else:
            print('Separating the piano with BS-RoFormer SW ({})...'.format(separation.backend()))
            temporary = piano + '.partial.wav'
            try:
                separation.separate_piano(audio, temporary, device)
                os.replace(temporary, piano)
            finally:
                if os.path.exists(temporary):
                    os.remove(temporary)
    if os.path.exists(midi) and not args.regenerate:
        print('{} exists; transcription skipped.'.format(midi))
    else:
        print('Transcribing {}...'.format(piano or audio))
        try:
            TranscribeTab.transcribe(piano or audio, midi, device)
        finally:
            partial = os.path.splitext(midi)[0] + '.partial.mid'
            if os.path.exists(partial):
                os.remove(partial)
    print('MIDI: {}'.format(midi))
    if args.no_process:
        return 0
    return postprocess.main([midi, '--audio', audio])


def main(argv=None):
    args = build_parser().parse_args(argv)
    failed = 0
    for audio in args.audio:
        if not os.path.isfile(audio):
            print('Not found: {}'.format(audio))
            failed += 1
            continue
        try:
            failed += 1 if convert(os.path.abspath(audio), args) else 0
        except Exception as error:
            print('Failed: {}: {}'.format(audio, error))
            failed += 1
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
