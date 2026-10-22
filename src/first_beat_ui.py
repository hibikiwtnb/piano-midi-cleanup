"""Local workbench for setting BPM and the first beat of a BPM-fixed MIDI.

The page is the vsti-webui-wes7-pcm MIDI workbench (web/result.js, web/result.css);
playback goes to a MIDI output port in realtime through midi_playback.
Saving rewrites the MIDI in place.
"""

import html
import json
import math
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import mido

from bpmfix_utils import estimate_first_beat, format_auto_first_beat_stats
from midi_stats import source_stats_path_for
from midi_playback import MidiPlayback, open_output, read_midi_events


ROOT = Path(__file__).resolve().parent.parent
STATIC_FILES = {
    '/static/result.js': (ROOT / 'web' / 'result.js', 'text/javascript; charset=utf-8'),
    '/static/result.css': (ROOT / 'web' / 'result.css', 'text/css; charset=utf-8'),
    '/static/tonal.min.js': (ROOT / 'vendor' / 'tonal.min.js', 'text/javascript; charset=utf-8'),
}
DEFAULT_MIDI_TEMPO = 500000
TRACK_COLORS = ('#1976d2', '#008b8b')
HANDS = (
    ('right', 'Right hand', 0),
    ('left', 'Left hand', 2),
)
STRINGS = {
    'play': '播放',
    'pause': '暫停',
    'follow': '跟隨',
    'original': '原音',
    'instruments': '樂器',
    'not_detected': '未偵測',
    'solo': '獨奏',
    'mute': '靜音',
    'ready': '就緒',
    'linked_source': '{track} · {backend}',
    'zoom_help': 'Ctrl/Alt + 滾輪縮放，Shift + 滾輪橫向捲動',
}


def midi_tempo(midi_file):
    for track in midi_file.tracks:
        for message in track:
            if message.type == 'set_tempo':
                return message.tempo
    return DEFAULT_MIDI_TEMPO


def read_roll_notes(midi_path):
    events, tracks, duration = read_midi_events(midi_path)
    active = {}
    notes = []
    for event in events:
        message = event.message
        if message.type not in ('note_on', 'note_off'):
            continue
        key = (message.channel, message.note)
        if message.type == 'note_on' and message.velocity > 0:
            active.setdefault(key, []).append((event.seconds, message.velocity))
            continue
        starts = active.get(key)
        if not starts:
            continue
        start, velocity = starts.pop(0)
        if event.seconds > start:
            notes.append({
                'instrument': event.track_id,
                'pitch': message.note,
                'velocity': velocity,
                'start': start,
                'end': event.seconds,
            })
    notes.sort(key=lambda note: (note['start'], note['pitch']))
    duration = max(duration, max((note['end'] for note in notes), default=0.0))
    return notes, tracks, duration


def build_manifest(midi_path, midi_url, backend_label):
    notes, tracks, duration = read_roll_notes(midi_path)
    instruments = [
        {
            'id': hand,
            'label': label,
            'detected': hand in tracks,
            'color': TRACK_COLORS[index],
            'midi': [{'channel': channel, 'program': 0, 'program_name': 'Acoustic Grand Piano'}],
        }
        for index, (hand, label, channel) in enumerate(HANDS)
    ]
    return {
        'notes': notes,
        'duration': duration,
        'bpm': mido.tempo2bpm(midi_tempo(mido.MidiFile(midi_path))),
        'backendLabel': backend_label,
        'sourceTrackName': '',
        'instruments': instruments,
        'downloads': {'midi': midi_url},
        'fileId': '',
        'strings': STRINGS,
    }


