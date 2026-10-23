"""Tk Canvas piano roll for the main panel: playback, hands, sustain pedal and first-beat alignment."""

import math
import os
import time
import tkinter as tk
from collections import defaultdict
from tkinter import ttk

import mido

from bpmfix_utils import bpmfix_path_for
from clean_midi_rules import tick_seconds
from first_beat import append_first_beat_stats, build_bpm_fixed_midi, midi_tempo
from hand_split_channels import CHANNELS
from key_signature import apply_key_signatures
from midi_playback import MidiPlayback, default_output_name, open_output, output_names
from midi_stats import note_name
from pianoroll_snapshot import HAND_COLORS, OTHER_COLOR
from gui_i18n import tr
from note_changes import changes_path, load_changes


KEYS_WIDTH = 44
RULER_HEIGHT = 20
ROW_HEIGHT = 9
PEDAL_ROW_HEIGHT = 12
PITCHES = 128
DEFAULT_PIXELS_PER_SECOND = 100.0
HORIZONTAL_SCROLL_INCREMENT = 24
HAND_ORDER = ('left', 'right', 'second_left', 'second_right')
HAND_BY_CHANNEL = {channel: hand for hand, channel in CHANNELS.items()}


def first_beat_target(midi_path):
    """song_bpmfix.mid of the song when the shown MIDI shares its timeline, else None."""
    base = os.path.splitext(midi_path)[0]
    for suffix in ('_bpmfix_cleaned', '_bpmfix'):
        if base.endswith(suffix):
            target = bpmfix_path_for(base[:-len(suffix)] + '.mid')
            return target if os.path.isfile(target) else None
    return None


def read_roll(midi_path):
    """Notes as (start s, end s, pitch, channel, velocity), pedal-down intervals per channel, duration and BPM."""
    midi = mido.MidiFile(midi_path)
    seconds = tick_seconds(midi)
    notes = []
    pedal_events = defaultdict(list)
    for track in midi.tracks:
        active = defaultdict(list)
        tick = 0
        for message in track:
            tick += message.time
            if message.type == 'note_on' and message.velocity > 0:
                active[message.channel, message.note].append((tick, message.velocity))
            elif message.type in ('note_on', 'note_off'):
                starts = active[message.channel, message.note]
                if starts:
                    # Match the post-processing note pairing, including repeated pitches.
                    start, velocity = starts.pop()
                    notes.append((seconds(start), seconds(tick), message.note, message.channel, velocity))
            elif message.type == 'control_change' and message.control == 64:
                pedal_events[message.channel].append((tick, message.value >= 64))
    duration = max((end for _, end, _, _, _ in notes), default=0.0)
    pedals = {}
    for channel, events in pedal_events.items():
        intervals = []
        down = None
        for tick, state in sorted(events, key=lambda item: item[0]):
            if state and down is None:
                down = tick
            elif not state and down is not None:
                intervals.append((seconds(down), seconds(tick)))
                down = None
        if down is not None:
            intervals.append((seconds(down), max(duration, seconds(down))))
        pedals[channel] = intervals
        duration = max([duration] + [end for _, end in intervals])
    return notes, pedals, duration, mido.tempo2bpm(midi_tempo(midi))


def shade(color, velocity):
    """Blend a hex colour towards white for soft notes."""
    strength = 0.35 + 0.65 * velocity / 127
    channels = [int(color[index:index + 2], 16) for index in (1, 3, 5)]
    return '#' + ''.join('{:02x}'.format(round(255 - (255 - value) * strength)) for value in channels)


