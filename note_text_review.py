import argparse
import sys

import mido

from hand_split_channels import CHANNELS, collect_notes, group_notes
from midi_stats import note_name


BEATS_PER_BAR = 4
HAND_LABELS = {
    CHANNELS['right']: 'R',
    CHANNELS['second_right']: 'R2',
    CHANNELS['second_left']: 'L2',
    CHANNELS['left']: 'L',
}


def hand_label(channel):
    return HAND_LABELS.get(channel, '?')


def parse_bars(text):
    first, _, last = text.partition('-')
    return int(first), int(last or first)


def note_text_lines(midi_path, bars=None):
    """One line per onset group: bar, beat, then hand, pitch and length in beats for each note, low to high."""
    midi_file = mido.MidiFile(midi_path)
    ticks_per_beat = midi_file.ticks_per_beat
    lines = []
    for group in group_notes(collect_notes(midi_file)):
        beat = group[0]['start_ticks'] / ticks_per_beat
        bar = int(beat // BEATS_PER_BAR) + 1
        if bars and not bars[0] <= bar <= bars[1]:
            continue
        cells = ['{}{}({:.2f}b)'.format(
            hand_label(note['start_message'].channel),
            note_name(note['note']),
            (note['end_ticks'] - note['start_ticks']) / ticks_per_beat,
        ) for note in sorted(group, key=lambda note: note['note'])]
        lines.append('m{} beat {:.2f}  {}'.format(bar, beat % BEATS_PER_BAR + 1, ' '.join(cells)))
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Print the notes of a bar-aligned MIDI as text, one onset group per line. Does not modify the MIDI.',
    )
    parser.add_argument('input_midi', help='Bar-aligned MIDI to read.')
    parser.add_argument('--bars', type=parse_bars, help='Bar range such as 95-97, or one bar such as 96.')
    parser.add_argument('-o', '--output', help='Write to this text file instead of printing.')
    args = parser.parse_args(argv)

    text = '\n'.join(note_text_lines(args.input_midi, args.bars)) + '\n'
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as file:
            file.write(text)
        print('Writing note text: {}'.format(args.output))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
