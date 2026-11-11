"""The meter of a MIDI, from its time signature.

Every step counts bars in quarter-note beats of the MIDI. A 6/8 bar is three quarter beats
(six eighths), a 3/4 bar three, a 4/4 bar four. A MIDI without a time signature is 4/4.
"""
import mido

METERS = ('4/4', '3/4', '6/8')


def parse_meter(text):
    """'6/8' -> (6, 8)."""
    numerator, _, denominator = str(text).partition('/')
    try:
        numerator, denominator = int(numerator), int(denominator)
    except ValueError:
        raise ValueError('Time signature must look like 4/4, 3/4 or 6/8: {}'.format(text))
    if '{}/{}'.format(numerator, denominator) not in METERS:
        raise ValueError('Time signature must be one of {}: {}'.format(', '.join(METERS), text))
    return numerator, denominator


def midi_meter(midi_file):
    """(numerator, denominator) of the earliest time signature, (4, 4) when there is none."""
    found = None
    for track in midi_file.tracks:
        tick = 0
        for message in track:
            tick += message.time
            if message.type == 'time_signature':
                if found is None or tick < found[0]:
                    found = (tick, message.numerator, message.denominator)
                break
    return (found[1], found[2]) if found else (4, 4)


def bar_beats(midi_file):
    """Quarter-note beats in one bar of midi_file (an int for every meter in METERS)."""
    numerator, denominator = midi_meter(midi_file)
    beats = numerator * 4 / denominator
    return int(beats) if beats == int(beats) else beats


def is_compound(numerator, denominator):
    """6/8 and the like: the felt beat is a dotted quarter split into three eighths."""
    return denominator == 8 and numerator % 3 == 0


def set_meter(midi_path, numerator, denominator):
    """Replace every time signature of midi_path with one at tick 0 of the first track."""
    midi_file = mido.MidiFile(midi_path)
    for track in midi_file.tracks:
        carry = 0
        kept = []
        for message in track:
            if message.type == 'time_signature':
                carry += message.time
                continue
            kept.append(message.copy(time=message.time + carry))
            carry = 0
        track[:] = kept
    midi_file.tracks[0].insert(0, mido.MetaMessage('time_signature', numerator=numerator, denominator=denominator,
                                                   clocks_per_click=24 if denominator == 4 else 36,
                                                   notated_32nd_notes_per_beat=8, time=0))
    midi_file.save(midi_path)
