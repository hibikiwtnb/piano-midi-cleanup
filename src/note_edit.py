"""Delete notes or move them to another hand's channel in a MIDI file, for the piano roll's editing."""

from collections import defaultdict, namedtuple

import mido

PairedNote = namedtuple('PairedNote', 'track on_index off_index start_tick end_tick pitch channel velocity')


def paired_notes(midi):
    """Notes in the roll's order: per track, as each note ends; repeated pitches pair last-in, first-out."""
    for track_index, track in enumerate(midi.tracks):
        active = defaultdict(list)
        tick = 0
        for index, message in enumerate(track):
            tick += message.time
            if message.type == 'note_on' and message.velocity > 0:
                active[message.channel, message.note].append((tick, message.velocity, index))
            elif message.type in ('note_on', 'note_off'):
                starts = active[message.channel, message.note]
                if starts:
                    # Match the post-processing note pairing, including repeated pitches.
                    start, velocity, on_index = starts.pop()
                    yield PairedNote(track_index, on_index, index, start, tick, message.note, message.channel,
                                     velocity)


def edit_notes(midi_path, indices, channel=None):
    """Delete the notes at these paired_notes indices, or move them to channel when one is given."""
    midi = mido.MidiFile(midi_path)
    notes = list(paired_notes(midi))
    touched = defaultdict(set)
    for index in indices:
        note = notes[index]
        touched[note.track].update((note.on_index, note.off_index))
    for track_index, message_indices in touched.items():
        edited = mido.MidiTrack()
        carry = 0
        for index, message in enumerate(midi.tracks[track_index]):
            if index in message_indices and channel is None:
                # Keep later events at their times by handing the delta to the next message.
                carry += message.time
                continue
            message = message.copy(time=message.time + carry)
            carry = 0
            if index in message_indices:
                message = message.copy(channel=channel)
            edited.append(message)
        midi.tracks[track_index] = edited
    midi.save(midi_path)
