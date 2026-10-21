"""Realtime MIDI playback on the S-MU2000 emulator through a virtual CoreMIDI port.

MidiPlayback is ported from vsti-webui-wes7-pcm app/playback/service.py; the UDP
output is replaced by a virtual port that the S-MU2000 `live` engine listens to.
"""

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, Thread, current_thread

import mido


MU2000_DIR = Path(os.environ.get('PIANOTRANS_MU2000_DIR', Path.home() / 'Music' / 'S-MU2000-mac')).expanduser()
VIRTUAL_PORT_NAME = 'pianotrans-tools'
LIVE_BOOT_SECONDS = 4.0
LEFT_HAND_CHANNELS = (2, 3)


@dataclass(frozen=True)
class MidiEvent:
    seconds: float
    track_id: str
    message: mido.Message


def hand_for_channel(channel):
    return 'left' if channel in LEFT_HAND_CHANNELS else 'right'


def read_midi_events(path):
    """Return timed non-meta events with a hand track id, the hands present, and the duration."""
    midi = mido.MidiFile(path)
    raw_events = []
    sequence = 0

    for track_index, track in enumerate(midi.tracks):
        absolute_tick = 0
        for message in track:
            absolute_tick += message.time
            if not message.is_meta or message.type == 'set_tempo':
                raw_events.append((absolute_tick, track_index, sequence, message.copy(time=0)))
            sequence += 1

    raw_events.sort(key=lambda item: (item[0], item[1], item[2]))
    tempo = 500000
    current_tick = 0
    current_seconds = 0.0
    events = []

    for absolute_tick, _, _, message in raw_events:
        current_seconds += mido.tick2second(absolute_tick - current_tick, midi.ticks_per_beat, tempo)
        current_tick = absolute_tick
        if message.type == 'set_tempo':
            tempo = message.tempo
        elif not message.is_meta:
            track_id = hand_for_channel(getattr(message, 'channel', 0))
            events.append(MidiEvent(current_seconds, track_id, message))

    tracks = list(dict.fromkeys(
        event.track_id
        for event in events
        if event.message.type == 'note_on' and event.message.velocity > 0
    ))
    return events, tracks, current_seconds


class Mu2000Live:
    """Owns the virtual MIDI output and a headless S-MU2000 `live` process listening to it."""

    def __init__(self, mu2000_dir=MU2000_DIR):
        self.mu2000_dir = Path(mu2000_dir)
        self.output = None
        self.process = None

    def start(self):
        live = self.mu2000_dir / 'app' / 'bin' / 'live'
        roms = self.mu2000_dir / 'roms'
        if not live.exists():
            raise RuntimeError('S-MU2000 live engine not found: {}'.format(live))

        self.output = mido.open_output(VIRTUAL_PORT_NAME, virtual=True)
        listing = subprocess.run(
            [str(live), str(roms), '--list'],
            capture_output=True, text=True, timeout=30,
        ).stdout
        index = self._input_index(listing)
        if index is None:
            raise RuntimeError('S-MU2000 does not see the {} MIDI port.'.format(VIRTUAL_PORT_NAME))

        self.process = subprocess.Popen(
            [str(live), str(roms), '--midi', str(index), '--fast-midi'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(LIVE_BOOT_SECONDS)
        if self.process.poll() is not None:
            raise RuntimeError('S-MU2000 live engine exited during boot.')

    @staticmethod
    def _input_index(listing):
        in_inputs = False
        for line in listing.splitlines():
            if line.startswith('MIDI inputs'):
                in_inputs = True
                continue
            if in_inputs and not line.startswith(' '):
                break
            number, _, name = line.strip().partition(': ')
            if in_inputs and name.strip() == VIRTUAL_PORT_NAME:
                return int(number)
        return None

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.output:
            self.output.close()


class MidiPlayback:
    def __init__(self, output):
        self._output = output
        self._state_lock = Lock()
        self._output_lock = Lock()
        self._stop = Event()
        self._thread = None
        self._playing = False
        self._tracks = []
        self._muted = set()
        self._solo = None
        self._active_notes = set()

    def play(self, path, start_seconds=0.0):
        if self._playing:
            self.stop()
        events, tracks, duration = read_midi_events(path)
        start_seconds = max(0.0, min(float(start_seconds), duration))
        with self._state_lock:
            self._tracks = tracks
            self._playing = True
        self._stop = Event()
        self._thread = Thread(target=self._run, args=(events, start_seconds, self._stop), daemon=True)
        self._thread.start()
        return {'tracks': tracks, 'duration_seconds': duration}

    def stop(self):
        self._stop.set()
        self._all_notes_off()
        thread = self._thread
        if thread and thread.is_alive() and thread is not current_thread():
            thread.join(timeout=2)
        self._thread = None
        with self._state_lock:
            self._playing = False

    def set_mix(self, muted_tracks, solo):
        with self._state_lock:
            self._muted = set(muted_tracks)
            self._solo = solo
            notes_to_stop = [
                note for note in self._active_notes
                if note[0] in self._muted or (self._solo is not None and note[0] != self._solo)
            ]
            self._active_notes.difference_update(notes_to_stop)
        for _, channel, note in notes_to_stop:
            self._send_message(mido.Message('note_off', channel=channel, note=note))
        return {'muted': sorted(self._muted), 'solo': self._solo}

    def _run(self, events, start_seconds, stop_event):
        started = time.monotonic()
        try:
            self._send_message(mido.Message('sysex', data=(0x7E, 0x7F, 0x09, 0x01)))
            for event in events:
                if event.seconds < start_seconds:
                    if event.message.type not in {'note_on', 'note_off'}:
                        self._send_message(event.message)
                    continue
                wait_seconds = event.seconds - start_seconds - (time.monotonic() - started)
                if stop_event.wait(max(0.0, wait_seconds)):
                    break
                self._send_event(event)
            self._all_notes_off()
        finally:
            with self._state_lock:
                self._active_notes.clear()
                self._playing = False

    def _send_event(self, event):
        message = event.message
        if message.type == 'note_on' and message.velocity > 0:
            with self._state_lock:
                if event.track_id in self._muted or (
                    self._solo is not None and event.track_id != self._solo
                ):
                    return
                self._active_notes.add((event.track_id, message.channel, message.note))
        elif message.type in {'note_off', 'note_on'}:
            with self._state_lock:
                self._active_notes.discard((event.track_id, message.channel, message.note))
        self._send_message(message)

    def _send_message(self, message):
        with self._output_lock:
            self._output.send(message)

    def _all_notes_off(self):
        with self._state_lock:
            self._active_notes.clear()
        for channel in range(16):
            # Release the sustain pedal too, or pedalled notes keep ringing after stop.
            self._send_message(mido.Message('control_change', channel=channel, control=64, value=0))
            self._send_message(mido.Message('control_change', channel=channel, control=123, value=0))
