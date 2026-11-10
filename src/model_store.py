"""Model weights for separation and transcription: where they live and how they are downloaded.

Weights are not shipped with the project. A model that is already in place is used as is
(a symlink to an existing copy also works); a missing one is downloaded on first use from
the first source that answers, checked by SHA-256 and only then moved into place.
"""

import hashlib
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

from gui_i18n import tr


MODELS_DIR = Path.home() / '.local' / 'share' / 'piano-midi-cleanup' / 'models'
CHUNK_BYTES = 1 << 20


class DownloadCancelled(Exception):
    pass


@dataclass(frozen=True)
class Model:
    key: str
    name: str
    path: Path            # What the engine loads; it exists once the model is ready.
    size: int             # Bytes downloaded.
    sha256: str           # Of the downloaded file.
    urls: tuple           # Tried in order.
    unzip_member: str = ''  # Directory inside the zip to extract to `path`.

    def ready(self):
        return self.path.exists()

    def size_mb(self):
        return round(self.size / 1e6)

    def source_hosts(self):
        return [url.split('/')[2] for url in self.urls]


MODELS = {
    'bs_roformer_coreml': Model(
        key='bs_roformer_coreml',
        name='BS-RoFormer SW (Core ML)',
        path=MODELS_DIR / 'bs_roformer_sw_coreml' / 'bs_roformer_6stem.mlpackage',
        size=327642134,
        sha256='eb420c4fc20175fbfe9a7f32b8fef3ab1dd3a814915ae5db4b4ca29238519794',
        urls=('https://github.com/jonkubis/BS-ROFO-SW-Fixed-CoreML/releases/download/v0.1.0/'
              'BS-ROFO-SW-Fixed-CoreML-v0.1.0.zip',),
        unzip_member='bs_roformer_6stem.mlpackage/',
    ),
    'bs_roformer_onnx': Model(
        key='bs_roformer_onnx',
        name='BS-RoFormer SW (ONNX)',
        path=MODELS_DIR / 'bs_roformer_sw_6stem_fp16.onnx',
        size=352778874,
        sha256='d3d2bac77a7023282cb5f35a5807179e34076b60589867b572275f1a8ec36444',
        urls=('https://huggingface.co/elicwhite/bs-roformer-sw-6stem-onnx/resolve/main/'
              'bs_roformer_sw_6stem_fp16.onnx',
              'https://hf-mirror.com/elicwhite/bs-roformer-sw-6stem-onnx/resolve/main/'
              'bs_roformer_sw_6stem_fp16.onnx'),
    ),
    # Same place as before, so existing downloads from transcribe.py are reused.
    'bytedance': Model(
        key='bytedance',
        name='Piano Transcription (ByteDance)',
        path=Path.home() / 'piano_transcription_inference_data' / 'note_F1=0.9677_pedal_F1=0.9186.pth',
        size=171966578,
        sha256='c3fa9730725bf4a762f1c14bc80cd5986eacda01b026f5a4a2525cd607876141',
        urls=('https://zenodo.org/records/4034264/files/'
              'CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1',),
    ),
}


def download(model, progress=None, cancel=None, log=None):
    """Fetch a missing model. progress(done, total) per chunk; cancel is a threading.Event."""
    if model.ready():
        return model.path
    model.path.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    for url in model.urls:
        host = url.split('/')[2]
        if log:
            log(tr('Downloading {} from {} (about {} MB)...').format(model.name, host, model.size_mb()))
        try:
            _fetch(model, url, _with_log(progress, log), cancel)
            return model.path
        except DownloadCancelled:
            raise
        except Exception as error:  # Try the next source; report all of them if none works.
            errors.append('{}: {}'.format(host, error))
            if log:
                log(tr('  failed: {}').format(error))
    raise RuntimeError(tr('Could not download {}. {}').format(model.name, ' / '.join(errors)))


def _with_log(progress, log):
    """Also print the downloaded megabytes about every 5 %."""
    shown = [-1]

    def report(done, total):
        step = int(20 * done / total)
        if log and step != shown[0]:
            shown[0] = step
            log('  {:.0f} / {:.0f} MB'.format(done / 1e6, total / 1e6))
        if progress:
            progress(done, total)
    return report


def _fetch(model, url, progress, cancel):
    temporary = None
    try:
        request = Request(url, headers={'User-Agent': 'piano-midi-cleanup'})
        with urlopen(request, timeout=30) as response, tempfile.NamedTemporaryFile(
                dir=model.path.parent, suffix='.download', delete=False) as output:
            temporary = Path(output.name)
            total = int(response.headers.get('Content-Length') or model.size)
            digest, done = hashlib.sha256(), 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise DownloadCancelled()
                block = response.read(CHUNK_BYTES)
                if not block:
                    break
                output.write(block)
                digest.update(block)
                done += len(block)
                if progress:
                    progress(done, total)
        if digest.hexdigest() != model.sha256:
            raise ValueError(tr('the file does not match the expected SHA-256'))
        if model.unzip_member:
            _unzip(temporary, model)
        else:
            temporary.replace(model.path)
            temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _unzip(archive, model):
    staging = Path(tempfile.mkdtemp(dir=model.path.parent, suffix='.unzip'))
    try:
        with zipfile.ZipFile(archive) as source:
            members = [name for name in source.namelist() if name.startswith(model.unzip_member)]
            if not members:
                raise ValueError('{} is not in the download'.format(model.unzip_member))
            source.extractall(staging, members)
        (staging / model.unzip_member.rstrip('/')).replace(model.path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
