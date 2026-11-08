"""Steinway Grand Piano MIDI sink, rendered by the bundled sfizz SFZ engine."""

import ctypes
import os
from pathlib import Path
import re
import sys
from threading import Event, Lock, Thread

import numpy as np

OUTPUT_NAME = 'Steinway Grand Piano'
SFZ_ENV = 'PIANO_MIDI_CLEANUP_SFZ'
SOURCE_DIR = Path(__file__).resolve().parents[1] / 'sound_sources' / 'sfizz'
LIBRARY_NAME = {
    'darwin': 'libsfizz.dylib',
    'win32': 'sfizz.dll',
}.get(sys.platform, 'libsfizz.so')
LIBRARY_PATH = SOURCE_DIR / 'native' / LIBRARY_NAME
BLOCK_SIZE = 1024
MAX_QUEUED_MESSAGES = 8192


def sfz_path():
    """Return the first existing Steinway SFZ, or None."""
    candidates = []
    configured = os.environ.get(SFZ_ENV)
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend([
        Path.home() / 'Documents' / 'MuseScore3Evo' / 'SoundFonts' / 'Steinway Grand Piano.sfz',
        Path.home() / 'Documents' / 'MuseScore3Evo' / 'SoundFonts' / 'Steinway Grand Piano'
        / 'Steinway Grand Piano.sfz',
        SOURCE_DIR / 'Steinway Grand Piano.sfz',
        SOURCE_DIR / 'Steinway Grand Piano' / 'Steinway Grand Piano.sfz',
    ])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def available():
    return LIBRARY_PATH.is_file() and sfz_path() is not None


def _sfz_text(path):
    """Return the SFZ text with an unusable default_path repaired.

    Some converters wrote default_path=SteinwayLogic/ while storing the
    samples in a sibling folder named after the SFZ. sfizz resolves
    default_path strictly, so repair that one case without editing the file.
    """
    text = path.read_text(encoding='utf-8', errors='replace')
    match = re.search(r'default_path\s*=\s*([^\r\n]*)', text)
    if not match:
        return text
    configured = match.group(1).strip()
    if not configured:
        return text
    if (path.parent / configured).exists():
        return text
    fallback = path.parent / path.stem
    if not fallback.is_dir():
        return text
    replacement = fallback.name + '/'
    return text[:match.start(1)] + replacement + text[match.end(1):]


