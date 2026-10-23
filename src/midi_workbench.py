import argparse
import os
import sys

from first_beat import auto_align_first_beat
from key_signature import apply_key_signatures


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description='Align the first beat of a BPM-fixed MIDI in place from its notes, then rewrite key signatures. '
                    'Adjust the first beat by hand in the main panel piano roll.',
    )
    parser.add_argument('input_midi', help='MIDI to modify in place, usually song_bpmfix.mid.')
    parser.add_argument('--no-key-signature', action='store_true',
                        help='Do not rewrite key signatures after the first beat moves.')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not os.path.isfile(args.input_midi):
        print('MIDI file does not exist: {}'.format(args.input_midi))
        return 1
    if auto_align_first_beat(args.input_midi) is not None and not args.no_key_signature:
        # Shifting moves the old key signature events off the bar lines.
        apply_key_signatures(args.input_midi)
    return 0


if __name__ == '__main__':
    sys.exit(main())
