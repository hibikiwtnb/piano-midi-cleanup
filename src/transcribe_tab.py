"""Separation and transcription tab: from a song or piano recording to a MIDI file."""

import contextlib
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from gui_i18n import tr
import model_store


AUDIO_EXTENSIONS = ('.flac', '.wav', '.mp3', '.m4a', '.aif', '.aiff', '.ape')
MIDI_EXTENSIONS = ('.mid', '.midi')
# (key, label, short description); the first entry is the default.
SEPARATION_ENGINES = (
    ('bs_roformer_sw', 'BS-RoFormer SW (6 stems, keep piano)', 'Splits a mix into 6 stems and keeps the piano.'),
    ('none', 'No separation', 'The audio is already solo piano or a separated piano stem.'),
)
TRANSCRIPTION_ENGINES = (
    ('bytedance', 'Piano Transcription (ByteDance)', 'The model the cleanup rules are tuned for.'),
)
DEVICES = (
    ('auto', 'Auto (GPU first)'),
    ('cpu', 'CPU'),
)
STEPS = ('Separate', 'Transcribe', 'Hand over to Processing')


def output_paths(audio_path, separate, output_dir=''):
    """Return (piano stem or None, MIDI) for an audio file."""
    folder = output_dir or os.path.dirname(audio_path)
    stem = os.path.splitext(os.path.basename(audio_path))[0]
    if not separate:
        return None, os.path.join(folder, stem + '.mid')
    piano = os.path.join(folder, stem + '_piano.wav')
    return piano, os.path.splitext(piano)[0] + '.mid'


def audio_summary(path):
    """Length, sample rate and channels, or '' when soundfile cannot read the format."""
    try:
        import soundfile
        info = soundfile.info(path)
    except Exception:
        return ''
    minutes, seconds = divmod(int(round(info.duration)), 60)
    channels = {1: tr('mono'), 2: tr('stereo')}.get(info.channels, tr('{} channels').format(info.channels))
    return '{}:{:02d} · {:.1f} kHz · {}'.format(minutes, seconds, info.samplerate / 1000, channels)