class SfizzOutput:
    """Match the MIDI port send/close interface; initialize away from Tk."""

    def __init__(self, path=None):
        self.ready = False
        self.error = None
        self._path = Path(path).expanduser() if path else sfz_path()
        self._closed = Event()
        self._lock = Lock()
        self._commands = bytearray()
        self._panic = False
        self._stream = None
        self._library = None
        self._handle = None
        self._left = (ctypes.c_float * BLOCK_SIZE)()
        self._right = (ctypes.c_float * BLOCK_SIZE)()
        self._left_view = np.frombuffer(self._left, dtype=np.float32)
        self._right_view = np.frombuffer(self._right, dtype=np.float32)
        self._channels = (ctypes.POINTER(ctypes.c_float) * 2)(self._left, self._right)
        self._thread = Thread(target=self._initialize, daemon=True)
        self._thread.start()

    def _initialize(self):
        try:
            import sounddevice as sd

            if self._path is None:
                raise RuntimeError(
                    'Steinway Grand Piano SFZ not found; set {} or place '
                    'Steinway Grand Piano.sfz in sound_sources/sfizz.'.format(SFZ_ENV))
            if not LIBRARY_PATH.is_file():
                raise RuntimeError('sfizz library not found: {}'.format(LIBRARY_PATH))

            library = ctypes.CDLL(str(LIBRARY_PATH))
            library.sfizz_create_synth.restype = ctypes.c_void_p
            library.sfizz_free.argtypes = [ctypes.c_void_p]
            library.sfizz_free.restype = None
            library.sfizz_set_sample_rate.argtypes = [ctypes.c_void_p, ctypes.c_float]
            library.sfizz_set_samples_per_block.argtypes = [ctypes.c_void_p, ctypes.c_int]
            library.sfizz_set_num_voices.argtypes = [ctypes.c_void_p, ctypes.c_int]
            library.sfizz_load_file.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            library.sfizz_load_file.restype = ctypes.c_bool
            library.sfizz_load_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
            library.sfizz_load_string.restype = ctypes.c_bool
            library.sfizz_get_num_regions.argtypes = [ctypes.c_void_p]
            library.sfizz_get_num_regions.restype = ctypes.c_int
            library.sfizz_send_note_on.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
            library.sfizz_send_note_off.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
            library.sfizz_send_cc.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
            library.sfizz_all_sound_off.argtypes = [ctypes.c_void_p]
            library.sfizz_render_block.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
                ctypes.c_int, ctypes.c_int]
            self._library = library

            sample_rate = sd.query_devices(kind='output')['default_samplerate']
            self._handle = library.sfizz_create_synth()
            if not self._handle:
                raise RuntimeError('sfizz could not create a synth')
            library.sfizz_set_sample_rate(self._handle, sample_rate)
            library.sfizz_set_samples_per_block(self._handle, BLOCK_SIZE)
            library.sfizz_set_num_voices(self._handle, 256)
            text = _sfz_text(self._path)
            loaded = library.sfizz_load_string(
                self._handle, str(self._path).encode('utf-8'), text.encode('utf-8'))
            if not loaded:
                loaded = library.sfizz_load_file(self._handle, str(self._path).encode('utf-8'))
            regions = library.sfizz_get_num_regions(self._handle)
            if not loaded or regions <= 0:
                raise RuntimeError('sfizz loaded no regions from {}'.format(self._path))

            if not self._closed.is_set():
                self._stream = sd.RawOutputStream(
                    samplerate=sample_rate, channels=2, dtype='float32',
                    blocksize=BLOCK_SIZE, latency='high', callback=self._render)
                self._stream.start()
                self.ready = True
                self._closed.wait()
        except Exception as error:
            self.error = str(error)
        finally:
            self.ready = False
            try:
                if self._stream is not None:
                    self._stream.abort()
                    self._stream.close()
            finally:
                self._stream = None
                if self._handle:
                    self._library.sfizz_free(self._handle)
                    self._handle = None

    def _render(self, outdata, frames, _time, _status):
        try:
            handle = self._handle
            library = self._library
            if handle is None or library is None:
                outdata[:] = bytes(len(outdata))
                return
            with self._lock:
                commands = bytes(self._commands)
                self._commands.clear()
                panic = self._panic
                self._panic = False
            if panic:
                library.sfizz_all_sound_off(handle)
            for index in range(0, len(commands) - 2, 3):
                status, data1, data2 = commands[index:index + 3]
                kind = status & 0xF0
                if kind == 0x90:
                    if data2:
                        library.sfizz_send_note_on(handle, 0, data1, data2)
                    else:
                        library.sfizz_send_note_off(handle, 0, data1, 0)
                elif kind == 0x80:
                    library.sfizz_send_note_off(handle, 0, data1, data2)
                elif kind == 0xB0:
                    library.sfizz_send_cc(handle, 0, data1, data2)
            output = np.frombuffer(outdata, dtype=np.float32).reshape(-1, 2)
            offset = 0
            while offset < frames:
                chunk = min(BLOCK_SIZE, frames - offset)
                library.sfizz_render_block(handle, self._channels, 2, chunk)
                output[offset:offset + chunk, 0] = self._left_view[:chunk]
                output[offset:offset + chunk, 1] = self._right_view[:chunk]
                offset += chunk
        except Exception as error:
            outdata[:] = bytes(len(outdata))
            self.error = str(error)
            self._closed.set()

    def send(self, message):
        if self._closed.is_set() or not self.ready:
            return
        if message.type == 'sysex':
            self.panic()
            return
        if message.type not in ('note_on', 'note_off', 'control_change'):
            return
        data = bytes(message.bytes())
        with self._lock:
            if len(self._commands) >= 3 * MAX_QUEUED_MESSAGES:
                self._commands.clear()
                self._panic = True
                self.error = 'Steinway Grand Piano MIDI queue overflow'
                self._closed.set()
            else:
                self._commands.extend(data)

    def panic(self):
        with self._lock:
            self._commands.clear()
            self._panic = True

    def close(self):
        self._closed.set()
        self._thread.join(timeout=3)
