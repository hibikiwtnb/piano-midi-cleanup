"""Compact Tk Canvas piano roll for first-beat adjustment."""

import math
import time
import tkinter as tk
from tkinter import ttk

import mido

from gui_i18n import tr
from first_beat import build_bpm_fixed_midi, midi_tempo
from midi_playback import MidiPlayback, open_output, output_names, read_midi_events


class PianoRollWorkbench:
    def __init__(self, root, midi_path, midi_port=None, on_complete=None):
        self.root = root
        self.midi_path = midi_path
        self.on_complete = on_complete
        self.output, self.backend = open_output(midi_port)
        self.playback = MidiPlayback(self.output)
        self.outputs = output_names()
        if self.backend not in self.outputs:
            self.outputs.insert(0, self.backend)
        self.output_var = tk.StringVar(value=self.backend)
        self.bpm = tk.StringVar()
        self.offset = tk.StringVar(value='0')
        self.status = tk.StringVar(value=tr('就緒 · {}').format(self.backend))
        self.pixels_per_second = 100.0
        self.row_height = 9
        self.notes, self.duration = self.read_notes()
        midi = mido.MidiFile(midi_path)
        self.bpm.set('{:.2f}'.format(mido.tempo2bpm(midi_tempo(midi))))

        root.title(tr('First Beat · {}').format(midi_path))
        root.geometry('1000x620')
        controls = ttk.Frame(root, padding=8)
        controls.pack(fill='x')
        ttk.Label(controls, text=tr('BPM')).pack(side='left')
        ttk.Entry(controls, textvariable=self.bpm, width=8).pack(side='left', padx=(4, 12))
        ttk.Label(controls, text=tr('第一拍 offset (秒)')).pack(side='left')
        ttk.Entry(controls, textvariable=self.offset, width=8).pack(side='left', padx=4)
        ttk.Button(controls, text=tr('−1 beat'), command=lambda: self.step_offset(-1)).pack(side='left', padx=2)
        ttk.Button(controls, text=tr('+1 beat'), command=lambda: self.step_offset(1)).pack(side='left', padx=2)
        ttk.Button(controls, text=tr('Play'), command=self.play).pack(side='left', padx=(12, 2))
        ttk.Button(controls, text=tr('Pause'), command=self.pause).pack(side='left', padx=2)
        ttk.Button(controls, text=tr('Apply'), command=self.apply).pack(side='right', padx=2)
        ttk.Button(controls, text=tr('Cancel'), command=self.cancel).pack(side='right', padx=2)
        output_row = ttk.Frame(root, padding=(8, 0, 8, 4))
        output_row.pack(fill='x')
        ttk.Label(output_row, text=tr('MIDI Output')).pack(side='left')
        self.output_selector = ttk.Combobox(
            output_row, textvariable=self.output_var, values=self.outputs,
            width=38, state='readonly' if self.outputs else 'disabled',
        )
        self.output_selector.pack(side='left', padx=(8, 0))
        self.output_selector.bind('<<ComboboxSelected>>', self.change_output)
        ttk.Label(root, textvariable=self.status, padding=(8, 0)).pack(fill='x')

        container = ttk.Frame(root, padding=8)
        container.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(container, background='#fbfcfe', highlightthickness=0,
                                xscrollincrement=24, yscrollincrement=self.row_height)
        horizontal = ttk.Scrollbar(container, orient='horizontal', command=self.canvas.xview)
        vertical = ttk.Scrollbar(container, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        self.canvas.bind('<Button-1>', self.seek_click)
        self.canvas.bind('<Control-MouseWheel>', self.zoom)
        self.canvas.bind('<Alt-MouseWheel>', self.zoom)
        self.canvas.bind('<Shift-MouseWheel>', lambda event: self.canvas.xview_scroll(-1 if event.delta > 0 else 1, 'units'))
        self.canvas.bind('<MouseWheel>', self.scroll_vertical)
        self.draw()
        self.play_started = None
        self.play_start_seconds = 0.0
        self.closed = False
        root.protocol('WM_DELETE_WINDOW', self.cancel)
        root.after(100, self.update_playhead)

    def read_notes(self):
        events, _, duration = read_midi_events(self.midi_path)
        active = {}
        notes = []
        for event in events:
            message = event.message
            if message.type == 'note_on' and message.velocity > 0:
                active.setdefault((message.channel, message.note), []).append((event.seconds, event.track_id))
            elif message.type in ('note_on', 'note_off'):
                starts = active.get((message.channel, message.note))
                if starts:
                    start, hand = starts.pop(0)
                    notes.append((start, max(event.seconds, start + 0.01), message.note, hand))
        return notes, max(duration, max((note[1] for note in notes), default=0.0))

    def draw(self):
        self.canvas.delete('all')
        height = 128 * self.row_height
        width = max(800, self.duration * self.pixels_per_second + 80)
        self.canvas.configure(scrollregion=(0, 0, width, height))
        for pitch in range(128):
            y = (127 - pitch) * self.row_height
            if pitch % 12 in (1, 3, 6, 8, 10):
                self.canvas.create_rectangle(0, y, width, y + self.row_height, fill='#f1f3f6', outline='')
            if pitch % 12 == 0:
                self.canvas.create_line(0, y, width, y, fill='#dce2e8')
        try:
            beat = 60.0 / float(self.bpm.get())
        except (ValueError, ZeroDivisionError):
            beat = 0.5
        tick = beat / 4
        for index in range(math.ceil(self.duration / tick) + 1):
            seconds = index * tick
            x = 64 + seconds * self.pixels_per_second
            major = index % 16 == 0
            self.canvas.create_line(x, 0, x, height, fill='#8996a5' if major else '#e7ebef', width=2 if major else 1)
            if index % 4 == 0:
                self.canvas.create_text(x + 3, 4, text=str(index // 4 + 1), anchor='nw', fill='#435365')
        for start, end, pitch, hand in self.notes:
            x1 = 64 + start * self.pixels_per_second
            x2 = 64 + end * self.pixels_per_second
            y1 = (127 - pitch) * self.row_height + 1
            color = '#1976d2' if hand == 'right' else '#008b8b'
            self.canvas.create_rectangle(x1, y1, max(x2, x1 + 3), y1 + self.row_height - 1,
                                         fill=color, outline='')
        self.playhead = self.canvas.create_line(64, 0, 64, height, fill='#e53935', width=2)

    def step_offset(self, direction):
        try:
            beat_seconds = 60.0 / float(self.bpm.get())
            self.offset.set('{:.3f}'.format(float(self.offset.get()) + direction * beat_seconds))
        except (ValueError, ZeroDivisionError):
            self.status.set(tr('請輸入有效 BPM 和 offset'))

    def play(self):
        position = self.position_seconds()
        self.playback.play(self.midi_path, position)
        self.play_started = time.monotonic()
        self.play_start_seconds = position

    def pause(self):
        self.playback.stop()
        self.play_started = None
        self.play_started = None

    def change_output(self, _event=None):
        selected = self.output_var.get()
        if selected == self.backend:
            return
        self.playback.stop()
        previous_output = self.output
        self.output, self.backend = open_output(selected)
        if previous_output:
            previous_output.close()
        self.playback = MidiPlayback(self.output)
        self.status.set(tr('就緒 · {}').format(self.backend))

    def position_seconds(self):
        if self.play_started is None:
            return self.play_start_seconds
        return min(self.duration, self.play_start_seconds + time.monotonic() - self.play_started)

    def seek_click(self, event):
        seconds = max(0, (self.canvas.canvasx(event.x) - 64) / self.pixels_per_second)
        self.pause()
        self.play_start_seconds = seconds
        self.update_playhead()

    def update_playhead(self):
        if self.closed:
            return
        seconds = self.position_seconds()
        x = 64 + seconds * self.pixels_per_second
        self.canvas.coords(self.playhead, x, 0, x, 128 * self.row_height)
        viewport_right = self.canvas.canvasx(self.canvas.winfo_width())
        if x > viewport_right - 40:
            region = self.canvas.cget('scrollregion').split()
            if len(region) == 4:
                total_width = float(region[2])
                self.canvas.xview_moveto(min(1.0, x / total_width))
        if self.play_started is not None and seconds >= self.duration:
            self.play_started = None
        self.root.after(50, self.update_playhead)

    def zoom(self, event):
        self.pixels_per_second = max(20, min(500, self.pixels_per_second * (1.15 if event.delta > 0 else 1 / 1.15)))
        self.draw()
        return 'break'

    def scroll_vertical(self, event):
        if not (event.state & 0x0004 or event.state & 0x0008):
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')

    def apply(self):
        try:
            bpm = float(self.bpm.get())
            offset = float(self.offset.get())
            if bpm <= 0:
                raise ValueError(tr('BPM 必須大於 0'))
        except ValueError as error:
            self.status.set(tr('輸入錯誤：{}').format(error))
            return
        self.pause()
        try:
            result = build_bpm_fixed_midi(self.midi_path, bpm, offset)
        except (OSError, ValueError) as error:
            self.status.set(tr('寫入失敗：{}').format(error))
            return
        self.status.set(tr('已寫入 MIDI · BPM {:.2f}').format(bpm))
        self.close(result)

    def cancel(self):
        self.pause()
        self.close(None)

    def close(self, result):
        self.result = result
        self.closed = True
        if self.output:
            self.output.close()
        self.root.destroy()
        if self.on_complete:
            self.on_complete(result)
