import argparse
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

import mido
from PIL import Image, ImageDraw, ImageFont

from hand_split_channels import CHANNELS, collect_notes
from midi_stats import note_name
from note_text_review import parse_bars


BEATS_PER_BAR = 4
BARS_PER_IMAGE = 4
MIN_PITCH_RANGE = 24
PITCH_PADDING = 2
KEYBOARD_WIDTH = 44
HEADER_HEIGHT = 34
MARGIN = 8
# Portrait is the landscape image rotated clockwise, with time running downward.
LAYOUTS = {
    'landscape': {'width': 1920, 'roll_height': 900},
}
HAND_COLORS = {
    CHANNELS['right']: ('#1976d2', 'R right'),
    CHANNELS['second_right']: ('#b77900', 'R2 second right'),
    CHANNELS['second_left']: ('#7559a6', 'L2 second left'),
    CHANNELS['left']: ('#3f8f3f', 'L left'),
}
OTHER_COLOR = '#6f9fd8'
BLACK_KEYS = {1, 3, 6, 8, 10}


def font(size):
    return ImageFont.load_default(size=size)


def pitch_range(notes):
    if not notes:
        return 48, 48 + MIN_PITCH_RANGE
    low = min(note['note'] for note in notes) - PITCH_PADDING
    high = max(note['note'] for note in notes) + PITCH_PADDING
    missing = MIN_PITCH_RANGE - (high - low)
    if missing > 0:
        low -= missing // 2
        high += missing - missing // 2
    return low, high


