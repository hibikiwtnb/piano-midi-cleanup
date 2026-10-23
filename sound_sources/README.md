# CoPiano

`coopiano/` is the selected piano synthesis source. Local audition renders such as `coopiano/CoPiano-preview.wav` are generated assets and are not included in the repository.

## Redistribution

CoPiano is licensed under MIT. When distributing its source or binaries, include `coopiano/LICENSE` with the package or in its third-party notices. Keep Peter Cooper's copyright and the MIT permission notice. The license permits commercial redistribution and modification; it disclaims warranty.

The upstream project uses C++17 and threads and states that the synth needs no third-party libraries or piano samples. Its live audio/MIDI host is macOS-specific; Windows has only been verified here for offline WAV rendering. The app still needs a Windows live-audio integration before it can use this engine directly for playback.
