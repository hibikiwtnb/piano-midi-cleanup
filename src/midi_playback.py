"""Shared MIDI scheduling for built-in CoPiano audio and external MIDI ports."""

import os
import time
from dataclasses import dataclass
from threading import Event, Lock, Thread, current_thread

import mido

from hand_split_channels import CHANNELS
from coopiano_output import OUTPUT_NAME as PIANO_OUTPUT_NAME, CoPianoOutput


VIRTUAL_PORT_NAME = 'piano-midi-cleanup'
WINDOWS_OUTPUT_NAME = 'Microsoft GS Wavetable Synth'
HAND_BY_CHANNEL = {channel: hand for hand, channel in CHANNELS.items()}


def default_output_name():
    return PIANO_OUTPUT_NAME


def open_output(port_name=None):
    """Open the selected audio/MIDI sink; never silently change the selection."""
    if port_name in (None, PIANO_OUTPUT_NAME):
        return CoPianoOutput(), PIANO_OUTPUT_NAME
    if port_name == VIRTUAL_PORT_NAME and os.name != 'nt':
        try:
            output = mido.open_output(VIRTUAL_PORT_NAME, virtual=True)
        except Exception as error:
            print('Cannot open virtual MIDI port {} ({}); playback has no sound.'.format(
                VIRTUAL_PORT_NAME, error))
            return None, VIRTUAL_PORT_NAME
        print('Playback output: {}'.format(VIRTUAL_PORT_NAME))
        return output, VIRTUAL_PORT_NAME

    wanted = port_name or WINDOWS_OUTPUT_NAME
    try:
        names = mido.get_output_names()
    except Exception as error:
        print('Cannot list MIDI outputs ({}); playback has no sound.'.format(error))
        return None, wanted
    name = next((candidate for candidate in names if candidate == wanted), None)
    if name is None:
        name = next((candidate for candidate in names if wanted in candidate), None)
    if name is not None:
        try:
            output = mido.open_output(name)
        except Exception as error:
            print('Cannot open {} ({}); playback has no sound.'.format(name, error))
            return None, wanted
        print('Playback output: {}'.format(name))
        return output, name
    print('{} not found; playback has no sound.'.format(wanted))
    print('Available MIDI outputs: {}'.format(', '.join(names) if names else '(none)'))
    return None, wanted


def output_names():
    """List CoPiano first, followed by existing MIDI outputs."""
    try:
        names = mido.get_output_names()
    except Exception as error:
        print('Cannot list MIDI outputs ({}).'.format(error))
        names = []
    if os.name != 'nt' and VIRTUAL_PORT_NAME not in names:
        names.insert(0, VIRTUAL_PORT_NAME)
    return [PIANO_OUTPUT_NAME] + [name for name in names if name != PIANO_OUTPUT_NAME]


@dataclass(frozen=True)
class MidiEvent:
    seconds: float
    track_id: str
    message: mido.Message


def hand_for_channel(channel):
    """Hand name of a hand-split channel; other channels count as the right hand."""
    return HAND_BY_CHANNEL.get(channel, 'right')


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
        if self._output is None:
            return
        with self._output_lock:
            self._output.send(message)

    def _all_notes_off(self):
        with self._state_lock:
            self._active_notes.clear()
        for channel in range(16):
            # Release the sustain pedal too, or pedalled notes keep ringing after stop.
            self._send_message(mido.Message('control_change', channel=channel, control=64, value=0))
            self._send_message(mido.Message('control_change', channel=channel, control=123, value=0))
        if self._output is not None and hasattr(self._output, 'panic'):
            with self._output_lock:
                self._output.panic()