class TranscribeTab(ttk.Frame):
    def __init__(self, master, settings, save_settings, on_midi_ready, on_open_midi, status, **kwargs):
        super().__init__(master, **kwargs)
        self.settings = settings
        self.save_settings = save_settings
        self.on_midi_ready = on_midi_ready
        self.on_open_midi = on_open_midi
        self.status = status
        self.audio = tk.StringVar(value=settings.get('transcribe_audio', ''))
        self.output_dir = tk.StringVar(value=settings.get('transcribe_output_dir', ''))
        self.separation = tk.StringVar()
        self.transcription = tk.StringVar()
        self.device = tk.StringVar()
        self.regenerate = tk.BooleanVar(value=False)
        self.process_after = tk.BooleanVar(value=settings.get('transcribe_process_after', False))
        self.audio_info = tk.StringVar()
        self.last_audio = None
        self.worker = None
        self.cancel = threading.Event()
        self.messages = queue.Queue()
        self.build()
        self.set_choice(self.separation, SEPARATION_ENGINES, settings.get('separation_engine'))
        self.set_choice(self.transcription, TRANSCRIPTION_ENGINES, settings.get('transcription_engine'))
        self.set_choice(self.device, DEVICES, settings.get('transcribe_device'))
        for variable in (self.audio, self.output_dir, self.separation, self.transcription, self.regenerate):
            variable.trace_add('write', lambda *_: self.refresh())
        for variable in (self.separation, self.transcription, self.device, self.process_after):
            variable.trace_add('write', lambda *_: self.remember())
        self.refresh()

    # Layout

    def build(self):
        source = ttk.LabelFrame(self, text=tr('Input audio'), padding=8)
        source.pack(fill='x')
        ttk.Label(source, text=tr('Song or piano recording')).grid(row=0, column=0, sticky='w')
        entry = ttk.Entry(source, textvariable=self.audio, width=70)
        entry.grid(row=0, column=1, sticky='we', padx=6)
        ttk.Button(source, text=tr('Choose...'), command=self.choose_audio).grid(row=0, column=2)
        ttk.Label(source, textvariable=self.audio_info, foreground='#555555').grid(
            row=1, column=1, sticky='w', padx=6, pady=(4, 0))
        self.open_midi_button = ttk.Button(source, text=tr('Open in Processing'),
                                           command=lambda: self.on_open_midi(self.audio.get().strip()))
        self.open_midi_button.grid(row=1, column=2, pady=(4, 0))
        source.columnconfigure(1, weight=1)

        engines = ttk.LabelFrame(self, text=tr('Engines'), padding=8)
        engines.pack(fill='x', pady=(8, 0))
        self.descriptions = {}
        for row, (label, variable, choices) in enumerate((
                ('Separation engine', self.separation, SEPARATION_ENGINES),
                ('Transcription engine', self.transcription, TRANSCRIPTION_ENGINES),
                ('Device', self.device, DEVICES))):
            ttk.Label(engines, text=tr(label)).grid(row=row, column=0, sticky='w', pady=2)
            box = ttk.Combobox(engines, textvariable=variable, state='readonly', width=38,
                               values=[tr(choice[1]) for choice in choices])
            box.grid(row=row, column=1, sticky='w', padx=6, pady=2)
            if len(choices[0]) > 2:
                description = tk.StringVar()
                ttk.Label(engines, textvariable=description, foreground='#555555').grid(
                    row=row, column=2, sticky='w')
                self.descriptions[str(variable)] = (variable, choices, description)
                variable.trace_add('write', lambda *_, key=str(variable): self.describe(key))

        self.models_label = ttk.Label(engines, foreground='#555555')
        self.models_label.grid(row=3, column=1, columnspan=2, sticky='w', padx=6, pady=(6, 0))
        self.download_button = ttk.Button(engines, text=tr('Download now'), command=self.download_only)
        self.download_button.grid(row=3, column=3, sticky='e', pady=(6, 0))

        output = ttk.LabelFrame(self, text=tr('Output location'), padding=8)
        output.pack(fill='x', pady=(8, 0))
        ttk.Label(output, text=tr('Folder')).grid(row=0, column=0, sticky='w')
        self.folder_label = ttk.Label(output)
        self.folder_label.grid(row=0, column=1, sticky='w', padx=6)
        ttk.Button(output, text=tr('Change...'), command=self.choose_output_dir).grid(row=0, column=2)
        ttk.Button(output, text=tr('Same as audio'), command=lambda: self.output_dir.set('')).grid(
            row=0, column=3, padx=(6, 0))
        self.output_rows = []
        for row in (1, 2):
            name, state = ttk.Label(output), ttk.Label(output, foreground='#555555')
            name.grid(row=row, column=1, sticky='w', padx=6, pady=(4, 0))
            state.grid(row=row, column=2, columnspan=2, sticky='w', pady=(4, 0))
            self.output_rows.append((name, state))
        ttk.Checkbutton(output, text=tr('Regenerate existing files'), variable=self.regenerate).grid(
            row=3, column=1, sticky='w', padx=6, pady=(6, 0))
        ttk.Checkbutton(output, text=tr('Process right after transcription'),
                        variable=self.process_after).grid(row=4, column=1, sticky='w', padx=6)
        output.columnconfigure(1, weight=1)

        run = ttk.Frame(self, padding=(0, 8, 0, 0))
        run.pack(fill='both', expand=True)
        buttons = ttk.Frame(run)
        buttons.pack(fill='x')
        self.start_button = ttk.Button(buttons, text=tr('Start'), command=self.start)
        self.start_button.pack(side='left')
        self.cancel_button = ttk.Button(buttons, text=tr('Cancel'), state='disabled', command=self.cancel_run)
        self.cancel_button.pack(side='left', padx=6)
        self.step_labels = []
        for index, step in enumerate(STEPS, 1):
            label = ttk.Label(buttons)
            label.pack(side='left', padx=(12, 0))
            self.step_labels.append((index, step, label))
        self.progress = ttk.Progressbar(run, mode='determinate', maximum=100)
        self.progress.pack(fill='x', pady=(8, 0))
        log_frame = ttk.LabelFrame(run, text=tr('Console output'), padding=4)
        log_frame.pack(fill='both', expand=True, pady=(8, 0))
        self.log = ScrolledText(log_frame, height=8, wrap='word', state='disabled')
        self.log.pack(fill='both', expand=True)
        self.show_steps({})

    # State

    @staticmethod
    def set_choice(variable, choices, key):
        match = next((choice for choice in choices if choice[0] == key), choices[0])
        variable.set(tr(match[1]))

    @staticmethod
    def choice_key(variable, choices):
        return next((choice[0] for choice in choices if tr(choice[1]) == variable.get()), choices[0][0])

    def describe(self, key):
        variable, choices, description = self.descriptions[key]
        match = next((choice for choice in choices if tr(choice[1]) == variable.get()), choices[0])
        description.set(tr(match[2]))

    def remember(self):
        self.settings['separation_engine'] = self.choice_key(self.separation, SEPARATION_ENGINES)
        self.settings['transcription_engine'] = self.choice_key(self.transcription, TRANSCRIPTION_ENGINES)
        self.settings['transcribe_device'] = self.choice_key(self.device, DEVICES)
        self.settings['transcribe_process_after'] = self.process_after.get()
        self.save_settings(self.settings)

    def show_steps(self, states):
        for index, step, label in self.step_labels:
            label.configure(text='{} {}  {}'.format('①②③'[index - 1], tr(step),
                                                    tr(states.get(step, 'Waiting'))))

    def refresh(self):
        """Describe the chosen file and what a run would write."""
        path = self.audio.get().strip()
        extension = os.path.splitext(path)[1].lower()
        is_midi = extension in MIDI_EXTENSIONS
        if is_midi:
            self.open_midi_button.grid()
        else:
            self.open_midi_button.grid_remove()
        if path != self.last_audio:
            self.last_audio = path
            if extension in AUDIO_EXTENSIONS:
                self.settings['transcribe_audio'] = path
                self.save_settings(self.settings)
                # A file already named *_piano is taken as a separated stem.
                if os.path.splitext(os.path.basename(path))[0].lower().endswith('_piano'):
                    self.set_choice(self.separation, SEPARATION_ENGINES, 'none')
        folder = self.output_dir.get() or (os.path.dirname(path) if path else '')
        self.folder_label.configure(text=folder if self.output_dir.get() else
                                    (tr('Same folder as the audio') + ('  ' + folder if folder else '')))
        ready = False
        if not path:
            self.audio_info.set(tr('Choose a song or a piano recording.'))
            rows = []
        elif is_midi:
            self.audio_info.set(tr('This is a MIDI file; clean it up in the Processing tab.'))
            rows = []
        elif extension not in AUDIO_EXTENSIONS:
            self.audio_info.set(tr('Unsupported file type.'))
            rows = []
        elif not os.path.isfile(path):
            self.audio_info.set(tr('File not found.'))
            rows = []
        else:
            self.audio_info.set(audio_summary(path))
            separate = self.choice_key(self.separation, SEPARATION_ENGINES) != 'none'
            piano, midi = output_paths(path, separate, self.output_dir.get())
            rows = ([piano] if piano else []) + [midi]
            ready = True
        for index, (name, state) in enumerate(self.output_rows):
            if index < len(rows):
                exists = os.path.exists(rows[index])
                name.configure(text=os.path.basename(rows[index]))
                if exists and not self.regenerate.get():
                    state.configure(text=tr('exists, will be skipped'))
                elif exists:
                    state.configure(text=tr('exists, will be overwritten'))
                else:
                    state.configure(text=tr('will be created'))
            else:
                name.configure(text='')
                state.configure(text='')
        self.start_button.configure(state='normal' if ready and self.worker is None else 'disabled')
        self.show_models()

    # Actions

    def choose_audio(self):
        current = self.audio.get().strip()
        path = filedialog.askopenfilename(
            title=tr('Choose a song or a piano recording'),
            initialdir=os.path.dirname(current) if current else None,
            filetypes=[(tr('Audio'), ' '.join('*' + e for e in AUDIO_EXTENSIONS)),
                       ('MIDI', '*.mid *.midi'), (tr('All files'), '*')])
        if path:
            self.audio.set(path)

    def choose_output_dir(self):
        path = filedialog.askdirectory(title=tr('Choose the output folder'),
                                       initialdir=self.output_dir.get() or None)
        if path:
            self.output_dir.set(path)
            self.settings['transcribe_output_dir'] = path
            self.save_settings(self.settings)

    def write_raw(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text.replace('\r\n', '\n').replace('\r', '\n'))
        self.log.see('end')
        self.log.configure(state='disabled')

    def write_log(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text.rstrip('\n') + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    # Models

    def separating(self):
        return self.choice_key(self.separation, SEPARATION_ENGINES) != 'none'

    def needed_models(self):
        models = []
        if self.separating():
            import separation
            models.append(separation.required_model())
        models.append(model_store.MODELS['bytedance'])
        return models

    def show_models(self):
        parts = []
        for model in self.needed_models():
            state = tr('ready') if model.ready() else tr('not downloaded (about {} MB)').format(model.size_mb())
            parts.append('{}: {}'.format(model.name, state))
        self.models_label.configure(text='   '.join(parts))
        missing = any(not model.ready() for model in self.needed_models())
        self.download_button.configure(state='normal' if missing and self.worker is None else 'disabled')

    def confirm_downloads(self):
        """Ask once before fetching missing weights; False if the user declines."""
        missing = [model for model in self.needed_models() if not model.ready()]
        if not missing:
            return True
        lines = ['{}: {} MB ({})'.format(model.name, model.size_mb(), ', '.join(model.source_hosts()))
                 for model in missing]
        return messagebox.askyesno(
            tr('Download models'),
            tr('These models are not on this computer yet and will be downloaded:') + '\n\n' +
            '\n'.join(lines) + '\n\n' + tr('Saved to {}').format(model_store.MODELS_DIR),
            parent=self)

    def missing_package_message(self):
        if not self.separating():
            return None
        import separation
        package = separation.missing_package()
        if package:
            return tr('Separation needs the Python package {}. Install it with: python -m pip install {}').format(
                package, package)
        return None

    # Running

    def download_only(self):
        if self.confirm_downloads():
            self.launch(download_only=True)

    def start(self):
        message = self.missing_package_message()
        if message:
            messagebox.showerror(tr('Separate & transcribe'), message, parent=self)
            return
        if self.confirm_downloads():
            self.launch(download_only=False)

    def cancel_run(self):
        self.cancel.set()
        self.write_log(tr('Cancelling...'))

    def launch(self, download_only):
        path = self.audio.get().strip()
        separate = self.separating()
        piano, midi = output_paths(path, separate, self.output_dir.get()) if path else (None, None)
        job = {
            'download_only': download_only,
            'audio': path,
            'piano': piano,
            'midi': midi,
            'models': self.needed_models(),
            'device': self.choice_key(self.device, DEVICES),
            'regenerate': self.regenerate.get(),
        }
        self.cancel.clear()
        self.worker = threading.Thread(target=self.work, args=(job,), daemon=True)
        self.start_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.download_button.configure(state='disabled')
        self.progress.configure(value=0)
        self.show_steps({})
        self.worker.start()
        self.after(100, self.poll)

    def work(self, job):
        """Background thread: only talks to the interface through self.messages.

        Everything the engines print (model loading, chunk timing, ByteDance's segment counter)
        goes to the output box unchanged, as the Processing tab does with its report.
        """
        put = self.messages.put
        writer = _QueueWriter(put)
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            self.run_job(job, put)

    def run_job(self, job, put):
        log = print
        try:
            for model in job['models']:
                if not model.ready():
                    put(('step', None, None))
                    model_store.download(model, cancel=self.cancel, log=log,
                                         progress=lambda done, total: put(('progress', 100 * done / total)))
                    log(tr('Saved to {}').format(model.path))
            if job['download_only']:
                put(('done', None))
                return
            regenerate = job['regenerate']
            if job['piano']:
                if os.path.exists(job['piano']) and not regenerate:
                    put(('step', 'Separate', 'Skipped'))
                    log(tr('{} exists; separation skipped.').format(os.path.basename(job['piano'])))
                else:
                    put(('step', 'Separate', 'Running'))
                    import separation
                    log(tr('Separating the piano with BS-RoFormer SW ({})...').format(separation.backend()))
                    temporary = job['piano'] + '.partial.wav'
                    finished = separation.separate_piano(
                        job['audio'], temporary, job['device'], cancel=self.cancel, log=log,
                        progress=lambda done, total: put(('progress', 100 * done / total)))
                    if not finished:
                        raise model_store.DownloadCancelled()
                    os.replace(temporary, job['piano'])
                    put(('step', 'Separate', 'Done'))
            else:
                put(('step', 'Separate', 'Not used'))
            source = job['piano'] or job['audio']
            if os.path.exists(job['midi']) and not regenerate:
                put(('step', 'Transcribe', 'Skipped'))
                log(tr('{} exists; transcription skipped.').format(os.path.basename(job['midi'])))
            else:
                put(('step', 'Transcribe', 'Running'))
                put(('progress', 0))
                log(tr('Transcribing {}...').format(os.path.basename(source)))
                self.transcribe(source, job['midi'], job['device'])
                put(('progress', 100))
                put(('step', 'Transcribe', 'Done'))
            put(('done', job['midi']))
        except model_store.DownloadCancelled:
            put(('cancelled', None))
        except Exception as error:
            put(('error', str(error)))
        finally:
            for path in (job['piano'], job['midi']):
                if path:
                    for partial in (path + '.partial.wav', os.path.splitext(path)[0] + '.partial.mid'):
                        if os.path.exists(partial):
                            os.remove(partial)

    @staticmethod
    def transcribe(audio_path, midi_path, device):
        import librosa
        import torch
        from piano_transcription_inference import PianoTranscription, sample_rate
        use_cuda = device != 'cpu' and torch.cuda.is_available()
        transcriptor = PianoTranscription(device=torch.device('cuda' if use_cuda else 'cpu'),
                                          checkpoint_path=str(model_store.MODELS['bytedance'].path))
        audio, _ = librosa.load(audio_path, sr=sample_rate, mono=True)
        temporary = os.path.splitext(midi_path)[0] + '.partial.mid'
        transcriptor.transcribe(audio, temporary)
        os.replace(temporary, midi_path)

    def poll(self):
        states = getattr(self, 'step_states', {})
        finished = False
        while True:
            try:
                kind, *values = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == 'text':
                self.write_raw(values[0])
            elif kind == 'progress':
                self.progress.configure(value=values[0])
            elif kind == 'step':
                if values[0]:
                    states[values[0]] = values[1]
                    self.show_steps(states)
            elif kind == 'error':
                self.write_log(tr('Failed: {}').format(values[0]))
                self.status.set(tr('Failed: {}').format(values[0]))
                finished = True
            elif kind == 'cancelled':
                self.write_log(tr('Cancelled.'))
                self.status.set(tr('Cancelled.'))
                finished = True
            elif kind == 'done':
                finished = True
                midi = values[0]
                if midi is None:
                    self.write_log(tr('Models are ready.'))
                else:
                    states['Hand over to Processing'] = 'Done'
                    self.show_steps(states)
                    self.write_log(tr('Done: {}').format(midi))
                    self.status.set(tr('Done: {}').format(midi))
                    self.on_midi_ready(midi, self.audio.get().strip(), self.process_after.get())
        self.step_states = states
        if finished:
            self.worker = None
            self.step_states = {}
            self.cancel_button.configure(state='disabled')
            self.refresh()
        else:
            self.after(100, self.poll)


class _QueueWriter:
    """File-like object that forwards printed text to the tab's message queue."""

    def __init__(self, put):
        self.put = put

    def write(self, text):
        if text:
            self.put(('text', text))
        return len(text)

    def flush(self):
        pass
