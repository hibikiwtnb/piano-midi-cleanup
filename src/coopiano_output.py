"""CoPiano MIDI sink, with sounddevice sending PCM to the OS default output."""

import ctypes
from pathlib import Path
import sys
from threading import Event, Lock, Thread


OUTPUT_NAME = 'CoPiano'
SOURCE_DIR = Path(__file__).resolve().parents[1] / 'sound_sources' / 'coopiano'
LIBRARY_NAME = 'coopiano.dll' if sys.platform == 'win32' else (
    'libcoopiano.dylib' if sys.platform == 'darwin' else 'libcoopiano.so')


class CoPianoOutput:
    """Match the MIDI port send/close interface; initialize away from Tk."""

    def __init__(self):
        self.ready = False
        self.error = None
        self._closed = Event()
        self._lock = Lock()
        self._commands = bytearray()
        self._stream = None
        self._library = None
        self._handle = None
        self._thread = Thread(target=self._initialize, daemon=True)
        self._thread.start()

    def _initialize(self):
        try:
            import sounddevice as sd

            library = ctypes.CDLL(str(SOURCE_DIR / 'native' / LIBRARY_NAME))
            library.coopiano_create.argtypes = [ctypes.c_char_p, ctypes.c_double]
            library.coopiano_create.restype = ctypes.c_void_p
            library.coopiano_destroy.argtypes = [ctypes.c_void_p]
            library.coopiano_destroy.restype = None
            library.coopiano_error.restype = ctypes.c_char_p
            library.coopiano_render.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                                ctypes.c_void_p, ctypes.c_uint]
            library.coopiano_render.restype = ctypes.c_int
            self._library = library
            sample_rate = sd.query_devices(kind='output')['default_samplerate']
            self._handle = library.coopiano_create(str(SOURCE_DIR / 'default.ini').encode('utf-8'), sample_rate)
            if not self._handle:
                raise RuntimeError(library.coopiano_error().decode('utf-8', errors='replace'))
            if not self._closed.is_set():
                self._stream = sd.RawOutputStream(samplerate=sample_rate, channels=2, dtype='float32',
                                                  blocksize=1024, latency='high', callback=self._render)
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
                if self._handle:
                    self._library.coopiano_destroy(self._handle)
                    self._handle = None

    def _render(self, outdata, frames, _time, _status):
        try:
            with self._lock:
                commands = bytes(self._commands)
                self._commands.clear()
            command_buffer = ctypes.create_string_buffer(commands) if commands else None
            samples = (ctypes.c_char * len(outdata)).from_buffer(outdata)
            if not self._library.coopiano_render(self._handle, samples, frames,
                                                  command_buffer, len(commands) // 3):
                raise RuntimeError('CoPiano could not render audio')
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
            if len(self._commands) >= 3 * 8192:
                self._commands[:] = b'\xff\x00\x00'
                self.error = 'CoPiano MIDI queue overflow'
                self._closed.set()
            else:
                self._commands.extend(data)

    def panic(self):
        with self._lock:
            self._commands[:] = b'\xff\x00\x00'

    def close(self):
        self._closed.set()
        # The owner thread closes the stream before destroying the native engine.
        self._thread.join(timeout=2)
