import json
import math
import mimetypes
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import mido


PAGE_PATH = Path(__file__).with_name('first_beat_ui.html')
BEATS_PER_BAR = 4
DEFAULT_TEMPO = 500000
LEFT_HAND_CHANNEL = 2


def midi_tempo(midi_file):
    for track in midi_file.tracks:
        for message in track:
            if message.type == 'set_tempo':
                return message.tempo
    return DEFAULT_TEMPO


def earliest_note_tick(midi_file):
    earliest = None
    for track in midi_file.tracks:
        absolute_tick = 0
        for message in track:
            absolute_tick += message.time
            if message.type == 'note_on' and message.velocity > 0:
                if earliest is None or absolute_tick < earliest:
                    earliest = absolute_tick
    return earliest


def read_notes(midi_file):
    """Return [start_seconds, end_seconds, pitch, hand] for a single-tempo MIDI."""
    tempo = midi_tempo(midi_file)
    notes = []
    for track in midi_file.tracks:
        active = {}
        absolute_tick = 0
        for message in track:
            absolute_tick += message.time
            if message.type not in ('note_on', 'note_off'):
                continue
            key = (message.channel, message.note)
            seconds = mido.tick2second(absolute_tick, midi_file.ticks_per_beat, tempo)
            if message.type == 'note_on' and message.velocity > 0:
                active.setdefault(key, []).append(seconds)
            elif active.get(key):
                start = active[key].pop()
                hand = 'left' if message.channel == LEFT_HAND_CHANNEL else 'right'
                notes.append([round(start, 4), round(seconds, 4), message.note, hand])
    notes.sort()
    return notes


def shift_midi_first_beat(midi_path, first_beat_seconds):
    """Move first_beat_seconds to a bar line in place; add whole bars if notes would start before 0."""
    midi_file = mido.MidiFile(midi_path)
    tempo = midi_tempo(midi_file)
    bar_ticks = midi_file.ticks_per_beat * BEATS_PER_BAR
    offset_ticks = int(round(mido.second2tick(first_beat_seconds, midi_file.ticks_per_beat, tempo)))

    shift_ticks = -offset_ticks
    padding_bars = 0
    earliest = earliest_note_tick(midi_file)
    if earliest is not None and earliest + shift_ticks < 0:
        padding_bars = math.ceil(-(earliest + shift_ticks) / bar_ticks)
        shift_ticks += padding_bars * bar_ticks

    for track in midi_file.tracks:
        absolute_tick = 0
        previous_tick = 0
        for message in track:
            absolute_tick += message.time
            shifted_tick = 0 if absolute_tick == 0 else max(0, absolute_tick + shift_ticks)
            message.time = shifted_tick - previous_tick
            previous_tick = shifted_tick

    midi_file.save(midi_path)
    return {
        'first_beat_seconds': first_beat_seconds,
        'shift_ticks': shift_ticks,
        'padding_bars': padding_bars,
    }


def format_first_beat_stats(midi_path, result):
    if result is None:
        return '\n'.join([
            'MIDI first beat stats',
            'MIDI: {}'.format(midi_path),
            'First beat: not changed',
        ])
    return '\n'.join([
        'MIDI first beat stats',
        'MIDI: {}'.format(midi_path),
        'First beat in source audio: {:.3f}s'.format(result['first_beat_seconds']),
        'Shift ticks: {}'.format(result['shift_ticks']),
        'Padding bars: {}'.format(result['padding_bars']),
    ])


def make_handler(session, finish):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send_bytes(self, body, content_type):
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, data):
            self.send_bytes(json.dumps(data).encode('utf-8'), 'application/json')

        def do_GET(self):
            if self.path == '/':
                self.send_bytes(PAGE_PATH.read_bytes(), 'text/html; charset=utf-8')
            elif self.path == '/session':
                self.send_json(session['info'])
            elif self.path == '/audio':
                content_type = mimetypes.guess_type(session['audio_path'])[0] or 'application/octet-stream'
                self.send_bytes(Path(session['audio_path']).read_bytes(), content_type)
            else:
                self.send_error(404)

        def do_POST(self):
            length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(length) or b'{}')
            if self.path == '/save':
                result = shift_midi_first_beat(session['midi_path'], float(payload['first_beat']))
                self.send_json(result)
                finish(result)
            elif self.path == '/cancel':
                self.send_json({})
                finish(None)
            else:
                self.send_error(404)

    return Handler


def run_first_beat_ui(audio_path, midi_path, open_browser=True, port=0):
    """Serve the first beat page until the user saves or cancels; return the shift result or None."""
    midi_file = mido.MidiFile(midi_path)
    session = {
        'audio_path': audio_path,
        'midi_path': midi_path,
        'info': {
            'bpm': mido.tempo2bpm(midi_tempo(midi_file)),
            'beats_per_bar': BEATS_PER_BAR,
            'notes': read_notes(midi_file),
            'audio_name': os.path.basename(audio_path),
            'midi_name': os.path.basename(midi_path),
        },
    }
    outcome = {}

    server = ThreadingHTTPServer(('127.0.0.1', port), None)

    def finish(result):
        outcome['result'] = result
        threading.Thread(target=server.shutdown).start()

    server.RequestHandlerClass = make_handler(session, finish)
    url = 'http://127.0.0.1:{}/'.format(server.server_address[1])
    print('First beat page: {}'.format(url))
    print('Set the first beat in the browser, then press Save or Cancel there.')
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return outcome.get('result')
