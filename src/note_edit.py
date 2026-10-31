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


def edited_midi(midi_path, deleted=(), channels=None):
    """The MIDI with the notes at these paired_notes indices deleted and others moved, as {index: channel}."""
    midi = mido.MidiFile(midi_path)
    notes = list(paired_notes(midi))
    actions = defaultdict(dict)  # track -> {message index: None to delete, or the new channel}
    for index, channel in (channels or {}).items():
        note = notes[index]
        actions[note.track].update({note.on_index: channel, note.off_index: channel})
    # Deletion wins over an earlier move of the same note.
    for index in deleted:
        note = notes[index]
        actions[note.track].update({note.on_index: None, note.off_index: None})
    for track_index, track_actions in actions.items():
        edited = mido.MidiTrack()
        carry = 0
        for index, message in enumerate(midi.tracks[track_index]):
            action = track_actions.get(index, message)
            if action is None:
                # Keep later events at their times by handing the delta to the next message.
                carry += message.time
                continue
            message = message.copy(time=message.time + carry)
            carry = 0
            if index in track_actions:
                message = message.copy(channel=action)
            edited.append(message)
        midi.tracks[track_index] = edited
    return midi


def edit_notes(midi_path, deleted=(), channels=None):
    """Write edited_midi back over midi_path."""
    edited_midi(midi_path, deleted, channels).save(midi_path)
