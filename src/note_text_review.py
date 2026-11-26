import argparse
import sys

import mido

from hand_split_channels import CHANNELS, collect_notes, group_notes
from meter import bar_beats
from midi_stats import note_name


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
    beats_per_bar = bar_beats(midi_file)
    lines = []
    for group in group_notes(collect_notes(midi_file)):
        beat = group[0]['start_ticks'] / ticks_per_beat
        bar = int(beat // beats_per_bar) + 1
        if bars and not bars[0] <= bar <= bars[1]:
            continue
        cells = ['{}{}({:.2f}b)'.format(
            hand_label(note['start_message'].channel),
            note_name(note['note']),
            (note['end_ticks'] - note['start_ticks']) / ticks_per_beat,
        ) for note in sorted(group, key=lambda note: note['note'])]
        lines.append('m{} beat {:.2f}  {}'.format(bar, beat % beats_per_bar + 1, ' '.join(cells)))
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Print the notes of a bar-aligned MIDI as text, one onset group per line. Does not modify the MIDI.',
    )
    parser.add_argument('input_midi', help='Bar-aligned MIDI to read.')
    parser.add_argument('--bars', type=parse_bars, help='Bar range such as 95-97, or one bar such as 96.')
    parser.add_argument('-o', '--output', help='Write to this text file instead of printing.')
    parser.add_argument('--musicxml', help='MusicXML of the same passage; enables notation checks (read-only).')
    parser.add_argument('--bar-offset', type=int, default=0,
                        help='display measure = MIDI measure + this offset; MusicXML uses display measures.')
    parser.add_argument('--quality-json', metavar='PATH',
                        help='Write the structured per-measure review JSON (read-only).')
    parser.add_argument('--quality-markdown', metavar='PATH',
                        help='Write the human-readable per-measure review (read-only).')
    parser.add_argument('--top', type=int, default=15,
                        help='Measures in the Markdown priority list (default 15).')
    args = parser.parse_args(argv)

    text = '\n'.join(note_text_lines(args.input_midi, args.bars)) + '\n'
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as file:
            file.write(text)
        print('Writing note text: {}'.format(args.output))
    else:
        sys.stdout.write(text)

    if args.quality_json or args.quality_markdown:
        import measure_quality
        result = measure_quality.analyze(args.input_midi, args.musicxml, args.bar_offset)
        if args.quality_json:
            measure_quality.write_json(result, args.quality_json)
            print('Writing quality JSON: {}'.format(args.quality_json))
        if args.quality_markdown:
            report = measure_quality.write_markdown(result, args.quality_markdown, args.top)
            with open(args.quality_markdown, 'w', encoding='utf-8') as file:
                file.write(report + '\n')
            print('Writing quality report: {}'.format(args.quality_markdown))
    return 0


if __name__ == '__main__':
    sys.exit(main())
