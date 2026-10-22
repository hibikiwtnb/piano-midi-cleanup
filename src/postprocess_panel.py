import contextlib
import json
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

import postprocess
from trim_overlaps import DEFAULT_FAST_FIGURE_HANDS


# Last choices and saved presets, per user.
SETTINGS_PATH = os.path.join(os.path.expanduser('~'), '.piano-midi-cleanup-panel.json')
PARAMETER_TABS = (
    ('key', 'Key signatures'),
    ('clean', 'Rule cleaning'),
    ('playable', 'Playable'),
    ('split', 'Hand split'),
    ('trim', 'Overlap trim'),
)
HAND_ORDER = ('right', 'second_right', 'second_left', 'left')
# Step switches and their defaults; presets store these and the thresholds, not the files.
SWITCHES = (
    ('first_beat', 'Auto first beat (after BPMFix)', True),
    ('key_signature', 'Key signatures (after BPMFix)', True),
    ('clean', 'Rule cleaning', True),
    ('semitone_pairs', 'Semitone pairs (in rule cleaning)', False),
    ('playable', 'Playable reduction (one player, 2 hands)', False),
    ('split', 'Hand split', True),
    ('trim', 'Overlap trim', True),
    ('discard_cleaned_edits', 'Rebuild even if the first beat was set by hand on the cleaned MIDI', False),
)


