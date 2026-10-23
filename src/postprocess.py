import argparse
import os
import shutil
import sys
from collections import namedtuple

import clean_midi_rules
import hand_split_channels
import key_signature
import playable_reduction
import trim_overlaps
from bpmfix_utils import apply_bpm_fix, bpmfix_path_for
from clean_midi_rules import apply_rule_cleaning, cleaned_path_for
from first_beat import auto_align_first_beat
from hand_split_channels import CHANNELS, apply_hand_split
from key_signature import apply_key_signatures
from midi_stats import stats_path_for, write_midi_stats
from pianoroll_snapshot import write_snapshots
from playable_reduction import apply_playable_reduction
from trim_overlaps import DEFAULT_FAST_FIGURE_HANDS, apply_overlap_trim
from report_utils import report_blocks, update_report
from note_changes import note_snapshot, save_changes, stage_changes


# Report sections that belong to song_bpmfix.mid; they are kept when it is reused, everything after is rebuilt.
BPMFIX_SECTIONS = (
    'MIDI quality stats',
    'MIDI BPMFix stats',
    'MIDI auto first beat stats',
    'MIDI first beat stats',
    'MIDI key signature stats',
)
Parameter = namedtuple('Parameter', 'key label kind targets')
# Thresholds a run may override with --set key=value; each one sets these module constants.
PARAMETERS = (
    Parameter('key.change_cost', 'Accidentals a key change must save', int, ((key_signature, 'CHANGE_COST'),)),
    Parameter('key.crowded_ratio', 'Accidental share that checks a phrase bar by bar', float,
              ((key_signature, 'CROWDED_RATIO'),)),
    Parameter('clean.min_seconds', 'Remove notes shorter than (s)', float,
              ((clean_midi_rules, 'MIN_NOTE_DURATION_SECONDS'),)),
    Parameter('clean.min_velocity', 'Remove velocity below', int, ((clean_midi_rules, 'MIN_NOTE_VELOCITY'),)),
    Parameter('clean.soft_velocity', 'Soft note: velocity below', int,
              ((clean_midi_rules, 'LOW_VELOCITY_AND_SHORT_VELOCITY'),)),
    Parameter('clean.soft_beats', 'Soft note: length up to (beats)', float,
              ((clean_midi_rules, 'LOW_VELOCITY_AND_SHORT_BEATS'),)),
    Parameter('clean.semitone_window', 'Semitone pair: onsets within (s)', float,
              ((clean_midi_rules, 'SEMITONE_PAIR_WINDOW_SECONDS'),)),
    Parameter('playable.max_notes', 'Most notes per hand', int, ((playable_reduction, 'DEFAULT_MAX_NOTES_PER_HAND'),)),
    Parameter('playable.max_span', 'Widest hand span (semitones)', int, ((playable_reduction, 'DEFAULT_MAX_SPAN'),)),
    Parameter('playable.max_removals', 'Most notes removed per onset group', int,
              ((playable_reduction, 'MAX_REMOVALS_PER_GROUP'),)),
    Parameter('split.group_window', 'Onset group window, all steps (s)', float,
              ((hand_split_channels, 'GROUP_START_WINDOW_SECONDS'),)),
    Parameter('split.comfort_span', 'Free hand span (semitones)', int, ((hand_split_channels, 'COMFORT_SPAN'),)),
    Parameter('split.hard_span', 'Heavily penalized beyond (semitones)', int, ((hand_split_channels, 'HARD_SPAN'),)),
    Parameter('trim.fast_figure_beats', 'Fast figure: onsets within (beats)', float,
              ((trim_overlaps, 'FAST_FIGURE_BEATS'),)),
    Parameter('trim.fast_figure_onsets', 'Fast figure: at least onset groups', int,
              ((trim_overlaps, 'FAST_FIGURE_MIN_ONSETS'),)),
    Parameter('trim.harmony_pitch_classes', 'Harmony change: at least pitch classes', int,
              ((trim_overlaps, 'HARMONY_MIN_PITCH_CLASSES'),)),
    Parameter('trim.outer_step', 'Outer line: step up to (semitones)', int, ((trim_overlaps, 'OUTER_STEP_SEMITONES'),)),
    Parameter('trim.max_fingers', 'Out of reach: more notes than', int, ((trim_overlaps, 'MAX_FINGERS'),)),
    Parameter('trim.max_span', 'Out of reach: span over (semitones)', int, ((trim_overlaps, 'HARD_SPAN'),)),
)
PARAMETER_DEFAULTS = {parameter.key: getattr(*parameter.targets[0]) for parameter in PARAMETERS}
PARAMETER_BY_KEY = {parameter.key: parameter for parameter in PARAMETERS}
SNAPSHOT_LAYOUTS = {
    'landscape': ('landscape',),
    'portrait': ('portrait',),
}


