# Run record: <song> (<source>+<model>-<version>)

- Request: <the user's sentence>
- Input: <audio file, kind: full mix | solo piano | existing MIDI>
- Tool commit / version: <hash> / <version>
- Commands run: <audio_to_midi.py ... ; postprocess.py ...>
- Reports read: BPM <value, drift, coherence> | meter <value or undetermined> | first beat <s, bass share> | cleaning removed <n (pct)> | glissandi <n>
- Settings changed: <KEY=VALUE, symptom seen, expected effect, actual effect> (none if defaults)
- Delivered: <path of final MIDI>
- Human-check items: <bar numbers and reason>
- Unresolved / unverified: <points>
