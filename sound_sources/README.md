# Coopiano

`coopiano/` is the selected piano synthesis source. Local audition renders such as `coopiano/Coopiano-preview.wav` are generated assets and are not included in the repository.

## Redistribution

Coopiano is licensed under MIT. When distributing its source or binaries, include `coopiano/LICENSE` with the package or in its third-party notices. Keep Peter Cooper's copyright and the MIT permission notice. The license permits commercial redistribution and modification; it disclaims warranty.

The upstream project uses C++17 and threads and needs no piano samples. `coopiano/playback_bridge.cpp` exposes the existing engine to the app; `src/coopiano_output.py` sends its stereo PCM to the operating system's default audio output using sounddevice. This path does not require a VST host or virtual MIDI cable.

The Windows x64 runtime is included at `coopiano/native/coopiano.dll`, built with the static MSVC runtime. Rebuild it with `windows/build_coopiano.cmd` when the engine, preset parser or bridge changes. Other platforms build their native library with `make playback` in `coopiano/`; those binaries are not included. Install Python dependencies from the project's `requirements.txt`.

Coopiano uses the calibrated `coopiano/default.ini` and the OS default stereo output device at its default rate. The existing host adapter resamples the 44.1 kHz engine to supported device rates (44.1, 48, 88.2 or 96 kHz). Under RDP, the session must have an available audio playback device. Output errors are displayed in the app; it does not silently switch to another synth.

The background worker initializes the engine and prepares each incoming note using the engine's existing strike preparation API. It does not precompute every key/velocity combination at startup. Prepared strikes cross a bounded single-producer/single-consumer queue to the audio callback; allocation, contact solving and strike destruction stay off the callback. MIDI timing, hand muting and sustain use the same scheduler as external MIDI outputs. The callback is block-based (1024 frames); note preparation can add latency, and this integration does not claim sample-accurate MIDI delivery or guaranteed real-time performance on every machine. Actual GUI/RDP playback has not been verified for this integration.