def parse_setting(text):
    """key=value from --set into (key, typed value)."""
    key, separator, value = text.partition('=')
    if not separator or key not in PARAMETER_BY_KEY:
        raise argparse.ArgumentTypeError('expected key=value with a key from --list-parameters: {}'.format(text))
    try:
        return key, PARAMETER_BY_KEY[key].kind(value)
    except ValueError:
        raise argparse.ArgumentTypeError('not a number: {}'.format(text))


def apply_parameters(settings):
    """Set every threshold to its override or default, so earlier runs in the same process leave nothing behind."""
    values = dict(PARAMETER_DEFAULTS, **dict(settings))
    for parameter in PARAMETERS:
        for module, name in parameter.targets:
            setattr(module, name, values[parameter.key])
    return {key: value for key, value in values.items() if value != PARAMETER_DEFAULTS[key]}


def source_midi_path(midi_path):
    """song.mid from song.mid, song_bpmfix.mid or song_bpmfix_cleaned.mid."""
    filename, ext = os.path.splitext(midi_path)
    for suffix in ('_cleaned', '_bpmfix'):
        if filename.endswith(suffix):
            filename = filename[:-len(suffix)]
    return filename + ext


def keep_bpmfix_sections(stats_path):
    """Drop the report sections of earlier runs that came after song_bpmfix.mid was made."""
    if not os.path.exists(stats_path):
        return
    with open(stats_path, encoding='utf-8') as file:
        sections = report_blocks(file.read())
    kept = [
        '\n'.join([title] + lines) for title, lines in sections.items()
        if title in BPMFIX_SECTIONS
        and not any(line.startswith('MIDI: ') and '_cleaned.mid' in line for line in lines)
    ]
    update_report(stats_path, '\n\n\n'.join(kept), replace=True)


def plan_steps(args):
    """The steps in order, as (name, description); raises ValueError on a combination that cannot run."""
    if args.playable and args.hands != 2:
        raise ValueError('Playable reduction is for one player; it needs --hands 2.')
    if args.trim and not args.split:
        raise ValueError('Overlap trimming works per hand; it needs the hand split.')
    steps = []
    if args.bpm or args.audio:
        steps.append(('bpmfix', 'BPMFix ({})'.format('{} BPM'.format(args.bpm) if args.bpm else args.audio)))
        if args.first_beat:
            steps.append(('first_beat', 'Auto first beat'))
        if args.key_signature:
            steps.append(('key_signature', 'Key signatures'))
    else:
        steps.append(('reuse_bpmfix', 'Reuse the existing BPM-fixed MIDI'))
    steps.append(('clean', 'Rule cleaning{}'.format(' with semitone pairs' if args.semitone_pairs else ''))
                 if args.clean else ('copy', 'No cleaning'))
    if args.playable:
        steps.append(('playable', 'Playable reduction'))
    if args.split:
        steps.append(('split', 'Hand split ({} hands)'.format(args.hands)))
    if args.trim:
        steps.append(('trim', 'Overlap trim (fast figure: {})'.format(', '.join(args.fast_figure_hands))))
    if args.snapshot:
        steps.append(('snapshot', 'Piano roll snapshots ({})'.format(args.snapshot)))
    return steps