def build_page(midi_path, midi_url, backend_label):
    manifest = html.escape(
        json.dumps(build_manifest(midi_path, midi_url, backend_label), ensure_ascii=False), quote=False)
    return '\n'.join([
        '<!doctype html>',
        '<html lang="zh-Hant">',
        '<head>',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<title>{}</title>'.format(html.escape(Path(midi_path).name)),
        '<link rel="icon" href="data:,">',
        '<link rel="stylesheet" href="/static/result.css">',
        '<script src="/static/tonal.min.js"></script>',
        '<script src="/static/result.js"></script>',
        '<style>',
        "body{margin:0;padding:0;background:#fff;color:#18212f;font-family:Arial,Helvetica,'Noto Sans TC',sans-serif;font-size:14px;}",
        'html body{height:100vh;display:flex;flex-direction:column;overflow:hidden;}',
        'body .share-shell{flex:1 1 auto;height:auto;min-height:0;width:100%;max-width:1600px;margin:0 auto;}',
        '.bar{flex:0 0 auto;z-index:5;display:flex;gap:8px;align-items:center;padding:9px 10px;background:#fff;border-bottom:1px solid #d5dce3;}',
        '.bar .name{flex:1;color:#243746;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
        '.bar button{height:32px;padding:5px 11px;color:#0069aa;background:#fff;border:1px solid #1683bd;border-radius:4px;font:inherit;cursor:pointer;}',
        '.bar button:hover{color:#00527f;background:#edf7fc;border-color:#0069aa;}',
        '.bar button.primary{color:#fff;background:#0069aa;border-color:#0069aa;font-weight:600;}',
        '.bar button.primary:hover{background:#005f91;border-color:#00527f;}',
        '.bar button:disabled{opacity:.5;cursor:default;}',
        '</style>',
        '</head>',
        '<body>',
        '<header class="bar">',
        '<span class="name">{}</span>'.format(html.escape(Path(midi_path).name)),
        '<span id="result-message"></span>',
        '<button id="cancel" type="button">取消</button>',
        '<button id="save" class="primary" type="button">寫入 MIDI</button>',
        '</header>',
        '<main class="share-shell">',
        '<div class="msr-root">',
        '<pre class="msr-manifest" hidden>{}</pre>'.format(manifest),
        '<div class="msr-host"></div>',
        '</div>',
        '</main>',
        '<script>',
        SHELL_SCRIPT,
        '</script>',
        '</body>',
        '</html>',
    ])


SHELL_SCRIPT = r'''
(function () {
  var message = document.getElementById("result-message");
  function finish(path, payload, done) {
    document.querySelectorAll(".bar button").forEach(function (b) { b.disabled = true; });
    var play = document.querySelector(".msr-toolbar .msr-btn");
    fetch("/api/synth/stop", { method: "POST", body: "{}" }).catch(function () {});
    fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (r) {
      return r.json().then(function (v) { if (!r.ok) throw new Error(v.error || r.status); return v; });
    }).then(function (v) {
      message.textContent = done(v);
      if (play) play.disabled = true;
    }).catch(function (e) {
      message.textContent = "失敗：" + e.message;
      document.querySelectorAll(".bar button").forEach(function (b) { b.disabled = false; });
    });
  }
  document.getElementById("save").onclick = function () {
    var bpm = document.querySelector('input[aria-label="BPM"]').value,
      firstBeat = document.querySelector('input[aria-label="第一拍延遲秒數"]').value || 0;
    finish("/api/save", { bpm: parseFloat(bpm), first_beat: parseFloat(firstBeat) }, function (v) {
      return "已寫入（BPM " + v.bpm.toFixed(2) + "，補 " + v.padding_bars + " 小節），可以關閉此頁";
    });
  };
  document.getElementById("cancel").onclick = function () {
    finish("/api/cancel", {}, function () { return "已取消，MIDI 未修改，可以關閉此頁"; });
  };
})();
'''


