"""BS-RoFormer SW piano separation: Core ML on macOS, ONNX Runtime elsewhere.

Adapted from the BS-ROFO-SW-Fixed-CoreML Python driver (jonkubis, MIT): the song is cut into
overlapping chunks, each chunk's STFT goes through the model, and the piano stem's masked
spectrogram is turned back into audio and overlap-added with a raised-cosine window.
"""

import functools
import sys
import time

import numpy as np

from model_store import MODELS


SAMPLE_RATE = 44100
N_FFT = 2048
HOP_LENGTH = 512
PIANO = 5  # Stem order: bass, drums, other, vocals, guitar, piano.


@functools.lru_cache(maxsize=None)
def backend():
    """'coreml' on macOS when coremltools is installed, otherwise 'onnx'."""
    if sys.platform == 'darwin':
        try:
            import coremltools  # noqa: F401
            return 'coreml'
        except ImportError:
            pass
    return 'onnx'


def required_model():
    return MODELS['bs_roformer_coreml' if backend() == 'coreml' else 'bs_roformer_onnx']


def missing_package():
    """The pip package the chosen backend needs, or None."""
    if backend() == 'coreml':
        return None
    try:
        import onnxruntime  # noqa: F401
        return None
    except ImportError:
        return 'onnxruntime'


def load_audio(path):
    """Stereo float32 at 44.1 kHz, shape [2, samples]."""
    try:
        import soundfile
        audio, rate = soundfile.read(path, dtype='float32', always_2d=True)
        audio = audio.T
    except Exception:  # m4a and other formats libsndfile cannot read go through librosa / ffmpeg.
        import librosa
        audio, rate = librosa.load(path, sr=None, mono=False)
        audio = np.atleast_2d(audio).astype(np.float32)
    if audio.shape[0] == 1:
        audio = np.repeat(audio, 2, axis=0)
    audio = audio[:2]
    if rate != SAMPLE_RATE:
        from scipy.signal import resample_poly
        audio = resample_poly(audio, SAMPLE_RATE, rate, axis=-1).astype(np.float32)
    return audio


def _stft(chunk, frames):
    from scipy.signal import stft
    _, _, spec = stft(chunk, fs=SAMPLE_RATE, window='hann', nperseg=N_FFT, noverlap=N_FFT - HOP_LENGTH,
                      nfft=N_FFT, boundary='zeros', padded=True, return_onesided=True, axis=-1)
    # scipy divides by the window sum; the model expects torch.stft's unnormalized scale.
    spec = (spec * (N_FFT / 2.0)).astype(np.complex64)[:, :, :frames]
    if spec.shape[-1] < frames:
        spec = np.pad(spec, ((0, 0), (0, 0), (0, frames - spec.shape[-1])))
    return spec


def _istft(spec, length):
    from scipy.signal import istft
    _, audio = istft(spec / (N_FFT / 2.0), fs=SAMPLE_RATE, window='hann', nperseg=N_FFT,
                     noverlap=N_FFT - HOP_LENGTH, nfft=N_FFT, boundary=True, time_axis=-1, freq_axis=-2)
    audio = audio[..., :length].astype(np.float32)
    if audio.shape[-1] < length:
        audio = np.pad(audio, ((0, 0), (0, length - audio.shape[-1])))
    return audio


def _raised_cosine(length, fade):
    window = np.ones(length, dtype=np.float32)
    ramp = 0.5 * (1.0 - np.cos(np.pi * np.arange(fade) / max(1, fade - 1)))
    window[:fade] = ramp
    window[length - fade:] = ramp[::-1]
    return window


class _CoreMLModel:
    frames = 256

    def __init__(self, device, log):
        import coremltools as ct
        package = MODELS['bs_roformer_coreml'].path
        compiled = package.with_suffix('.mlmodelc')
        if not compiled.exists():
            log('Compiling the Core ML model (first use only)...')
            ct.utils.compile_model(str(package), str(compiled))
        # cpuOnly crashes for this model in coremltools 9, so the device choice does not apply.
        self.model = ct.models.CompiledMLModel(str(compiled), compute_units=ct.ComputeUnit.CPU_AND_GPU)

    def piano(self, spec):
        packed = np.stack([spec[0].real, spec[0].imag, spec[1].real, spec[1].imag])[None].astype(np.float32)
        block = self.model.predict({'spectrogram': packed})['stems_spectrogram'][0][PIANO]
        return np.stack([block[0] + 1j * block[1], block[2] + 1j * block[3]]).astype(np.complex64)


class _OnnxModel:
    frames = 345  # The ONNX export is traced at this length.

    def __init__(self, device, log):
        import onnxruntime as ort
        available = ort.get_available_providers()
        providers = ['CPUExecutionProvider']
        if device != 'cpu' and 'CUDAExecutionProvider' in available:
            providers.insert(0, 'CUDAExecutionProvider')
        options = ort.SessionOptions()
        options.enable_mem_pattern = False
        self.session = ort.InferenceSession(str(MODELS['bs_roformer_onnx'].path), options, providers=providers)
        log('ONNX Runtime: {}'.format(', '.join(self.session.get_providers())))

    def piano(self, spec):
        real, imag = self.session.run(None, {'spec_real': spec.real[None].astype(np.float32),
                                             'spec_imag': spec.imag[None].astype(np.float32)})
        return (real[0, PIANO] + 1j * imag[0, PIANO]).astype(np.complex64)


def separate_piano(audio_path, output_path, device='auto', progress=None, cancel=None, log=print):
    """Write the piano stem of audio_path to output_path as 16-bit WAV. Returns False if cancelled."""
    import soundfile
    model = (_CoreMLModel if backend() == 'coreml' else _OnnxModel)(device, log)
    audio = load_audio(audio_path)
    samples = audio.shape[-1]
    chunk = (model.frames - 1) * HOP_LENGTH
    step = chunk // 2  # 50 % overlap.
    window = _raised_cosine(chunk, chunk - step)
    output = np.zeros((2, samples + chunk), dtype=np.float32)
    weights = np.zeros(samples + chunk, dtype=np.float32)
    starts = list(range(0, max(samples - step, 1), step)) or [0]
    log('{:.1f} s of audio, {} chunks'.format(samples / SAMPLE_RATE, len(starts)))
    began = time.time()
    for index, start in enumerate(starts):
        if cancel is not None and cancel.is_set():
            return False
        piece = np.zeros((2, chunk), dtype=np.float32)
        piece[:, :min(chunk, samples - start)] = audio[:, start:start + chunk]
        output[:, start:start + chunk] += _istft(model.piano(_stft(piece, model.frames)), chunk) * window
        weights[start:start + chunk] += window
        elapsed = time.time() - began
        log('chunk {}/{}  elapsed {:.0f} s, ETA {:.0f} s'.format(
            index + 1, len(starts), elapsed, elapsed / (index + 1) * (len(starts) - index - 1)))
        if progress:
            progress(index + 1, len(starts))
    nonzero = weights > 1e-8
    output[:, nonzero] /= weights[nonzero]
    soundfile.write(output_path, output[:, :samples].T, SAMPLE_RATE, subtype='PCM_16')
    return True
