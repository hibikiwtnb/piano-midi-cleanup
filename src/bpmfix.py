import argparse
import os
import sys

from bpmfix_utils import apply_bpm_fix, bpmfix_report_path_for
from first_beat import auto_align_first_beat
from key_signature import apply_key_signatures


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description='Fix MIDI BPM, align the first beat, then write key signatures. '
                    'Adjust the first beat by hand in the main panel piano roll.',
    )
    parser.add_argument('input_midi', help='MIDI to fix; writes *_bpmfix.mid next to it.')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--bpm', type=float, help='Known BPM.')
    source.add_argument('--audio', help='Source audio for Essentia BPM detection.')
    parser.add_argument('--no-auto-first-beat', action='store_true', help='Skip the automatic first beat alignment.')
    parser.add_argument('--no-key-signature', action='store_true', help='Skip key signature detection.')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not os.path.isfile(args.input_midi):
        print('MIDI file does not exist: {}'.format(args.input_midi))
        return 1
    if args.audio is not None and not os.path.isfile(args.audio):
        print('Audio file does not exist: {}'.format(args.audio))
        return 1

    report_path = bpmfix_report_path_for(args.input_midi)
    output_path, _ = apply_bpm_fix(args.audio, args.input_midi, target_bpm=args.bpm)
    if not args.no_auto_first_beat:
        auto_align_first_beat(output_path, report_path=report_path)
    if not args.no_key_signature:
        apply_key_signatures(output_path, report_path=report_path)
    print('Output: {}'.format(output_path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
