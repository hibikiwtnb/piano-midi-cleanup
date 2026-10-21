import argparse
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

from bpmfix_utils import apply_bpm_fix, bpmfix_report_path_for
from first_beat_ui import append_first_beat_stats, auto_align_first_beat, run_first_beat_ui


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


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description='Fix MIDI BPM, align the first beat, then optionally open the workbench. '
                    'Without input_midi the tool asks with dialogs.',
    )
    parser.add_argument('input_midi', nargs='?', help='MIDI to fix; writes *_bpmfix.mid next to it.')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--bpm', type=float, help='Known BPM.')
    source.add_argument('--audio', help='Source audio for Essentia BPM detection.')
    parser.add_argument('--no-auto-first-beat', action='store_true', help='Skip the automatic first beat alignment.')
    parser.add_argument('--workbench', action='store_true', help='Open the browser workbench after fixing.')
    return parser.parse_args(argv)


def run_non_interactive(args):
    if not os.path.isfile(args.input_midi):
        print('MIDI file does not exist: {}'.format(args.input_midi))
        return 1
    if args.bpm is None and args.audio is None:
        print('Give --bpm or --audio.')
        return 1
    if args.audio is not None and not os.path.isfile(args.audio):
        print('Audio file does not exist: {}'.format(args.audio))
        return 1

    report_path = bpmfix_report_path_for(args.input_midi)
    output_path, _ = apply_bpm_fix(args.audio, args.input_midi, target_bpm=args.bpm)
    if not args.no_auto_first_beat:
        auto_align_first_beat(output_path, report_path=report_path)
    if args.workbench:
        append_first_beat_stats(output_path, run_first_beat_ui(output_path), report_path=report_path)
    print('Output: {}'.format(output_path))
    return 0


def main(argv=None):
    args = parse_args(argv)
    if args.input_midi:
        return run_non_interactive(args)

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

        report_path = bpmfix_report_path_for(midi_path)
        output_path, bpm_info = apply_bpm_fix(audio_path, midi_path, target_bpm=target_bpm)
        auto_align_first_beat(output_path, report_path=report_path)
        first_beat = run_first_beat_ui(output_path)
        append_first_beat_stats(output_path, first_beat, report_path=report_path)
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
