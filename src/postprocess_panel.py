import contextlib
import json
import os
import queue
import re
import sys
import threading
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont
from tkinter.scrolledtext import ScrolledText

from gui_i18n import LANGUAGES, get_language, set_language, tr, tr_report
import postprocess
from midi_stats import source_stats_path_for
from pianoroll_canvas import PianoRollView
from trim_overlaps import DEFAULT_FAST_FIGURE_HANDS
from app_info import APP_NAME, VERSION, LICENSE_ID, PROJECT_URL, ISSUES_URL, BUNDLED_COMPONENTS


# Last choices and saved presets, per user.
SETTINGS_PATH = os.path.join(os.path.expanduser('~'), '.piano-midi-cleanup-panel.json')
PARAMETER_TABS = (
    ('clean', 'Rule cleaning'),
    ('split', 'Hand split'),
    ('trim', 'Overlap trim'),
    ('key', 'Key signatures'),
    ('playable', 'Playable'),
)
HAND_ORDER = ('left', 'right', 'second_left', 'second_right')
# Step switches and their defaults; presets store these and the thresholds, not the files.
SWITCHES = (
    ('first_beat', 'Auto first beat (after BPMFix)', True),
    ('key_signature', 'Key signatures (after BPMFix)', True),
    ('clean', 'Rule cleaning', True),
    ('semitone_pairs', 'Semitone pairs (in rule cleaning)', False),
    ('playable', 'Playable reduction (one player, 2 hands)', False),
    ('split', 'Hand split', True),
    ('trim', 'Overlap trim', True),
)


def default_preset():
    return {
        'switches': {name: default for name, _, default in SWITCHES},
        'hands': 2,
        'fast_figure_hands': list(DEFAULT_FAST_FIGURE_HANDS),
        'protect_pedal': False,
        'snapshot': '',
        'parameters': {key: str(value) for key, value in postprocess.PARAMETER_DEFAULTS.items()},
    }


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as file:
            return json.load(file)
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as file:
        json.dump(settings, file, ensure_ascii=False, indent=2)


class QueueWriter:
    def __init__(self, lines):
        self.lines = lines

    def write(self, text):
        self.lines.put(text)

    def flush(self):
        pass


