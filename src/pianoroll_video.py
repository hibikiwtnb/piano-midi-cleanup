"""Render a section of the piano roll to an MP4, page by page like the GUI, without opening a window.

Frames are drawn offline with PIL and piped to ffmpeg, so every frame time comes
from the MIDI clock and the picture cannot drift from audio bounced from the
same MIDI. The roll looks like the panel's roll (pianoroll_canvas.py) scaled to
the frame height: same colours, grid, hand colours, velocity shading, pedal
lane and page turn rule. Chords and the change overlay are not drawn.

    python pianoroll_video.py song_bpmfix_cleaned.mid --bars 9-20 --audio bounce.wav --out section.mp4
"""

import argparse
import math
import shutil
import subprocess
import sys
from bisect import bisect_right
from collections import defaultdict

from PIL import Image, ImageDraw, ImageFont

from gui_i18n import tr
from midi_stats import note_name
from pianoroll_canvas import (
    BAR_LINE, BEAT_LINE, BLACK_KEY_ROW, DEFAULT_PIXELS_PER_SECOND, HAND_BY_CHANNEL, HAND_ORDER, KEY_BLACK,
    KEY_OUTLINE, KEY_WHITE, KEYS_WIDTH, LABEL_TEXT, OCTAVE_LINE, PAGE_MARGIN, PEDAL_BACKGROUND, PEDAL_FILL,
    PEDAL_ROW_HEIGHT, PLAYHEAD, ROLL_BACKGROUND, ROW_HEIGHT, RULER_BACKGROUND, RULER_HEIGHT, RULER_TEXT,
    RULER_TICK, SIXTEENTH_LINE, read_roll, shade)
from pianoroll_snapshot import HAND_COLORS, OTHER_COLOR

PITCH_PADDING = 2
MIN_PITCH_RANGE = 24
ACTIVE_OUTLINE = '#1f2933'
FONT_FILES = ('msjh.ttc', '/System/Library/Fonts/Hiragino Sans GB.ttc', '/System/Library/Fonts/STHeiti Medium.ttc')


def load_font(size):
    for name in FONT_FILES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


class Layout:
    """Pixel geometry of one frame: the panel's roll scaled so the section's pitch range fills the height."""

    def __init__(self, width, height, low, high, pedal_rows, zoom):
        self.width, self.height = width, height
        self.low, self.high = low, high
        rows = max(1, pedal_rows)
        self.scale = height / (RULER_HEIGHT + rows * PEDAL_ROW_HEIGHT + (high - low + 1) * ROW_HEIGHT)
        s = self.scale
        self.keys = round(KEYS_WIDTH * s)
        self.ruler = round(RULER_HEIGHT * s)
        self.pedal_row = PEDAL_ROW_HEIGHT * s
        self.pedal_top = height - round(rows * self.pedal_row)
        self.row = (self.pedal_top - self.ruler) / (high - low + 1)
        self.roll_width = width - self.keys
        self.pps = DEFAULT_PIXELS_PER_SECOND * s * zoom
        self.margin = PAGE_MARGIN * s

    def pitch_y(self, pitch):
        return self.ruler + (self.high - pitch) * self.row


def pitch_range(notes):
    pitches = [pitch for _, _, pitch, _, _ in notes] or [60]
    low, high = min(pitches) - PITCH_PADDING, max(pitches) + PITCH_PADDING
    missing = MIN_PITCH_RANGE - (high - low)
    if missing > 0:
        low -= missing // 2
        high += missing - missing // 2
    return max(0, low), min(127, high)


def pedal_rows(pedals):
    """Merge channels with identical pedal intervals into one lane, as the panel does."""
    streams = defaultdict(list)
    for channel, intervals in sorted(pedals.items()):
        streams[tuple(intervals)].append(channel)
    return [(channels, list(intervals)) for intervals, channels in streams.items()]


def pedal_label(rows, channels):
    if len(rows) == 1:
        return tr('Pedal')
    names = sorted({HAND_BY_CHANNEL.get(channel, '') for channel in channels} - {''}, key=HAND_ORDER.index)
    return '/'.join(tr(hand.replace('_', ' ')) for hand in names) or tr('Pedal')