def default_preset():
    return {
        'switches': {name: default for name, _, default in SWITCHES},
        'hands': 2,
        'fast_figure_hands': list(DEFAULT_FAST_FIGURE_HANDS),
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
        self.lines = queue.Queue()
        self.running = False
        root.title('Piano MIDI Cleanup')

        self.midi = tk.StringVar(value=self.settings.get('midi', ''))
        self.source = tk.StringVar(value=self.settings.get('source', 'reuse'))
        self.bpm = tk.StringVar(value=self.settings.get('bpm', ''))
        self.audio = tk.StringVar(value=self.settings.get('audio', ''))
        self.switches = {name: tk.BooleanVar() for name, _, _ in SWITCHES}
        self.hands = tk.StringVar()
        self.fast_figure = {hand: tk.BooleanVar() for hand in HAND_ORDER}
        self.snapshot = tk.StringVar()
        self.parameters = {parameter.key: tk.StringVar() for parameter in postprocess.PARAMETERS}
        self.preset_name = tk.StringVar()

        self.build_files(ttk.Frame(root, padding=8))
        body = ttk.Frame(root, padding=(8, 0))
        body.pack(fill='both', expand=False)
        self.build_steps(ttk.LabelFrame(body, text='Steps', padding=8))
        self.build_parameters(ttk.LabelFrame(body, text='Thresholds', padding=8))
        self.build_run(ttk.Frame(root, padding=8))
        self.show_preset(self.settings.get('last') or default_preset())
        self.update_source()
        root.after(100, self.drain)

    def build_files(self, frame):
        frame.pack(fill='x')
        ttk.Label(frame, text='MIDI').grid(row=0, column=0, sticky='w')
        ttk.Entry(frame, textvariable=self.midi, width=70).grid(row=0, column=1, columnspan=3, sticky='we')
        ttk.Button(frame, text='Choose...', command=self.choose_midi).grid(row=0, column=4)
        ttk.Radiobutton(frame, text='Reuse song_bpmfix.mid', variable=self.source, value='reuse',
                        command=self.update_source).grid(row=1, column=1, sticky='w')
        ttk.Radiobutton(frame, text='Known BPM', variable=self.source, value='bpm',
                        command=self.update_source).grid(row=2, column=1, sticky='w')
        self.bpm_entry = ttk.Entry(frame, textvariable=self.bpm, width=10)
        self.bpm_entry.grid(row=2, column=2, sticky='w')
        ttk.Radiobutton(frame, text='Detect from audio', variable=self.source, value='audio',
                        command=self.update_source).grid(row=3, column=1, sticky='w')
        self.audio_entry = ttk.Entry(frame, textvariable=self.audio, width=50)
        self.audio_entry.grid(row=3, column=2, columnspan=2, sticky='we')
        self.audio_button = ttk.Button(frame, text='Choose...', command=self.choose_audio)
        self.audio_button.grid(row=3, column=4)
        frame.columnconfigure(3, weight=1)

    def build_steps(self, frame):
        frame.pack(side='left', fill='y', padx=(0, 8))
        self.step_checks = {}
        for row, (name, label, _) in enumerate(SWITCHES):
            check = ttk.Checkbutton(frame, text=label, variable=self.switches[name])
            check.grid(row=row, column=0, columnspan=2, sticky='w')
            self.step_checks[name] = check
        row = len(SWITCHES)
        ttk.Label(frame, text='Hands').grid(row=row, column=0, sticky='w', pady=(8, 0))
        ttk.Combobox(frame, textvariable=self.hands, values=('2', '3', '4'), width=4,
                     state='readonly').grid(row=row, column=1, sticky='w', pady=(8, 0))
        ttk.Label(frame, text='Fast figure hands').grid(row=row + 1, column=0, sticky='nw')
        hands = ttk.Frame(frame)
        hands.grid(row=row + 1, column=1, sticky='w')
        for hand in HAND_ORDER:
            ttk.Checkbutton(hands, text=hand.replace('_', ' '), variable=self.fast_figure[hand]).pack(anchor='w')
        ttk.Label(frame, text='Snapshots').grid(row=row + 2, column=0, sticky='w')
        ttk.Combobox(frame, textvariable=self.snapshot, values=('', 'landscape', 'portrait', 'both'), width=10,
                     state='readonly').grid(row=row + 2, column=1, sticky='w')

    def build_parameters(self, frame):
        frame.pack(side='left', fill='both', expand=True)
        tabs = ttk.Notebook(frame)
        tabs.pack(fill='both', expand=True)
        for prefix, title in PARAMETER_TABS:
            tab = ttk.Frame(tabs, padding=8)
            tabs.add(tab, text=title)
            row = 0
            for parameter in postprocess.PARAMETERS:
                if not parameter.key.startswith(prefix + '.'):
                    continue
                ttk.Label(tab, text=parameter.label).grid(row=row, column=0, sticky='w')
                ttk.Entry(tab, textvariable=self.parameters[parameter.key], width=8).grid(row=row, column=1, padx=4)
                ttk.Label(tab, text='default {}'.format(postprocess.PARAMETER_DEFAULTS[parameter.key]),
                          foreground='gray').grid(row=row, column=2, sticky='w')
                row += 1
        presets = ttk.Frame(frame)
        presets.pack(fill='x', pady=(8, 0))
        ttk.Label(presets, text='Preset').pack(side='left')
        self.preset_box = ttk.Combobox(presets, textvariable=self.preset_name, width=16, state='readonly')
        self.preset_box.pack(side='left', padx=4)
        self.preset_box.bind('<<ComboboxSelected>>', lambda _: self.load_preset())
        ttk.Button(presets, text='Save as...', command=self.save_preset).pack(side='left')
        ttk.Button(presets, text='Delete', command=self.delete_preset).pack(side='left')
        ttk.Button(presets, text='Defaults', command=lambda: self.show_preset(default_preset())).pack(side='right')
        self.refresh_presets()

    def build_run(self, frame):
        frame.pack(fill='both', expand=True)
        self.run_button = ttk.Button(frame, text='Run', command=self.run)
        self.run_button.pack(anchor='w')
        self.log = ScrolledText(frame, height=18, width=110)
        self.log.pack(fill='both', expand=True, pady=(8, 0))

    def choose_midi(self):
        path = filedialog.askopenfilename(title='Choose song.mid, song_bpmfix.mid or song_bpmfix_cleaned.mid',
                                          filetypes=[('MIDI files', '*.mid *.midi'), ('All files', '*.*')])
        if path:
            self.midi.set(path)

    def choose_audio(self):
        path = filedialog.askopenfilename(title='Choose source audio for BPM detection',
                                          filetypes=[('Audio files', '*.flac *.mp3 *.wav *.m4a *.aif *.aiff'),
                                                     ('All files', '*.*')])
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
            'snapshot': self.snapshot.get(),
            'parameters': {key: variable.get().strip() for key, variable in self.parameters.items()},
        }

    def show_preset(self, preset):
        preset = dict(default_preset(), **preset)
        for name, variable in self.switches.items():
            variable.set(preset['switches'].get(name, dict((n, d) for n, _, d in SWITCHES)[name]))
        self.hands.set(str(preset['hands']))
        for hand, variable in self.fast_figure.items():
            variable.set(hand in preset['fast_figure_hands'])
        self.snapshot.set(preset['snapshot'])
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
        name = simpledialog.askstring('Save preset', 'Preset name', initialvalue=self.preset_name.get(), parent=self.root)
        if not name:
            return
        self.settings.setdefault('presets', {})[name] = self.current_preset()
        save_settings(self.settings)
        self.refresh_presets()
        self.preset_name.set(name)

    def delete_preset(self):
        name = self.preset_name.get()
        if name in self.settings.get('presets', {}) and messagebox.askyesno('Delete preset', 'Delete "{}"?'.format(name)):
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
        for name, flag in (('semitone_pairs', '--semitone-pairs'), ('playable', '--playable'),
                           ('discard_cleaned_edits', '--discard-cleaned-edits')):
            if switches[name]:
                args.append(flag)
        args += ['--hands', str(preset['hands'])]
        if switches['trim']:
            if not preset['fast_figure_hands']:
                raise ValueError('Choose at least one fast figure hand, or turn overlap trim off.')
            args += ['--fast-figure-hands'] + preset['fast_figure_hands']
        if preset['snapshot']:
            args += ['--snapshot', preset['snapshot']]
        for parameter in postprocess.PARAMETERS:
            text = preset['parameters'][parameter.key]
            try:
                value = parameter.kind(text)
            except ValueError:
                raise ValueError('{} is not a number: {}'.format(parameter.label, text))
            if value != postprocess.PARAMETER_DEFAULTS[parameter.key]:
                args += ['--set', '{}={}'.format(parameter.key, value)]
        return args

    def run(self):
        if self.running:
            return
        if not os.path.isfile(self.midi.get().strip()):
            messagebox.showerror('Piano MIDI Cleanup', 'Choose a MIDI file first.')
            return
        preset = self.current_preset()
        try:
            argv = self.argv(preset)
        except ValueError as error:
            messagebox.showerror('Piano MIDI Cleanup', str(error))
            return
        self.settings.update(midi=self.midi.get(), source=self.source.get(), bpm=self.bpm.get(),
                             audio=self.audio.get(), last=preset)
        save_settings(self.settings)
        self.running = True
        self.run_button.configure(state='disabled')
        self.log.delete('1.0', 'end')
        self.lines.put('postprocess.py {}\n\n'.format(' '.join('"{}"'.format(arg) if ' ' in arg else arg for arg in argv)))
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
        while True:
            try:
                item = self.lines.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, tuple):
                self.running = False
                self.run_button.configure(state='normal')
                self.log.insert('end', '\nFinished.\n' if item[1] == 0 else '\nStopped (exit {}).\n'.format(item[1]))
            else:
                self.log.insert('end', item)
            self.log.see('end')
        self.root.after(100, self.drain)


def main():
    root = tk.Tk()
    Panel(root)
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
