# sfizz (Steinway output)

`native/libsfizz.dylib` is sfizz 1.2.3, built as a universal macOS binary
(x86_64 and arm64). sfizz is BSD-2-Clause; see `LICENSE`. The dylib contains
bundled third-party code; keep sfizz's license and notices with it.

`src/sfizz_output.py` loads an SFZ through this library and sends stereo float
PCM to the operating system's default output with sounddevice. It is the
"Steinway Grand Piano" playback output in the app. The samples are not part of
this repository; they stay wherever the user's SFZ points. Do not redistribute
Logic or MuseScore sample content without checking its license.

## SFZ location

`src/sfizz_output.py` looks for the instrument in this order:

1. the path in `PIANO_MIDI_CLEANUP_SFZ`
2. `~/Documents/MuseScore3Evo/SoundFonts/Steinway Grand Piano.sfz`
3. `~/Documents/MuseScore3Evo/SoundFonts/Steinway Grand Piano/Steinway Grand Piano.sfz`
4. `sound_sources/sfizz/Steinway Grand Piano.sfz`
5. `sound_sources/sfizz/Steinway Grand Piano/Steinway Grand Piano.sfz`

The converter that produced the current SFZ wrote `default_path=SteinwayLogic/`
while storing samples in a sibling folder named after the SFZ. sfizz resolves
`default_path` strictly, so the loader repairs that one case in memory without
editing the SFZ. A correct `default_path` is used as-is.

## Rebuild

sfizz is built from <https://github.com/sfztools/sfizz>. The vendored dylib was
built with:

```bash
git clone --recursive https://github.com/sfztools/sfizz.git
cmake -S sfizz -B build -DCMAKE_BUILD_TYPE=Release \
  -DSFIZZ_SHARED=ON -DSFIZZ_RENDER=OFF -DSFIZZ_JACK=OFF \
  -DSFIZZ_TESTS=OFF -DSFIZZ_DEMOS=OFF -DSFIZZ_BENCHMARKS=OFF \
  -DCMAKE_DISABLE_FIND_PACKAGE_Qt5=ON \
  -DCMAKE_OSX_ARCHITECTURES="arm64;x86_64" \
  -DCMAKE_CXX_FLAGS="-Wno-missing-template-arg-list-after-template-kw -Wno-error"
cmake --build build -j8
cp build/library/lib/libsfizz.1.2.3.dylib sound_sources/sfizz/native/libsfizz.dylib
```

The app loads the dylib with ctypes; no separate sfizz executable or plugin is
needed.