def build_bpm_fixed_midi(midi_path, target_bpm, first_beat_delay):
    """Ported from vsti-webui-wes7-pcm: rescale to target_bpm, then move first_beat_delay to bar 1."""
    midi = mido.MidiFile(midi_path)
    source_tempo = midi_tempo(midi)
    target_tempo = int(mido.bpm2tempo(target_bpm))
    bar_ticks = midi.ticks_per_beat * 4
    user_offset_ticks = int(round(first_beat_delay * midi.ticks_per_beat * target_bpm / 60.0))
    scaled_tracks = []
    earliest_note_tick = None

    for track in midi.tracks:
        source_absolute_tick = 0
        scaled_events = []
        for message in track:
            source_absolute_tick += message.time
            absolute_tick = (source_absolute_tick * source_tempo + target_tempo // 2) // target_tempo
            copied = message.copy(time=0)
            if copied.type != 'set_tempo':
                scaled_events.append((absolute_tick, copied))
            if (copied.type == 'note_on' and copied.velocity > 0
                    and (earliest_note_tick is None or absolute_tick < earliest_note_tick)):
                earliest_note_tick = absolute_tick
        scaled_tracks.append(scaled_events)

    shift_ticks = -user_offset_ticks
    padding_bars = 0
    if earliest_note_tick is not None and earliest_note_tick + shift_ticks < 0:
        padding_bars = math.ceil(-(earliest_note_tick + shift_ticks) / bar_ticks)
        shift_ticks += padding_bars * bar_ticks

    output = mido.MidiFile(type=midi.type, ticks_per_beat=midi.ticks_per_beat)
    for track_index, events in enumerate(scaled_tracks):
        output_track = mido.MidiTrack()
        output.tracks.append(output_track)
        previous_tick = 0
        if track_index == 0:
            output_track.append(mido.MetaMessage('set_tempo', tempo=target_tempo, time=0))
        for absolute_tick, message in events:
            corrected_tick = max(0, absolute_tick + shift_ticks)
            message.time = corrected_tick - previous_tick
            previous_tick = corrected_tick
            output_track.append(message)

    output.save(midi_path)
    return {
        'bpm': target_bpm,
        'first_beat_seconds': first_beat_delay,
        'shift_ticks': shift_ticks,
        'padding_bars': padding_bars,
    }


def append_first_beat_stats(midi_path, result, report_path=None):
    report_path = report_path or source_stats_path_for(midi_path)
    report = format_first_beat_stats(midi_path, result)
    with open(report_path, 'a', encoding='utf-8') as file:
        file.write('\n\n' + report + '\n')
    print(report)
    print('Appending first beat report: {}'.format(report_path))
    return report


def auto_align_first_beat(midi_path, report_path=None):
    """Move the estimated first beat to a bar line in place and append the report."""
    estimate = estimate_first_beat(midi_path)
    if estimate is None:
        print('No notes; first beat not aligned.')
        return None
    bpm = mido.tempo2bpm(midi_tempo(mido.MidiFile(midi_path)))
    result = build_bpm_fixed_midi(midi_path, bpm, estimate['first_beat_seconds'])
    report_path = report_path or source_stats_path_for(midi_path)
    report = format_auto_first_beat_stats(midi_path, estimate, result)
    with open(report_path, 'a', encoding='utf-8') as file:
        file.write('\n\n' + report + '\n')
    print(report)
    print('Appending auto first beat report: {}'.format(report_path))
    return result


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
        'BPM: {:.2f}'.format(result['bpm']),
        'First beat in source timeline: {:.3f}s'.format(result['first_beat_seconds']),
        'Shift ticks: {}'.format(result['shift_ticks']),
        'Padding bars: {}'.format(result['padding_bars']),
    ])


def make_handler(midi_path, page, playback, finish):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send_bytes(self, body, content_type, status=200):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, data, status=200):
            self.send_bytes(json.dumps(data).encode('utf-8'), 'application/json', status)

        def do_GET(self):
            if self.path == '/':
                self.send_bytes(page.encode('utf-8'), 'text/html; charset=utf-8')
            elif self.path in STATIC_FILES:
                path, content_type = STATIC_FILES[self.path]
                self.send_bytes(path.read_bytes(), content_type)
            else:
                self.send_error(404)

        def do_POST(self):
            length = int(self.headers.get('Content-Length', 0))
            try:
                payload = json.loads(self.rfile.read(length) or b'{}')
                if self.path == '/api/synth/play':
                    self.send_json(playback.play(midi_path, payload.get('start_seconds', 0.0)))
                elif self.path == '/api/synth/stop':
                    playback.stop()
                    self.send_json({})
                elif self.path == '/api/synth/mix':
                    self.send_json(playback.set_mix(payload.get('muted_tracks', []), payload.get('solo')))
                elif self.path == '/api/save':
                    playback.stop()
                    result = build_bpm_fixed_midi(midi_path, float(payload['bpm']), float(payload['first_beat']))
                    self.send_json(result)
                    finish(result)
                elif self.path == '/api/cancel':
                    playback.stop()
                    self.send_json({})
                    finish(None)
                else:
                    self.send_error(404)
            except Exception as error:
                self.send_json({'error': str(error)}, status=500)

    return Handler


def run_first_beat_ui(midi_path, open_browser=True, port=0, midi_port=None):
    """Serve the workbench until the user saves or cancels; return the save result or None."""
    midi_path = str(midi_path)
    output, backend_label = open_output(midi_port)
    playback = MidiPlayback(output)
    outcome = {}
    server = ThreadingHTTPServer(('127.0.0.1', port), None)

    def finish(result):
        outcome['result'] = result
        threading.Thread(target=server.shutdown).start()

    midi_url = '/midi?{}'.format(Path(midi_path).stat().st_mtime_ns)
    server.RequestHandlerClass = make_handler(
        midi_path, build_page(midi_path, midi_url, backend_label), playback, finish)
    url = 'http://127.0.0.1:{}/'.format(server.server_address[1])
    print('First beat page: {}'.format(url))
    print('Set BPM and the first beat in the browser, then press 寫入 MIDI or 取消 there.')
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        playback.stop()
        if output:
            output.close()
    return outcome.get('result')