class PianoRollView(ttk.Frame):
    def __init__(self, master, midi_port=None, on_output=None, on_first_beat=None, status=None):
        super().__init__(master)
        self.on_output = on_output
        self.on_first_beat = on_first_beat
        self.path = None
        self.loaded_mtime = None
        self.target = None
        self.notes = []
        self.pedal_rows = []
        self.duration = 0.0
        self.pixels_per_second = DEFAULT_PIXELS_PER_SECOND
        self.output = None
        self.playback = None
        self.play_started = None
        self.play_start_seconds = 0.0
        self.after_id = None
        self.play_after_id = None
        self.center_pending = False
        self.changes = None
        self.changes_mtime = None
        self.change_items = {}

        self.bpm = tk.StringVar()
        self.offset = tk.StringVar(value='0')
        self.output_var = tk.StringVar(value=default_output_name() if midi_port in (None, 'CoPiano') else midi_port)
        self.shading = tk.BooleanVar(value=True)
        self.change_mode = tk.StringVar(value=tr('Hide changes'))
        self.hand_enabled = {hand: tk.BooleanVar(value=True) for hand in HAND_ORDER}
        self.target_text = tk.StringVar()
        self.status = status if status is not None else tk.StringVar(value=tr('Ready'))
        self.build()
        self.bpm.trace_add('write', lambda *_: self.draw_grid())
        self.offset.trace_add('write', lambda *_: self.draw_grid())
        self.after_id = self.after(50, self.update_playhead)

    def build(self):
        edit = ttk.Frame(self, padding=(0, 0, 0, 4))
        edit.pack(fill='x')
        ttk.Label(edit, text=tr('BPM')).pack(side='left')
        self.bpm_entry = ttk.Entry(edit, textvariable=self.bpm, width=8)
        self.bpm_entry.pack(side='left', padx=(4, 12))
        for key, amount in (('<Up>', 0.1), ('<Right>', 0.1), ('<Down>', -0.1), ('<Left>', -0.1)):
            self.bpm_entry.bind(key, lambda _event, amount=amount: self.step_bpm(amount))
        ttk.Label(edit, text=tr('第一拍 offset (秒)')).pack(side='left')
        self.offset_entry = ttk.Entry(edit, textvariable=self.offset, width=8)
        self.offset_entry.pack(side='left', padx=4)
        for key, amount in (('<Up>', 0.1), ('<Down>', -0.1), ('<Right>', 0.005), ('<Left>', -0.005)):
            self.offset_entry.bind(key, lambda _event, amount=amount: self.step_offset_seconds(amount))
        self.minus_button = ttk.Button(edit, text=tr('−1 beat'), command=lambda: self.step_offset(-1))
        self.minus_button.pack(side='left', padx=2)
        self.plus_button = ttk.Button(edit, text=tr('+1 beat'), command=lambda: self.step_offset(1))
        self.plus_button.pack(side='left', padx=2)
        self.apply_button = ttk.Button(edit, text=tr('Apply'), command=self.apply)
        self.apply_button.pack(side='left', padx=(12, 2))
        ttk.Label(edit, textvariable=self.target_text, foreground='gray').pack(side='left', padx=8)

        play = ttk.Frame(self, padding=(0, 0, 0, 4))
        play.pack(fill='x')
        self.play_button = ttk.Button(play, text=tr('Play'), command=self.toggle_playback)
        self.play_button.pack(side='left', padx=(0, 2))
        ttk.Label(play, text=tr('MIDI Output')).pack(side='left', padx=(12, 4))
        outputs = output_names()
        if self.output_var.get() not in outputs:
            outputs.insert(0, self.output_var.get())
        selector = ttk.Combobox(play, textvariable=self.output_var, values=outputs, width=22, state='readonly')
        selector.pack(side='left')
        selector.bind('<<ComboboxSelected>>', self.change_output)
        for hand in HAND_ORDER:
            ttk.Checkbutton(play, text=tr(hand.replace('_', ' ')), variable=self.hand_enabled[hand],
                            command=self.apply_mix).pack(side='left', padx=(8, 0))
        ttk.Checkbutton(play, text=tr('Velocity shading'), variable=self.shading,
                        command=self.draw).pack(side='left', padx=(12, 0))
        ttk.Label(play, text=tr('Change overlay')).pack(side='left', padx=(12, 4))
        self.change_modes = {tr(label): mode for label, mode in (
            ('Hide changes', 'none'), ('Deleted notes', 'deleted'),
            ('Shortened tails', 'shortened'), ('All changes', 'all'))}
        selector = ttk.Combobox(play, textvariable=self.change_mode, values=tuple(self.change_modes),
                                width=14, state='readonly')
        selector.pack(side='left')
        selector.bind('<<ComboboxSelected>>', lambda _event: self.draw())

        roll = ttk.Frame(self)
        roll.pack(fill='both', expand=True)
        self.ruler = tk.Canvas(roll, height=RULER_HEIGHT, background='#eef1f5', highlightthickness=0,
                               xscrollincrement=HORIZONTAL_SCROLL_INCREMENT)
        self.keys = tk.Canvas(roll, width=KEYS_WIDTH, background='white', highlightthickness=0,
                              yscrollincrement=ROW_HEIGHT)
        self.canvas = tk.Canvas(roll, width=800, height=360, background='#fbfcfe', highlightthickness=0,
                                xscrollincrement=HORIZONTAL_SCROLL_INCREMENT, yscrollincrement=ROW_HEIGHT)
        self.pedal_labels = tk.Canvas(roll, width=KEYS_WIDTH, height=PEDAL_ROW_HEIGHT, background='white',
                                      highlightthickness=0)
        self.pedal = tk.Canvas(roll, height=PEDAL_ROW_HEIGHT, background='#f6f7f9', highlightthickness=0,
                               xscrollincrement=HORIZONTAL_SCROLL_INCREMENT)
        vertical = ttk.Scrollbar(roll, orient='vertical', command=self.yview)
        horizontal = ttk.Scrollbar(roll, orient='horizontal', command=self.xview)
        self.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        self.canvas.bind('<Configure>', self.center_initial_pitch)
        self.canvas.bind('<Motion>', self.show_change_hint)
        self.canvas.bind('<Leave>', lambda _event: self.reset_change_hint())
        self.ruler.grid(row=0, column=1, sticky='ew')
        self.keys.grid(row=1, column=0, sticky='ns')
        self.canvas.grid(row=1, column=1, sticky='nsew')
        vertical.grid(row=1, column=2, sticky='ns')
        self.pedal_labels.grid(row=2, column=0, sticky='ns')
        self.pedal.grid(row=2, column=1, sticky='ew')
        horizontal.grid(row=3, column=1, sticky='ew')
        roll.rowconfigure(1, weight=1)
        roll.columnconfigure(1, weight=1)

        for widget in (self.canvas, self.ruler, self.pedal):
            widget.bind('<Button-1>', self.seek_click)
        for widget in (self.canvas, self.keys, self.ruler, self.pedal):
            widget.bind('<MouseWheel>', self.wheel)
        self.set_editable(False)

    # Scrolling and zoom keep every canvas on the same time or pitch axis.
    def xview(self, *args):
        for widget in (self.canvas, self.ruler, self.pedal):
            widget.xview(*args)

    def yview(self, *args):
        for widget in (self.canvas, self.keys):
            widget.yview(*args)

    def center_initial_pitch(self, _event=None):
        """Center a newly loaded song once the viewport has its actual dimensions."""
        if not self.center_pending or not self.canvas.winfo_ismapped():
            return
        height = self.canvas.winfo_height()
        width = self.canvas.winfo_width()
        if height <= 1 or width <= 1:
            return
        left = self.canvas.canvasx(0) / self.pixels_per_second
        right = self.canvas.canvasx(width) / self.pixels_per_second
        pitches = [pitch for start, end, pitch, _, _ in self.notes if start < right and end > left]
        if not pitches:
            pitches = [pitch for _, _, pitch, _, _ in self.notes] or [60]
        middle_pitch = (min(pitches) + max(pitches)) / 2
        center = (PITCHES - 1 - middle_pitch + 0.5) * ROW_HEIGHT
        total_height = PITCHES * ROW_HEIGHT
        top = max(0, min(max(0, total_height - height), center - height / 2))
        self.yview('moveto', top / total_height)
        self.center_pending = False

    def wheel(self, event):
        step = -1 if event.delta > 0 else 1
        if event.state & 0x0004 or event.state & 0x20000:
            self.zoom(1.15 if event.delta > 0 else 1 / 1.15)
        elif event.state & 0x0001:
            self.xview('scroll', step, 'units')
        else:
            self.yview('scroll', step, 'units')
        return 'break'

    def zoom(self, factor):
        left = self.canvas.canvasx(0) / self.pixels_per_second
        self.pixels_per_second = max(20.0, min(500.0, self.pixels_per_second * factor))
        self.draw()
        self.xview('moveto', left * self.pixels_per_second / self.width())

    def width(self):
        end = max([self.duration] + [item['end'] for item in self.changes or []])
        return max(800.0, end * self.pixels_per_second + 80)

    # Loading.
    def show(self, midi_path):
        """Load midi_path unless the same file version is already shown."""
        if not midi_path or not os.path.isfile(midi_path):
            self.stop()
            self.center_pending = False
            self.path = None
            self.changes = None
            self.changes_mtime = None
            self.notes, self.pedal_rows, self.duration = [], [], 0.0
            self.set_editable(False)
            self.target_text.set('')
            self.draw()
            self.status.set(tr('Choose a MIDI file first.'))
            return
        mtime = os.path.getmtime(midi_path)
        record_path = changes_path(midi_path)
        record_mtime = record_path.stat().st_mtime_ns if record_path.is_file() else None
        if midi_path == self.path and mtime == self.loaded_mtime:
            if record_mtime != self.changes_mtime:
                self.changes = load_changes(midi_path)
                self.changes_mtime = record_mtime
                self.draw()
            return
        same_file = midi_path == self.path
        view = (self.canvas.xview()[0], self.canvas.yview()[0])
        self.stop()
        self.notes, pedals, self.duration, bpm = read_roll(midi_path)
        self.path, self.loaded_mtime = midi_path, mtime
        self.changes = load_changes(midi_path)
        self.changes_mtime = record_mtime
        streams = defaultdict(list)
        for channel, intervals in sorted(pedals.items()):
            streams[tuple(intervals)].append(channel)
        self.pedal_rows = [(channels, list(intervals)) for intervals, channels in streams.items()]
        self.play_start_seconds = 0.0
        self.bpm.set('{:.2f}'.format(bpm))
        self.offset.set('0')
        self.target = first_beat_target(midi_path)
        self.set_editable(self.target is not None)
        self.target_text.set(tr('First beat is saved to {}').format(os.path.basename(self.target)) if self.target
                             else tr('First beat editing needs song_bpmfix.mid'))
        self.draw()
        if same_file:
            self.xview('moveto', view[0])
            self.yview('moveto', view[1])
        else:
            self.xview('moveto', 0)
            self.center_pending = True
            self.after_idle(self.center_initial_pitch)
        self.status.set(tr('Loaded: {}').format(os.path.basename(midi_path)))

    def set_editable(self, editable):
        state = 'normal' if editable else 'disabled'
        for widget in (self.bpm_entry, self.offset_entry, self.minus_button, self.plus_button, self.apply_button):
            widget.configure(state=state)

    # Drawing.
    def reset_change_hint(self):
        mode = self.change_modes[self.change_mode.get()]
        if mode == 'none':
            return
        elif self.changes is None:
            text = tr('No matching change record. Run processing to record changes.')
        elif not self.changes:
            text = tr('No notes were deleted or shortened.')
        else:
            text = tr('Red: deleted notes   Orange: shortened tails')
        self.status.set(text)

    def show_change_hint(self, _event):
        current = self.canvas.find_withtag('current')
        change = self.change_items.get(current[0]) if current else None
        if change is None:
            self.reset_change_hint()
            return
        stage = {'clean': 'Rule cleaning', 'playable': 'Playable reduction (one player, 2 hands)',
                 'trim': 'Overlap trim'}[change['stage']]
        self.status.set(tr('{} | {} | Velocity {}').format(
            tr(stage), note_name(change['pitch']), change['velocity']))

    def draw(self):
        width = self.width()
        height = PITCHES * ROW_HEIGHT
        rows = max(1, len(self.pedal_rows))
        for widget in (self.canvas, self.keys, self.ruler, self.pedal, self.pedal_labels):
            widget.delete('all')
        self.canvas.configure(scrollregion=(0, 0, width, height))
        self.keys.configure(scrollregion=(0, 0, KEYS_WIDTH, height))
        self.ruler.configure(scrollregion=(0, 0, width, RULER_HEIGHT))
        self.pedal.configure(scrollregion=(0, 0, width, rows * PEDAL_ROW_HEIGHT), height=rows * PEDAL_ROW_HEIGHT)
        self.pedal_labels.configure(height=rows * PEDAL_ROW_HEIGHT)

        for pitch in range(PITCHES):
            y = (PITCHES - 1 - pitch) * ROW_HEIGHT
            black = pitch % 12 in (1, 3, 6, 8, 10)
            if black:
                self.canvas.create_rectangle(0, y, width, y + ROW_HEIGHT, fill='#f1f3f6', outline='', tags='bg')
            self.keys.create_rectangle(0, y, KEYS_WIDTH, y + ROW_HEIGHT, fill='#3a3f47' if black else 'white',
                                       outline='#d5dae0')
            if pitch % 12 == 0:
                self.canvas.create_line(0, y + ROW_HEIGHT, width, y + ROW_HEIGHT, fill='#dce2e8', tags='bg')
                self.keys.create_text(KEYS_WIDTH - 3, y + ROW_HEIGHT / 2, text=note_name(pitch), anchor='e',
                                      font=('TkDefaultFont', 7), fill='#435365')

        pps = self.pixels_per_second
        self.change_items.clear()
        self.reset_change_hint()
        mode = self.change_modes[self.change_mode.get()]
        for change in self.changes or []:
            if mode not in ('all', change['kind']):
                continue
            deleted = change['kind'] == 'deleted'
            y = (PITCHES - 1 - change['pitch']) * ROW_HEIGHT + 1
            item = self.canvas.create_rectangle(
                change['start'] * pps, y, max(change['end'] * pps, change['start'] * pps + 3),
                y + ROW_HEIGHT - 1, fill='#f4b8b8' if deleted else '#f4cf9b',
                outline='#bd6262' if deleted else '#c28a3c', stipple='gray50',
                dash=(3, 2) if deleted else (), tags='change')
            self.change_items[item] = change
        for start, end, pitch, channel, velocity in self.notes:
            color = HAND_COLORS.get(channel, (OTHER_COLOR,))[0]
            if self.shading.get():
                color = shade(color, velocity)
            y = (PITCHES - 1 - pitch) * ROW_HEIGHT + 1
            self.canvas.create_rectangle(start * pps, y, max(end * pps, start * pps + 3), y + ROW_HEIGHT - 1,
                                         fill=color, outline='', tags='note')

        for row, (channels, intervals) in enumerate(self.pedal_rows):
            y = row * PEDAL_ROW_HEIGHT
            names = sorted({HAND_BY_CHANNEL.get(channel, '') for channel in channels} - {''},
                           key=lambda hand: HAND_ORDER.index(hand))
            label = tr('Pedal') if len(self.pedal_rows) == 1 else '/'.join(
                tr(hand.replace('_', ' ')) for hand in names) or tr('Pedal')
            self.pedal_labels.create_text(KEYS_WIDTH - 3, y + PEDAL_ROW_HEIGHT / 2, text=label, anchor='e',
                                          font=('TkDefaultFont', 7), fill='#435365')
            for down, up in intervals:
                self.pedal.create_rectangle(down * pps, y + 2, max(up * pps, down * pps + 2),
                                            y + PEDAL_ROW_HEIGHT - 2, fill='#8a94a6', outline='')
        self.playhead = self.canvas.create_line(0, 0, 0, height, fill='#e53935', width=2)
        self.pedal_playhead = self.pedal.create_line(0, 0, 0, rows * PEDAL_ROW_HEIGHT, fill='#e53935', width=2)
        self.draw_grid()
        self.move_playhead(self.position_seconds())

    def draw_grid(self):
        """Bar, beat and 16th lines from the BPM and first beat fields, so edits preview before saving."""
        self.canvas.delete('grid')
        self.ruler.delete('all')
        try:
            beat = 60.0 / float(self.bpm.get())
            offset = float(self.offset.get())
        except (ValueError, ZeroDivisionError):
            return
        if beat <= 0:
            return
        pps = self.pixels_per_second
        height = PITCHES * ROW_HEIGHT
        sixteenth = beat / 4
        first = math.floor(-offset / sixteenth)
        last = math.ceil((self.duration - offset) / sixteenth)
        for index in range(first, last + 1):
            x = (offset + index * sixteenth) * pps
            if index % 16 == 0:
                self.canvas.create_line(x, 0, x, height, fill='#8996a5', width=2, tags='grid')
                self.ruler.create_line(x, 0, x, RULER_HEIGHT, fill='#8996a5')
                if index >= 0:
                    self.ruler.create_text(x + 3, RULER_HEIGHT / 2, text=str(index // 16 + 1), anchor='w',
                                           fill='#435365')
            elif index % 4 == 0:
                self.canvas.create_line(x, 0, x, height, fill='#d3d9e0', tags='grid')
                self.ruler.create_line(x, RULER_HEIGHT * 0.6, x, RULER_HEIGHT, fill='#b8c0ca')
            elif pps * sixteenth >= 6:
                self.canvas.create_line(x, 0, x, height, fill='#eef1f4', tags='grid')
        if self.canvas.find_withtag('bg'):
            self.canvas.tag_raise('grid', 'bg')
        if self.canvas.find_withtag('note'):
            self.canvas.tag_lower('grid', 'note')

    def step_bpm(self, amount):
        try:
            value = float(self.bpm.get()) + amount
            if value <= 0:
                return 'break'
            self.bpm.set('{:.2f}'.format(value))
        except ValueError:
            self.status.set(tr('請輸入有效 BPM 和 offset'))
        return 'break'

    def step_offset_seconds(self, amount):
        try:
            self.offset.set('{:.4f}'.format(float(self.offset.get()) + amount))
        except ValueError:
            self.status.set(tr('請輸入有效 BPM 和 offset'))
        return 'break'

    def step_offset(self, direction):
        try:
            beat = 60.0 / float(self.bpm.get())
            self.offset.set('{:.3f}'.format(float(self.offset.get()) + direction * beat))
        except (ValueError, ZeroDivisionError):
            self.status.set(tr('請輸入有效 BPM 和 offset'))

    # Playback.
    def ensure_playback(self):
        if self.playback is None:
            self.output, name = open_output(self.output_var.get())
            self.playback = MidiPlayback(self.output)
            if self.output is None:
                self.status.set(tr('No sound: {} is not available').format(name))
                return False
        if self.output is None:
            return False
        if getattr(self.output, 'error', None):
            self.status.set(tr('Could not start playback: {}').format(self.output.error))
            return False
        if not getattr(self.output, 'ready', True):
            self.status.set(tr('Preparing Coopiano...'))
            return False
        return True

    def change_output(self, _event=None):
        self.release_output()
        if self.on_output:
            self.on_output(self.output_var.get())

    def release_output(self):
        self.stop()
        if self.output is not None:
            self.output.close()
        self.output = None
        self.playback = None

    def toggle_playback(self):
        if self.play_started is not None or self.play_after_id is not None:
            self.stop()
        else:
            self.play()

    def play(self):
        if self.play_after_id is not None:
            self.after_cancel(self.play_after_id)
        self.play_after_id = None
        self.play_button.configure(text=tr('Play'))
        if not self.path:
            return
        if not self.ensure_playback():
            if self.output is not None and not getattr(self.output, 'error', None):
                self.play_after_id = self.after(100, self.play)
                self.play_button.configure(text=tr('Pause'))
            return
        self.status.set(tr('Playback output: {}').format(self.output_var.get()))
        position = self.position_seconds()
        self.playback.play(self.path, position)
        self.apply_mix()
        self.play_started = time.monotonic()
        self.play_start_seconds = position
        self.play_button.configure(text=tr('Pause'))

    def apply_mix(self):
        if self.playback is not None:
            self.playback.set_mix([hand for hand, enabled in self.hand_enabled.items() if not enabled.get()], None)

    def stop(self):
        if self.play_after_id is not None:
            self.after_cancel(self.play_after_id)
            self.play_after_id = None
        if self.playback is not None:
            self.playback.stop()
        self.play_start_seconds = self.position_seconds()
        self.play_started = None
        self.play_button.configure(text=tr('Play'))

    def shutdown(self):
        if self.after_id is not None:
            self.after_cancel(self.after_id)
            self.after_id = None
        self.release_output()

    def position_seconds(self):
        if self.play_started is None:
            return self.play_start_seconds
        return min(self.duration, self.play_start_seconds + time.monotonic() - self.play_started)

    def seek_click(self, event):
        seconds = max(0.0, event.widget.canvasx(event.x) / self.pixels_per_second)
        playing = self.play_started is not None
        self.stop()
        self.play_start_seconds = min(seconds, self.duration)
        self.move_playhead(self.play_start_seconds)
        if playing:
            self.play()

    def move_playhead(self, seconds):
        x = seconds * self.pixels_per_second
        self.canvas.coords(self.playhead, x, 0, x, PITCHES * ROW_HEIGHT)
        self.pedal.coords(self.pedal_playhead, x, 0, x, max(1, len(self.pedal_rows)) * PEDAL_ROW_HEIGHT)
        return x

    def update_playhead(self):
        if self.play_started is not None and getattr(self.output, 'error', None):
            self.stop()
            self.status.set(tr('Could not start playback: {}').format(self.output.error))
        if self.play_started is not None:
            seconds = self.position_seconds()
            x = self.move_playhead(seconds)
            right = self.canvas.canvasx(self.canvas.winfo_width())
            if x > right - 40 or x < self.canvas.canvasx(0):
                self.xview('moveto', max(0.0, (x - 40) / self.width()))
            if seconds >= self.duration:
                self.stop()
        self.after_id = self.after(50, self.update_playhead)

    # First beat.
    def apply(self):
        if not self.target:
            return
        try:
            bpm = float(self.bpm.get())
            offset = float(self.offset.get())
            if bpm <= 0:
                raise ValueError(tr('BPM 必須大於 0'))
        except ValueError as error:
            self.status.set(tr('輸入錯誤：{}').format(error))
            return
        self.stop()
        try:
            result = build_bpm_fixed_midi(self.target, bpm, offset)
            append_first_beat_stats(self.target, result)
            # Shifting moves the old key signature events off the bar lines.
            apply_key_signatures(self.target)
        except (OSError, ValueError) as error:
            self.status.set(tr('寫入失敗：{}').format(error))
            return
        target = self.target
        self.show(target)
        self.status.set(tr('Saved the first beat to {}. Run the processing again to rebuild the cleaned MIDI.')
                        .format(os.path.basename(target)))
        if self.on_first_beat:
            self.on_first_beat(target)
