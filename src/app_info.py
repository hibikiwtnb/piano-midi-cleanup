"""Application identity shared by the UI and future release packaging."""

APP_NAME = 'Piano MIDI Cleanup'
VERSION = '1.0.1'
LICENSE_ID = 'AGPL-3.0-only'
PROJECT_URL = 'https://github.com/hibikiwtnb/piano-midi-cleanup'
ISSUES_URL = PROJECT_URL + '/issues'

# Source and license files distributed in this repository.
BUNDLED_COMPONENTS = (
    ('Coopiano', 'Built-in piano sound', 'MIT', 'https://github.com/peterc/coopiano',
     'sound_sources/coopiano/LICENSE', 'Copyright (c) 2026 Peter Cooper'),
    ('Tonal.js', 'Chord analysis', 'MIT', 'https://github.com/tonaljs/tonal',
     'vendor/TONAL_LICENSE.txt', 'Copyright (c) 2015 danigb'),
    ('note-seq', 'Chord inference (modified in src/chord_inference.py)', 'Apache-2.0',
     'https://github.com/magenta/note-seq', 'vendor/NOTE_SEQ_LICENSE.txt', 'Copyright 2024 The Magenta Authors'),
    ('Essentia.js', 'Automatic tempo detection on Windows', 'AGPL-3.0',
     'https://github.com/MTG/essentia.js', 'vendor/ESSENTIA_LICENSE.txt', 'Music Technology Group, UPF'),
)