def draw_page(layout, left, notes, rows, bpm, key_signatures, end_seconds):
    """Everything that does not move while the page is shown; left is in seconds."""
    L = layout
    s = L.scale
    image = Image.new('RGB', (L.width, L.height), ROLL_BACKGROUND)
    draw = ImageDraw.Draw(image)
    x_of = lambda seconds: L.keys + (seconds - left) * L.pps
    right = left + L.roll_width / L.pps

    for pitch in range(L.low, L.high + 1):
        y = L.pitch_y(pitch)
        if pitch % 12 in (1, 3, 6, 8, 10):
            draw.rectangle((L.keys, y, L.width, y + L.row), fill=BLACK_KEY_ROW)
        if pitch % 12 == 0:
            draw.line((L.keys, y + L.row, L.width, y + L.row), fill=OCTAVE_LINE, width=max(1, round(s)))

    # Grid and ruler from the MIDI tempo with first beat at 0, as the panel shows a processed file.
    draw.rectangle((L.keys, 0, L.width, L.ruler), fill=RULER_BACKGROUND)
    ruler_font = load_font(round(11 * s))
    sixteenth = 60.0 / bpm / 4
    key_times = [start for start, _ in key_signatures]
    first = max(0, math.floor(left / sixteenth))
    last = math.ceil(min(right, end_seconds + sixteenth * 16) / sixteenth)
    previous_key = None
    for index in range(0, last + 1):
        seconds = index * sixteenth
        key_index = bisect_right(key_times, seconds + 1e-9) - 1
        key = key_signatures[key_index][1] if key_index >= 0 else None
        changed = index % 16 == 0 and key is not None and key != previous_key
        if index % 16 == 0 and key is not None:
            previous_key = key
        if index < first:
            continue
        x = x_of(seconds)
        if index % 16 == 0:
            draw.line((x, L.ruler, x, L.pedal_top), fill=BAR_LINE, width=max(2, round(2 * s)))
            draw.line((x, 0, x, L.ruler), fill=BAR_LINE, width=max(1, round(s)))
            label = str(index // 16 + 1)
            if changed:
                tonic = key[:-1] if key.endswith('m') else key
                label += '  Key=' + tonic + (' minor' if key.endswith('m') else ' Major')
            draw.text((x + 3 * s, L.ruler / 2), label, font=ruler_font, fill=RULER_TEXT, anchor='lm')
        elif index % 4 == 0:
            draw.line((x, L.ruler, x, L.pedal_top), fill=BEAT_LINE, width=max(1, round(s)))
            draw.line((x, L.ruler * 0.6, x, L.ruler), fill=RULER_TICK, width=max(1, round(s)))
        elif L.pps * sixteenth >= 6:
            draw.line((x, L.ruler, x, L.pedal_top), fill=SIXTEENTH_LINE, width=max(1, round(s)))

    for start, end, pitch, channel, velocity in notes:
        if end < left or start > right or not L.low <= pitch <= L.high:
            continue
        y = L.pitch_y(pitch)
        color = shade(HAND_COLORS.get(channel, (OTHER_COLOR,))[0], velocity)
        draw.rectangle((x_of(start), y + s, max(x_of(end), x_of(start) + 3 * s), y + L.row - s), fill=color)

    draw.rectangle((L.keys, L.pedal_top, L.width, L.height), fill=PEDAL_BACKGROUND)
    label_font = load_font(round(12 * s))
    draw.rectangle((0, L.pedal_top, L.keys - 1, L.height), fill=KEY_WHITE)
    for row, (channels, intervals) in enumerate(rows):
        y = L.pedal_top + row * L.pedal_row
        draw.text((L.keys - 3 * s, y + L.pedal_row / 2), pedal_label(rows, channels), font=label_font,
                  fill=LABEL_TEXT, anchor='rm')
        for down, up in intervals:
            if up < left or down > right:
                continue
            draw.rectangle((max(L.keys, x_of(down)), y + 2 * s, max(x_of(up), x_of(down) + 2 * s),
                            y + L.pedal_row - 2 * s), fill=PEDAL_FILL)

    # Keyboard last so notes running off the left edge stay under it.
    draw.rectangle((0, 0, L.keys - 1, L.pedal_top), fill=KEY_WHITE)
    key_font = load_font(round(9 * s))
    for pitch in range(L.low, L.high + 1):
        y = L.pitch_y(pitch)
        black = pitch % 12 in (1, 3, 6, 8, 10)
        draw.rectangle((0, y, L.keys - 1, y + L.row), fill=KEY_BLACK if black else KEY_WHITE, outline=KEY_OUTLINE)
        if pitch % 12 == 0:
            draw.text((L.keys - 3 * s, y + L.row / 2), note_name(pitch), font=key_font, fill=LABEL_TEXT, anchor='rm')
    draw.rectangle((0, 0, L.keys - 1, L.ruler), fill=KEY_WHITE)
    return image


def draw_frame(page, layout, left, seconds, notes, highlight):
    L = layout
    s = L.scale
    image = page.copy()
    draw = ImageDraw.Draw(image)
    x_of = lambda t: L.keys + (t - left) * L.pps
    if highlight:
        for start, end, pitch, channel, _ in notes:
            if start <= seconds < end and L.low <= pitch <= L.high:
                y = L.pitch_y(pitch)
                draw.rectangle((max(L.keys, x_of(start)), y + s, max(x_of(end), x_of(start) + 3 * s), y + L.row - s),
                               fill=HAND_COLORS.get(channel, (OTHER_COLOR,))[0], outline=ACTIVE_OUTLINE,
                               width=max(1, round(s)))
    x = x_of(seconds)
    draw.line((x, L.ruler, x, L.height), fill=PLAYHEAD, width=max(2, round(2 * s)))
    return image


def section_seconds(args, bpm):
    if args.bars:
        first, _, last = args.bars.partition('-')
        bar = 240.0 / bpm
        return (int(first) - 1) * bar, int(last or first) * bar
    return args.start, args.end


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('midi')
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument('--bars', help='bar range as numbered on the roll ruler, e.g. 9-20 (end bar included)')
    where.add_argument('--start', type=float, help='section start in MIDI seconds (use with --end)')
    parser.add_argument('--end', type=float, help='section end in MIDI seconds')
    parser.add_argument('--out', required=True, help='output .mp4')
    parser.add_argument('--audio', help='audio rendered from the same MIDI; its 0 s is the MIDI 0 s')
    parser.add_argument('--audio-offset', type=float, default=0.0,
                        help='seconds of audio before MIDI 0 s (e.g. a lead-in in the bounce)')
    parser.add_argument('--size', default='1920x1080')
    parser.add_argument('--fps', type=int, default=60)
    parser.add_argument('--zoom', type=float, default=1.0, help='horizontal zoom relative to the panel default')
    parser.add_argument('--no-highlight', action='store_true', help='do not outline notes while they sound')
    args = parser.parse_args(argv)
    if args.start is not None and args.end is None:
        parser.error('--start needs --end')
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg is None:
        parser.error('ffmpeg not found on PATH')

    notes, pedals, duration, bpm, key_signatures = read_roll(args.midi)
    start, end = section_seconds(args, bpm)
    if not 0 <= start < end:
        parser.error('empty section')
    width, height = (int(value) for value in args.size.lower().split('x'))
    # Only the section is drawn, so nothing outside the recorded range shows on its first or last page.
    notes = [note for note in notes if note[1] > start and note[0] < end]
    rows = pedal_rows({channel: [(max(down, start), min(up, end)) for down, up in intervals
                                 if up > start and down < end] for channel, intervals in pedals.items()})
    layout = Layout(width, height, *pitch_range(notes), len(rows), args.zoom)

    command = [ffmpeg, '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
               '-s', '{}x{}'.format(width, height), '-r', str(args.fps), '-i', '-']
    if args.audio:
        command += ['-ss', '{:.6f}'.format(start + args.audio_offset), '-t', '{:.6f}'.format(end - start),
                    '-i', args.audio, '-map', '0:v', '-map', '1:a', '-c:a', 'aac', '-b:a', '320k']
    command += ['-c:v', 'libx264', '-preset', 'medium', '-crf', '16', '-pix_fmt', 'yuv420p',
                '-movflags', '+faststart', args.out]
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE)

    frames = round((end - start) * args.fps)
    left = max(0.0, start - layout.margin / layout.pps)
    page = None
    pages = 0
    for frame in range(frames):
        seconds = start + frame / args.fps
        x = (seconds - left) * layout.pps
        if page is None or x > layout.roll_width - layout.margin or x < 0:
            if page is not None:
                left = seconds - layout.margin / layout.pps
            page = draw_page(layout, left, notes, rows, bpm, key_signatures, duration)
            pages += 1
        encoder.stdin.write(draw_frame(page, layout, left, seconds, notes, not args.no_highlight).tobytes())
    encoder.stdin.close()
    if encoder.wait() != 0:
        sys.exit('ffmpeg failed')
    print('{}: {:.2f}-{:.2f} s, {} frames, {} pages, pitch {}-{}'.format(
        args.out, start, end, frames, pages, note_name(layout.low), note_name(layout.high)))


if __name__ == '__main__':
    main()