def run_postprocess(args):
    source_path = source_midi_path(args.input_midi)
    bpmfix_path = bpmfix_path_for(source_path)
    cleaned_path = cleaned_path_for(bpmfix_path)
    stats_path = stats_path_for(source_path)
    steps = plan_steps(args)
    names = [name for name, _ in steps]
    overrides = apply_parameters(args.settings)

    if 'bpmfix' in names:
        if not os.path.isfile(source_path):
            raise ValueError('Source MIDI does not exist: {}'.format(source_path))
        write_midi_stats(source_path, reset_report=True)
    else:
        if not os.path.isfile(bpmfix_path):
            raise ValueError('No BPM-fixed MIDI to reuse; give --bpm or --audio: {}'.format(bpmfix_path))
        keep_bpmfix_sections(stats_path)

    changes = []
    for name in names:
        before = None
        if name in ('clean', 'playable', 'trim'):
            before = note_snapshot(bpmfix_path if name == 'clean' else cleaned_path)
        if name == 'bpmfix':
            apply_bpm_fix(args.audio, source_path, report_path=stats_path, append_report=True, target_bpm=args.bpm)
        elif name == 'first_beat':
            auto_align_first_beat(bpmfix_path, report_path=stats_path)
        elif name == 'key_signature':
            apply_key_signatures(bpmfix_path, report_path=stats_path)
        elif name == 'clean':
            apply_rule_cleaning(bpmfix_path, cleaned_path, report_path=stats_path, append_report=True,
                                semitone_pairs=args.semitone_pairs)
        elif name == 'copy':
            shutil.copyfile(bpmfix_path, cleaned_path)
        elif name == 'playable':
            apply_playable_reduction(cleaned_path, report_path=stats_path,
                                     max_notes=playable_reduction.DEFAULT_MAX_NOTES_PER_HAND,
                                     max_span=playable_reduction.DEFAULT_MAX_SPAN)
        elif name == 'split':
            apply_hand_split(cleaned_path, report_path=stats_path, append_report=True, hands=args.hands)
        elif name == 'trim':
            apply_overlap_trim(cleaned_path, report_path=stats_path, fast_figure_hands=tuple(args.fast_figure_hands),
                               protect_pedal=not args.no_pedal_protection)
        elif name == 'snapshot':
            write_snapshots(cleaned_path, layouts=SNAPSHOT_LAYOUTS[args.snapshot])

        if before is not None:
            changes.extend(stage_changes(before, note_snapshot(cleaned_path), name))

    save_changes(cleaned_path, changes)

    summary = '\n'.join(['MIDI post-processing run', 'Output: {}'.format(cleaned_path)]
                        + ['{}. {}'.format(index, label) for index, (_, label) in enumerate(steps, 1)]
                        + ['Setting {} = {}'.format(key, value) for key, value in sorted(overrides.items())])
    update_report(stats_path, summary)
    print(summary)
    return cleaned_path, steps


def build_parser():
    parser = argparse.ArgumentParser(
        description='Run the post-processing steps on a transcribed MIDI in order, each one switchable. '
                    'song_bpmfix_cleaned.mid is rebuilt from song_bpmfix.mid on every run, so no MIDI is added.',
    )
    parser.add_argument('input_midi', nargs='?', help='song.mid, song_bpmfix.mid or song_bpmfix_cleaned.mid of one song.')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--bpm', type=float, help='Known BPM; runs BPMFix from song.mid.')
    source.add_argument('--audio', help='Source audio for BPM detection; runs BPMFix from song.mid.')
    parser.add_argument('--no-first-beat', dest='first_beat', action='store_false',
                        help='Skip the automatic first beat alignment after BPMFix.')
    parser.add_argument('--no-key-signature', dest='key_signature', action='store_false',
                        help='Skip key signature detection after BPMFix.')
    parser.add_argument('--no-clean', dest='clean', action='store_false', help='Skip rule cleaning.')
    parser.add_argument('--semitone-pairs', action='store_true',
                        help='Also remove the softer note of semitone pairs in rule cleaning.')
    parser.add_argument('--playable', action='store_true',
                        help='Reduce every onset group to what two hands can play (one-player score).')
    parser.add_argument('--no-split', dest='split', action='store_false', help='Skip the hand split.')
    parser.add_argument('--hands', type=int, choices=(2, 3, 4), default=2, help='Hands for the split (default: 2).')
    parser.add_argument('--no-trim', dest='trim', action='store_false', help='Skip overlap trimming.')
    parser.add_argument('--no-pedal-protection', action='store_true', help='Trim without checking pedal sustain.')
    parser.add_argument('--fast-figure-hands', nargs='+', choices=sorted(CHANNELS), default=list(DEFAULT_FAST_FIGURE_HANDS),
                        help='Overlap trim: hands that get the fast figure rule (default: right second_right).')
    parser.add_argument('--snapshot', choices=sorted(SNAPSHOT_LAYOUTS),
                        help='Also save piano roll images of the result.')
    parser.add_argument('--set', dest='settings', type=parse_setting, action='append', default=[], metavar='KEY=VALUE',
                        help='Override a threshold, e.g. clean.min_velocity=25; repeatable.')
    parser.add_argument('--list-parameters', action='store_true', help='List the thresholds --set accepts and exit.')
    return parser


def format_parameters():
    return '\n'.join('{} = {}  {}'.format(parameter.key, PARAMETER_DEFAULTS[parameter.key], parameter.label)
                     for parameter in PARAMETERS)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_parameters:
        print(format_parameters())
        return 0
    if not args.input_midi:
        parser.error('input_midi is required')
    try:
        run_postprocess(args)
    except ValueError as error:
        print(error)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
