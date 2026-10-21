import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

from bpmfix_utils import apply_bpm_fix, bpmfix_report_path_for
from first_beat_ui import append_first_beat_stats, run_first_beat_ui


def choose_audio():
    root = tk.Tk()
    root.withdraw()
    return filedialog.askopenfilename(
        title='Choose source audio for BPM detection',
        filetypes=[
            ('Audio files', '*.flac *.mp3 *.wav *.ape *.m4a *.aif *.aiff'),
            ('All files', '*.*'),
        ],
    )


def choose_midi():
    root = tk.Tk()
    root.withdraw()
    return filedialog.askopenfilename(
        title='Choose MIDI to fix',
        filetypes=[
            ('MIDI files', '*.mid *.midi'),
            ('All files', '*.*'),
        ],
    )


def ask_known_bpm():
    raw_value = input('Known BPM? Type a BPM value, or press Return to auto-detect: ').strip()
    if not raw_value:
        return None

    try:
        bpm = float(raw_value)
    except ValueError:
        raise ValueError('BPM must be a number.')

    if bpm <= 0:
        raise ValueError('BPM must be greater than zero.')

    return bpm


def main():
    print('--------------------------------------------------------------------------------')
    print('MIDI BPMFix using Essentia BPM detection, then BPM and first beat in the browser')
    print('Enter a known BPM first. If unknown, press Return and choose audio for detection.')
    print('--------------------------------------------------------------------------------')

    try:
        target_bpm = ask_known_bpm()
        audio_path = None
        if target_bpm is None:
            audio_path = choose_audio()
            if not audio_path:
                print('No audio selected.')
                return 1
            if not os.path.isfile(audio_path):
                messagebox.showerror('BPMFix', 'Audio file does not exist.')
                return 1

        midi_path = choose_midi()
        if not midi_path:
            print('No MIDI selected.')
            return 1
        if not os.path.isfile(midi_path):
            messagebox.showerror('BPMFix', 'MIDI file does not exist.')
            return 1

        output_path, bpm_info = apply_bpm_fix(audio_path, midi_path, target_bpm=target_bpm)
        first_beat = run_first_beat_ui(output_path)
        append_first_beat_stats(output_path, first_beat, report_path=bpmfix_report_path_for(midi_path))
    except Exception as error:
        messagebox.showerror('BPMFix failed', str(error))
        raise

    confidence = bpm_info.get('confidence')
    messagebox.showinfo(
        'BPMFix complete',
        'BPM: {bpm:.2f}\nSource: {source}\nConfidence: {confidence}\nFirst beat: {first_beat}\nOutput:\n{output}'.format(
            output=output_path,
            confidence='N/A' if confidence is None else '{:.3f}'.format(confidence),
            first_beat='not changed' if first_beat is None else '{:.3f}s'.format(first_beat['first_beat_seconds']),
            bpm=bpm_info['bpm'] if first_beat is None else first_beat['bpm'],
            source=bpm_info['source'],
        ),
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