def draw_row(draw, box, first_bar, bar_count, notes, low, high, ticks_per_beat):
    """One strip of bars: key shading, grid, notes, and bar numbers."""
    left, top, right, bottom = box
    roll_left = left + KEYBOARD_WIDTH
    bar_ticks = ticks_per_beat * BEATS_PER_BAR
    start_tick = (first_bar - 1) * bar_ticks
    end_tick = start_tick + bar_count * bar_ticks
    key_height = (bottom - top) / (high - low + 1)
    tick_width = (right - roll_left) / (end_tick - start_tick)

    def y_for(pitch):
        return bottom - (pitch - low + 1) * key_height

    for pitch in range(low, high + 1):
        y = y_for(pitch)
        black = pitch % 12 in BLACK_KEYS
        draw.rectangle([roll_left, y, right, y + key_height], fill='#edf5ff' if black else '#ffffff')
        draw.rectangle([left, y, roll_left - 2, y + key_height], fill='#2a2a2a' if black else '#f4f4f4',
                       outline='#c8c8c8')
        if pitch % 12 == 0:
            draw.line([roll_left, y + key_height, right, y + key_height], fill='#bfd2eb')
            draw.text((left + 3, y + key_height / 2), note_name(pitch), fill='#202020',
                      font=font(max(9, min(13, int(key_height * 1.4)))), anchor='lm')

    for beat in range(bar_count * BEATS_PER_BAR + 1):
        x = roll_left + beat * ticks_per_beat * tick_width
        bar_line = beat % BEATS_PER_BAR == 0
        draw.line([x, top, x, bottom], fill='#404040' if bar_line else '#b8b8b8', width=2 if bar_line else 1)
        if bar_line and beat < bar_count * BEATS_PER_BAR:
            draw.text((x + 4, top + 2), str(first_bar + beat // BEATS_PER_BAR), fill='#404040', font=font(16))

    for note in notes:
        if note['end_ticks'] <= start_tick or note['start_ticks'] >= end_tick:
            continue
        x0 = roll_left + (max(note['start_ticks'], start_tick) - start_tick) * tick_width
        x1 = roll_left + (min(note['end_ticks'], end_tick) - start_tick) * tick_width
        y = y_for(note['note'])
        color = HAND_COLORS.get(note['start_message'].channel, (OTHER_COLOR, ''))[0]
        draw.rectangle([x0, y + 1, max(x0 + 2, x1 - 1), y + key_height - 1], fill=color, outline='#002d69')


def render_image(notes, first_bar, ticks_per_beat, layout_name):
    if layout_name == 'portrait':
        return render_image(notes, first_bar, ticks_per_beat, 'landscape').transpose(Image.Transpose.ROTATE_270)
    layout = LAYOUTS[layout_name]
    bar_ticks = ticks_per_beat * BEATS_PER_BAR
    start_tick = (first_bar - 1) * bar_ticks
    end_tick = start_tick + BARS_PER_IMAGE * bar_ticks
    visible = [note for note in notes if note['end_ticks'] > start_tick and note['start_ticks'] < end_tick]
    low, high = pitch_range(visible)

    height = HEADER_HEIGHT + layout['roll_height'] + MARGIN
    image = Image.new('RGB', (layout['width'], height), '#e7e7e7')
    draw = ImageDraw.Draw(image)
    draw.text((MARGIN, HEADER_HEIGHT / 2), 'Bars {}-{}'.format(first_bar, first_bar + BARS_PER_IMAGE - 1),
              fill='#202020', font=font(20), anchor='lm')
    legend_x = layout['width'] - MARGIN
    for color, label in reversed(list(HAND_COLORS.values())):
        width = draw.textlength(label, font=font(16))
        legend_x -= width
        draw.text((legend_x, HEADER_HEIGHT / 2), label, fill='#202020', font=font(16), anchor='lm')
        draw.rectangle([legend_x - 18, HEADER_HEIGHT / 2 - 6, legend_x - 6, HEADER_HEIGHT / 2 + 6], fill=color)
        legend_x -= 34

    draw_row(draw, (MARGIN, HEADER_HEIGHT, layout['width'] - MARGIN, HEADER_HEIGHT + layout['roll_height']),
             first_bar, BARS_PER_IMAGE, visible, low, high, ticks_per_beat)
    return image


def default_output_dir(midi_path):
    return os.path.splitext(midi_path)[0] + '_pianoroll'


def write_snapshots(midi_path, output_dir=None, bars=None, layouts=('landscape', 'portrait')):
    midi_file = mido.MidiFile(midi_path)
    notes = collect_notes(midi_file)
    ticks_per_beat = midi_file.ticks_per_beat
    last_bar = max(note['end_ticks'] for note in notes) // (ticks_per_beat * BEATS_PER_BAR) + 1 if notes else 1
    first, last = bars or (1, last_bar)
    output_dir = output_dir or default_output_dir(midi_path)
    os.makedirs(output_dir, exist_ok=True)

    paths = []
    for first_bar in range(first, last + 1, BARS_PER_IMAGE):
        for layout in layouts:
            path = os.path.join(output_dir, 'bars_{:03d}-{:03d}_{}.png'.format(
                first_bar, first_bar + BARS_PER_IMAGE - 1, layout))
            render_image(notes, first_bar, ticks_per_beat, layout).save(path)
            paths.append(path)
    print('Wrote {} image(s) to {}'.format(len(paths), output_dir))
    return paths


def choose_midi():
    root = tk.Tk()
    root.withdraw()
    return filedialog.askopenfilename(
        title='Choose bar-aligned MIDI for piano roll images',
        filetypes=[
            ('MIDI files', '*.mid *.midi'),
            ('All files', '*.*'),
        ],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Save piano roll images of a bar-aligned MIDI, four bars per image. Does not modify the MIDI.',
    )
    parser.add_argument('input_midi', nargs='?', help='Bar-aligned MIDI to read. Without it a dialog opens.')
    parser.add_argument('--bars', type=parse_bars, help='Bar range such as 17-32; images start at its first bar.')
    parser.add_argument('--layout', choices=('landscape', 'portrait', 'both'), default='both',
                        help='landscape: four bars in a row; portrait: rotated clockwise for phones (default: both).')
    parser.add_argument('-o', '--output-dir', help='Output folder (default: <midi name>_pianoroll next to the MIDI).')
    args = parser.parse_args(argv)

    midi_path = args.input_midi or choose_midi()
    if not midi_path:
        print('No input selected.')
        return 1
    if not os.path.isfile(midi_path):
        print('MIDI file does not exist: {}'.format(midi_path))
        return 1

    layouts = ('landscape', 'portrait') if args.layout == 'both' else (args.layout,)
    paths = write_snapshots(midi_path, output_dir=args.output_dir, bars=args.bars, layouts=layouts)
    if not args.input_midi:
        messagebox.showinfo('Piano Roll complete', 'Wrote {} image(s) to:\n{}'.format(
            len(paths), os.path.dirname(paths[0]) if paths else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