class Panel:
    def __init__(self, root):
        self.root = root
        self.settings = load_settings()
        set_language(self.settings.get('language', '繁體中文'))
        self.lines = queue.Queue()
        self.running = False

        self.midi = tk.StringVar(value=self.settings.get('midi', ''))
        self.source = tk.StringVar(value=self.settings.get('source', 'reuse'))
        self.bpm = tk.StringVar(value=self.settings.get('bpm', ''))
        self.audio = tk.StringVar(value=self.settings.get('audio', ''))
        self.switches = {name: tk.BooleanVar() for name, _, _ in SWITCHES}
        self.hands = tk.StringVar()
        self.fast_figure = {hand: tk.BooleanVar() for hand in HAND_ORDER}
        self.protect_pedal = tk.BooleanVar()
        self.snapshot = tk.StringVar()
        self.parameters = {parameter.key: tk.StringVar() for parameter in postprocess.PARAMETERS}
        self.preset_name = tk.StringVar()
        self.status = tk.StringVar(value=tr('Ready'))
        self.status_fragment = ''
        self.report_text = ''
        self.roll = None
        self.build_ui()
        self.load_existing_report()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.drain)

    def close(self):
        self.roll.shutdown()
        self.root.destroy()

    def build_ui(self):
        selected_tab = self.main_tabs.index('current') if hasattr(self, 'main_tabs') else 0
        if self.roll is not None:
            self.roll.shutdown()
        for child in self.root.winfo_children():
            child.destroy()
        self.root.title(tr('Piano MIDI Cleanup'))
        language_bar = ttk.Frame(self.root, padding=(8, 8, 8, 0))
        language_bar.pack(fill='x')
        ttk.Label(language_bar, text=tr('Language')).pack(side='left')
        self.language = tk.StringVar(value=get_language())
        language_box = ttk.Combobox(language_bar, textvariable=self.language,
                                    values=tuple(LANGUAGES), width=12, state='readonly')
        language_box.pack(side='left', padx=6)
        language_box.bind('<<ComboboxSelected>>', self.change_language)
        self.main_tabs = ttk.Notebook(self.root)
        self.main_tabs.pack(fill='both', expand=True, padx=8, pady=(8, 0))
        processing = ttk.Frame(self.main_tabs)
        report = ttk.Frame(self.main_tabs, padding=8)
        about = ttk.Frame(self.main_tabs, padding=8)
        self.roll = PianoRollView(self.main_tabs, midi_port=self.settings.get('playback_output'),
                                  on_output=self.save_midi_output, on_first_beat=self.first_beat_saved,
                                  status=self.status)
        self.roll.configure(padding=8)
        self.main_tabs.add(processing, text=tr('Processing'))
        self.main_tabs.add(self.roll, text=tr('Piano roll'))
        self.main_tabs.add(report, text=tr('Detailed report'))
        self.main_tabs.add(about, text=tr('About'))
        self.main_tabs.bind('<<NotebookTabChanged>>', self.main_tab_changed)
        self.build_files(ttk.Frame(processing, padding=8))
        body = ttk.Frame(processing, padding=(8, 0, 8, 8))
        body.pack(fill='both', expand=True)
        steps = ttk.LabelFrame(body, text=tr('Steps'), padding=8)
        self.build_steps(steps)
        self.build_parameters(ttk.LabelFrame(body, text=tr('Thresholds'), padding=8))
        self.build_report(report)
        self.build_about(about)
        self.main_tabs.select(selected_tab)
        self.build_run(steps)
        self.show_preset(self.settings.get('last') or default_preset())
        self.update_source()

    def build_about(self, frame):
        canvas = tk.Canvas(frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=canvas.yview)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        content = ttk.Frame(canvas, padding=16)
        window = canvas.create_window(0, 0, window=content, anchor='nw')
        content.bind('<Configure>', lambda _event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(window, width=event.width))
        content.columnconfigure(1, weight=1)

        title_font = tkfont.nametofont('TkDefaultFont').copy()
        title_font.configure(size=16, weight='bold')
        self.about_title_font = title_font
        ttk.Label(content, text=APP_NAME, font=title_font).grid(row=0, column=0, columnspan=3, sticky='w')
        ttk.Label(content, text=tr('Version: {} (development build)').format(VERSION)).grid(
            row=1, column=0, columnspan=3, sticky='w', pady=(4, 12))
        ttk.Label(content, text=tr('Prepare piano transcription MIDI for reading and editing.')).grid(
            row=2, column=0, columnspan=3, sticky='w')
        links = ttk.Frame(content)
        links.grid(row=3, column=0, columnspan=3, sticky='w', pady=(12, 24))
        ttk.Button(links, text=tr('Project home'), command=lambda: webbrowser.open(PROJECT_URL)).pack(side='left')
        ttk.Button(links, text=tr('Report an issue'), command=lambda: webbrowser.open(ISSUES_URL)).pack(
            side='left', padx=8)
        ttk.Label(content, text=tr('Bundled open-source components')).grid(
            row=4, column=0, columnspan=3, sticky='w', pady=(0, 8))
        for column, heading in enumerate(('Component', 'Purpose', 'License')):
            ttk.Label(content, text=tr(heading)).grid(row=5, column=column, sticky='w', padx=(0, 20))
        for row, (name, purpose, license_name, url, license_path, credit) in enumerate(BUNDLED_COMPONENTS, 6):
            ttk.Button(content, text=name, command=lambda url=url: webbrowser.open(url)).grid(
                row=row, column=0, sticky='w', padx=(0, 20), pady=6)
            ttk.Label(content, text=tr(purpose) + '\n' + credit).grid(
                row=row, column=1, sticky='w', padx=(0, 20), pady=6)
            ttk.Button(content, text=license_name,
                       command=lambda path=license_path, name=name: self.show_license(name, path)).grid(
                row=row, column=2, sticky='w', pady=6)
        ttk.Separator(content).grid(row=9, column=0, columnspan=3, sticky='ew', pady=16)
        ttk.Label(content, text=tr('Project license')).grid(row=10, column=0, columnspan=3, sticky='w')
        ttk.Button(content, text=LICENSE_ID, command=lambda: self.show_license(APP_NAME, 'LICENSE')).grid(
            row=11, column=0, sticky='w', pady=(4, 16))
        ttk.Label(content, text=tr('Third-party components retain their respective licenses.')).grid(
            row=11, column=1, columnspan=2, sticky='w', pady=(4, 16))
        ttk.Label(content, text=tr('This interface processes MIDI. Audio transcription is performed by upstream models.')).grid(
            row=12, column=0, columnspan=3, sticky='w')

    def show_license(self, name, relative_path):
        path = Path(__file__).resolve().parents[1] / relative_path
        try:
            text = path.read_text(encoding='utf-8')
        except OSError as error:
            messagebox.showerror(tr('License'), str(error), parent=self.root)
            return
        window = tk.Toplevel(self.root)
        window.title('{} — {}'.format(name, tr('License')))
        window.geometry('760x560')
        viewer = ScrolledText(window, wrap='word', padx=12, pady=12)
        viewer.pack(fill='both', expand=True)
        viewer.insert('1.0', text)
        viewer.configure(state='disabled')

    def change_language(self, _event=None):
        self.settings.update(midi=self.midi.get(), source=self.source.get(), bpm=self.bpm.get(),
                             audio=self.audio.get(), last=self.current_preset(), language=self.language.get())
        save_settings(self.settings)
        set_language(self.language.get())
        self.build_ui()

    def build_files(self, frame):
        frame.pack(fill='x')
        ttk.Label(frame, text=tr('MIDI')).grid(row=0, column=0, sticky='w')
        midi_entry = ttk.Entry(frame, textvariable=self.midi, width=70)
        midi_entry.grid(row=0, column=1, columnspan=3, sticky='we')
        midi_entry.bind('<FocusOut>', self.load_existing_report)
        midi_entry.bind('<Return>', self.load_existing_report)
        ttk.Button(frame, text=tr('Choose...'), command=self.choose_midi).grid(row=0, column=4)
        ttk.Button(frame, text=tr('Piano Roll / First Beat'),
                   command=lambda: self.main_tabs.select(self.roll)).grid(row=0, column=5, padx=(6, 0))
        ttk.Radiobutton(frame, text=tr('Reuse song_bpmfix.mid'), variable=self.source, value='reuse',
                        command=self.update_source).grid(row=1, column=1, sticky='w')
        ttk.Radiobutton(frame, text=tr('Known BPM'), variable=self.source, value='bpm',
                        command=self.update_source).grid(row=2, column=1, sticky='w')
        self.bpm_entry = ttk.Entry(frame, textvariable=self.bpm, width=10)
        self.bpm_entry.grid(row=2, column=2, sticky='w')
        ttk.Radiobutton(frame, text=tr('Detect from audio'), variable=self.source, value='audio',
                        command=self.update_source).grid(row=3, column=1, sticky='w')
        self.audio_entry = ttk.Entry(frame, textvariable=self.audio, width=50)
        self.audio_entry.grid(row=3, column=2, columnspan=2, sticky='we')
        self.audio_button = ttk.Button(frame, text=tr('Choose...'), command=self.choose_audio)
        self.audio_button.grid(row=3, column=4)
        frame.columnconfigure(3, weight=1)

    @staticmethod
    def focus_tabs(event):
        """Leave no entry focused after a tab change; a hidden entry would still receive keys."""
        event.widget.focus_set()

    def main_tab_changed(self, event):
        self.focus_tabs(event)
        self.refresh_roll()
        if self.main_tabs.select() == str(self.roll):
            self.roll.focus_roll()

    def refresh_roll(self):
        """Preview the processed result when available, otherwise the selected input."""
        if self.main_tabs.select() == str(self.roll) and not self.running:
            midi_path = self.midi.get().strip()
            if midi_path:
                source_path = postprocess.source_midi_path(midi_path)
                cleaned_path = postprocess.cleaned_path_for(postprocess.bpmfix_path_for(source_path))
                if os.path.isfile(cleaned_path):
                    midi_path = cleaned_path
            self.roll.show(midi_path)

    def save_midi_output(self, name):
        self.settings['playback_output'] = name
        save_settings(self.settings)

    def first_beat_saved(self, _target):
        self.load_existing_report(update_status=False)

    def build_steps(self, frame):
        frame.pack(side='left', fill='y', padx=(0, 8))
        frame.columnconfigure(1, pad=40)
        self.step_checks = {}
        for row, (name, label, _) in enumerate(SWITCHES):
            check = ttk.Checkbutton(frame, text=tr(label), variable=self.switches[name])
            check.grid(row=row, column=0, columnspan=2, sticky='w', pady=3)
            self.step_checks[name] = check
        row = len(SWITCHES)
        ttk.Label(frame, text=tr('Snapshots')).grid(row=row, column=0, sticky='w', pady=(8, 0))
        snapshot_values = ('', 'landscape', 'portrait')
        self.snapshot_values = {tr(value) if value else tr('No image'): value for value in snapshot_values}
        ttk.Combobox(frame, textvariable=self.snapshot, values=tuple(self.snapshot_values), width=10,
                     state='readonly').grid(row=row, column=1, sticky='w', pady=(8, 0))

    def build_parameters(self, frame):
        frame.pack(side='left', fill='both', expand=True)
        tabs = ttk.Notebook(frame)
        tabs.pack(fill='both', expand=True)
        tabs.bind('<<NotebookTabChanged>>', self.focus_tabs)
        for prefix, title in PARAMETER_TABS:
            tab = ttk.Frame(tabs, padding=8)
            tabs.add(tab, text=tr(title))
            parameter_frame = tab
            if prefix == 'split':
                ttk.Label(tab, text=tr('Hands')).grid(row=0, column=0, sticky='w')
                ttk.Combobox(tab, textvariable=self.hands, values=('2', '3', '4'), width=4,
                             state='readonly').grid(row=0, column=1, sticky='w')
                parameter_frame = ttk.Frame(tab)
                parameter_frame.grid(row=1, column=0, columnspan=2, sticky='w', pady=(12, 0))
            elif prefix == 'trim':
                ttk.Checkbutton(tab, text=tr('Protect pedal sustain'), variable=self.protect_pedal).grid(
                    row=0, column=0, columnspan=2, sticky='w')
                ttk.Label(tab, text=tr('Keep note tails that trimming could silence earlier; fewer notes may be shortened.'),
                          wraplength=340).grid(row=1, column=0, columnspan=2, sticky='w', pady=(0, 8))
                ttk.Label(tab, text=tr('Fast figure hands')).grid(row=2, column=0, sticky='nw')
                hands = ttk.Frame(tab)
                hands.grid(row=2, column=1, sticky='w')
                for index, hand in enumerate(HAND_ORDER):
                    ttk.Checkbutton(hands, text=tr(hand.replace('_', ' ')),
                                    variable=self.fast_figure[hand]).grid(
                        row=index // 2, column=index % 2, sticky='w', padx=(0, 12))
                parameter_frame = ttk.Frame(tab)
                parameter_frame.grid(row=3, column=0, columnspan=2, sticky='w', pady=(12, 0))
            self.add_parameter_rows(parameter_frame, [
                parameter for parameter in postprocess.PARAMETERS
                if parameter.key.startswith(prefix + '.')
            ])
        presets = ttk.Frame(frame)
        presets.pack(fill='x', pady=(8, 0))
        ttk.Label(presets, text=tr('Preset')).pack(side='left')
        self.preset_box = ttk.Combobox(presets, textvariable=self.preset_name, width=16, state='readonly')
        self.preset_box.pack(side='left', padx=4)
        self.preset_box.bind('<<ComboboxSelected>>', lambda _: self.load_preset())
        ttk.Button(presets, text=tr('Save as...'), command=self.save_preset).pack(side='left')
        ttk.Button(presets, text=tr('Delete'), command=self.delete_preset).pack(side='left')
        ttk.Button(presets, text=tr('Defaults'), command=lambda: self.show_preset(default_preset())).pack(side='right')
        self.refresh_presets()

    def add_parameter_rows(self, frame, parameters):
        for row, parameter in enumerate(parameters):
            ttk.Label(frame, text=tr(parameter.label)).grid(row=row, column=0, sticky='w', pady=4)
            ttk.Entry(frame, textvariable=self.parameters[parameter.key], width=8).grid(row=row, column=1, padx=4)
            ttk.Label(frame, text=tr('default {}').format(postprocess.PARAMETER_DEFAULTS[parameter.key]),
                      foreground='gray').grid(row=row, column=2, sticky='w')

    def build_run(self, frame):
        frame.rowconfigure(len(SWITCHES) + 1, weight=1)
        self.run_button = ttk.Button(frame, text=tr('Run'), command=self.run)
        self.run_button.grid(row=len(SWITCHES) + 1, column=0, columnspan=2, sticky='se', pady=(12, 0))
        if self.running:
            self.run_button.configure(state='disabled')
        status_bar = tk.Frame(self.root, background='SystemButtonFace',
                              relief='raised', borderwidth=1)
        status_bar.pack(side='bottom', fill='x')
        ttk.Sizegrip(status_bar).pack(side='right', anchor='se', padx=2, pady=2)
        tk.Label(status_bar, textvariable=self.status, width=1, anchor='w',
                 background='white', relief='sunken', borderwidth=1,
                 padx=4, pady=1).pack(side='left', fill='x', expand=True, padx=2, pady=2)

    def build_report(self, frame):
        frame.grid_propagate(False)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.report_tree = ttk.Treeview(frame, columns=('value',), show='tree headings')
        self.report_tree.heading('#0', text=tr('Item'), anchor='w')
        self.report_tree.heading('value', text=tr('Value'), anchor='w')
        self.report_tree.column('#0', width=280, minwidth=160, stretch=False)
        self.report_tree.column('value', width=450, minwidth=200)
        self.report_tree.tag_configure('section', background='#e8e8e8')
        vertical = ttk.Scrollbar(frame, orient='vertical', command=self.report_tree.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=self.report_tree.xview)
        self.report_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.report_tree.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.render_report()

    def render_report(self):
        self.report_tree.delete(*self.report_tree.get_children())
        section = subgroup = ''
        longest_value = 0
        font = tkfont.nametofont('TkDefaultFont')

        def add(parent, label, value='', section_row=False):
            nonlocal longest_value
            longest_value = max(longest_value, font.measure(value))
            return self.report_tree.insert(
                parent, 'end', text=label, values=(value,), open=True,
                tags=('section',) if section_row else (),
            )

        for raw_line in self.report_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith('MIDI ') and line.endswith((' stats', ' run')):
                section = add('', tr_report(line), section_row=True)
                subgroup = ''
                continue
            bullet = line.startswith('- ')
            key, separator, value = (line[2:] if bullet else line).partition(':')
            value = value.strip()
            if not separator:
                add(section, tr('Details'), tr_report(line))
                continue
            if not value:
                subgroup = add(section, tr_report(key), section_row=True)
                continue
            parent = subgroup if bullet and subgroup else section
            if not bullet:
                subgroup = ''
            channel = re.fullmatch(r'(\d+) notes, (.+)', value)
            if key.startswith('Channel ') and channel:
                channel_row = add(parent, tr_report(key), tr_report(channel[1] + ' notes'), True)
                for metric in channel[2].split(', '):
                    match = re.fullmatch(r'(.+?) (\d+(?: -> \d+)?)', metric)
                    if match:
                        add(channel_row, tr_report(match[1]), match[2].replace(' -> ', ' → '))
                    else:
                        add(channel_row, tr('Details'), metric)
            else:
                file_field = key in ('MIDI', 'Audio', 'Input', 'Output', 'Input MIDI', 'Output MIDI')
                display_value = value if file_field and value != 'N/A' else tr_report(value)
                add(parent, tr_report(key), display_value)
        self.report_tree.column('value', width=max(450, longest_value + 20))

    def choose_midi(self):
        path = filedialog.askopenfilename(title=tr('Choose song.mid, song_bpmfix.mid or song_bpmfix_cleaned.mid'),
                                          filetypes=[(tr('MIDI files'), '*.mid *.midi'), (tr('All files'), '*.*')])
        if path:
            self.midi.set(path)
            self.load_existing_report()

    def load_existing_report(self, event=None, update_status=True):
        if self.running:
            return
        midi_path = self.midi.get().strip()
        if not midi_path:
            content = tr('Choose a MIDI file to view its report.')
            message = tr('Ready')
        else:
            report_path = source_stats_path_for(midi_path)
            try:
                with open(report_path, encoding='utf-8-sig') as report:
                    content = report.read()
                message = tr('Report loaded: {}').format(os.path.basename(report_path))
            except FileNotFoundError:
                content = tr('No existing report found: {}').format(report_path)
                message = tr('No existing report.')
            except (OSError, UnicodeError) as error:
                content = tr('Could not read report: {}').format(error)
                message = content
        self.report_text = content
        self.render_report()
        self.report_tree.yview_moveto(0)
        if update_status:
            self.status.set(message)
        self.refresh_roll()

    def choose_audio(self):
        path = filedialog.askopenfilename(title=tr('Choose source audio for BPM detection'),
                                          filetypes=[(tr('Audio files'), '*.flac *.mp3 *.wav *.m4a *.aif *.aiff'),
                                                     (tr('All files'), '*.*')])
        if path:
            self.audio.set(path)
            self.source.set('audio')
            self.update_source()

    def update_source(self):
        source = self.source.get()
        self.bpm_entry.configure(state='normal' if source == 'bpm' else 'disabled')
        self.audio_entry.configure(state='normal' if source == 'audio' else 'disabled')
        self.audio_button.configure(state='normal' if source == 'audio' else 'disabled')
        for name in ('first_beat', 'key_signature'):
            self.step_checks[name].configure(state='disabled' if source == 'reuse' else 'normal')

    def current_preset(self):
        return {
            'switches': {name: variable.get() for name, variable in self.switches.items()},
            'hands': int(self.hands.get()),
            'fast_figure_hands': [hand for hand in HAND_ORDER if self.fast_figure[hand].get()],
            'protect_pedal': self.protect_pedal.get(),
            'snapshot': self.snapshot_values.get(self.snapshot.get(), self.snapshot.get()),
            'parameters': {key: variable.get().strip() for key, variable in self.parameters.items()},
        }

    def show_preset(self, preset):
        preset = dict(default_preset(), **preset)
        for name, variable in self.switches.items():
            variable.set(preset['switches'].get(name, dict((n, d) for n, _, d in SWITCHES)[name]))
        self.hands.set(str(preset['hands']))
        self.protect_pedal.set(preset['protect_pedal'])
        for hand, variable in self.fast_figure.items():
            variable.set(hand in preset['fast_figure_hands'])
        snapshot = 'landscape' if preset['snapshot'] == 'both' else preset['snapshot']
        self.snapshot.set(tr(snapshot) if snapshot else tr('No image'))
        defaults = default_preset()['parameters']
        for key, variable in self.parameters.items():
            variable.set(preset['parameters'].get(key, defaults[key]))

    def refresh_presets(self):
        self.preset_box.configure(values=sorted(self.settings.get('presets', {})))

    def load_preset(self):
        preset = self.settings.get('presets', {}).get(self.preset_name.get())
        if preset:
            self.show_preset(preset)

    def save_preset(self):
        name = simpledialog.askstring(tr('Save preset'), tr('Preset name'), initialvalue=self.preset_name.get(), parent=self.root)
        if not name:
            return
        self.settings.setdefault('presets', {})[name] = self.current_preset()
        save_settings(self.settings)
        self.refresh_presets()
        self.preset_name.set(name)

    def delete_preset(self):
        name = self.preset_name.get()
        if name in self.settings.get('presets', {}) and messagebox.askyesno(tr('Delete'), tr('Delete "{}"?').format(name)):
            del self.settings['presets'][name]
            save_settings(self.settings)
            self.refresh_presets()
            self.preset_name.set('')

    def argv(self, preset):
        """The postprocess.py command line for the panel's choices."""
        args = [self.midi.get().strip()]
        source = self.source.get()
        if source == 'bpm':
            args += ['--bpm', self.bpm.get().strip()]
        elif source == 'audio':
            args += ['--audio', self.audio.get().strip()]
        switches = preset['switches']
        for name, flag in (('first_beat', '--no-first-beat'), ('key_signature', '--no-key-signature'),
                           ('clean', '--no-clean'), ('split', '--no-split'), ('trim', '--no-trim')):
            if not switches[name]:
                args.append(flag)
        for name, flag in (('semitone_pairs', '--semitone-pairs'), ('playable', '--playable')):
            if switches[name]:
                args.append(flag)
        args += ['--hands', str(preset['hands'])]
        if switches['trim']:
            if not preset['protect_pedal']:
                args.append('--no-pedal-protection')
            if not preset['fast_figure_hands']:
                raise ValueError(tr('Choose at least one fast figure hand, or turn overlap trim off.'))
            args += ['--fast-figure-hands'] + preset['fast_figure_hands']
        if preset['snapshot']:
            args += ['--snapshot', preset['snapshot']]
        for parameter in postprocess.PARAMETERS:
            text = preset['parameters'][parameter.key]
            try:
                value = parameter.kind(text)
            except ValueError:
                raise ValueError(tr('{} is not a number: {}').format(tr(parameter.label), text))
            if value != postprocess.PARAMETER_DEFAULTS[parameter.key]:
                args += ['--set', '{}={}'.format(parameter.key, value)]
        return args

    def run(self):
        if self.running:
            return
        if not os.path.isfile(self.midi.get().strip()):
            messagebox.showerror(tr('Piano MIDI Cleanup'), tr('Choose a MIDI file first.'))
            return
        preset = self.current_preset()
        try:
            argv = self.argv(preset)
        except ValueError as error:
            messagebox.showerror(tr('Piano MIDI Cleanup'), str(error))
            return
        self.settings.update(midi=self.midi.get(), source=self.source.get(), bpm=self.bpm.get(),
                             audio=self.audio.get(), last=preset)
        save_settings(self.settings)
        self.roll.stop()
        self.running = True
        self.run_button.configure(state='disabled')
        self.status_fragment = ''
        self.status.set(tr('Processing...'))
        self.report_text = 'postprocess.py {}\n\n'.format(' '.join('"{}"'.format(arg) if ' ' in arg else arg for arg in argv))
        self.render_report()
        threading.Thread(target=self.work, args=(argv,), daemon=True).start()

    def work(self, argv):
        writer = QueueWriter(self.lines)
        code = 1
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            try:
                code = postprocess.main(argv)
            except SystemExit as error:
                code = error.code
            except Exception as error:
                print('Failed: {}'.format(error))
        self.lines.put(('done', code))

    def drain(self):
        report_changed = False
        while True:
            try:
                item = self.lines.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, tuple):
                self.running = False
                self.run_button.configure(state='normal')
                message = tr('Finished.') if item[1] == 0 else tr('Stopped (exit {}).').format(item[1])
                if item[1] == 0:
                    self.load_existing_report(update_status=False)
                    report_changed = False
                else:
                    self.report_text += '\n' + message + '\n'
                    report_changed = True
                self.status.set(message)
                self.status_fragment = ''
            else:
                self.report_text += item
                report_changed = True
                lines = (self.status_fragment + item.replace('\r\n', '\n').replace('\r', '\n')).split('\n')
                self.status_fragment = lines[-1]
                latest = next((line.strip() for line in reversed(lines) if line.strip()), None)
                if latest:
                    self.status.set(latest)
        if report_changed:
            self.render_report()
            self.report_tree.yview_moveto(1)
        self.root.after(100, self.drain)


def main():
    root = tk.Tk()
    root.geometry('1280x720')
    Panel(root)
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
