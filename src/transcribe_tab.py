"""Separation and transcription tab: from a song or piano recording to a MIDI file."""

import os
import tkinter as tk
from tkinter import filedialog, ttk
from tkinter.scrolledtext import ScrolledText

from gui_i18n import tr


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
        self.build()
        self.set_choice(self.separation, SEPARATION_ENGINES, settings.get('separation_engine'))
        self.set_choice(self.transcription, TRANSCRIPTION_ENGINES, settings.get('transcription_engine'))
        self.set_choice(self.device, DEVICES, settings.get('transcribe_device'))
        for variable in (self.audio, self.output_dir, self.separation, self.regenerate):
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
        self.cancel_button = ttk.Button(buttons, text=tr('Cancel'), state='disabled')
        self.cancel_button.pack(side='left', padx=6)
        self.step_labels = []
        for index, step in enumerate(STEPS, 1):
            label = ttk.Label(buttons)
            label.pack(side='left', padx=(12, 0))
            self.step_labels.append((index, step, label))
        self.progress = ttk.Progressbar(run, mode='determinate', maximum=100)
        self.progress.pack(fill='x', pady=(8, 0))
        log_frame = ttk.LabelFrame(run, text=tr('Log'), padding=4)
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
        self.start_button.configure(state='normal' if ready else 'disabled')

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

    def write_log(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text.rstrip('\n') + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def start(self):
        # Interface first: the engines are connected in a later change.
        self.write_log(tr('The engines are not connected yet; this tab is an interface preview.'))
        self.status.set(tr('The engines are not connected yet; this tab is an interface preview.'))
