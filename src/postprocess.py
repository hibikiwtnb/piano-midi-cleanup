import argparse
import os
import shutil
import sys

from bpmfix_utils import apply_bpm_fix, bpmfix_path_for
from clean_midi_rules import apply_rule_cleaning, cleaned_path_for
from first_beat_ui import auto_align_first_beat
from hand_split_channels import CHANNELS, apply_hand_split
from key_signature import apply_key_signatures
from midi_stats import stats_path_for, write_midi_stats
from pianoroll_snapshot import write_snapshots
from playable_reduction import DEFAULT_MAX_NOTES_PER_HAND, DEFAULT_MAX_SPAN, apply_playable_reduction
from trim_overlaps import DEFAULT_FAST_FIGURE_HANDS, apply_overlap_trim


# Report sections that belong to song_bpmfix.mid; they are kept when it is reused, everything after is rebuilt.
BPMFIX_SECTIONS = (
    'MIDI quality stats',
    'MIDI BPMFix stats',
    'MIDI auto first beat stats',
    'MIDI first beat stats',
    'MIDI key signature stats',
)
SNAPSHOT_LAYOUTS = {
    'landscape': ('landscape',),
    'portrait': ('portrait',),
    'both': ('landscape', 'portrait'),
}


def source_midi_path(midi_path):
    """song.mid from song.mid, song_bpmfix.mid or song_bpmfix_cleaned.mid."""
    filename, ext = os.path.splitext(midi_path)
    for suffix in ('_cleaned', '_bpmfix'):
        if filename.endswith(suffix):
            filename = filename[:-len(suffix)]
    return filename + ext


def cleaned_first_beat_edits(stats_path):
    """Whether a first beat was set by hand on song_bpmfix_cleaned.mid, which rebuilding it would lose."""
    if not os.path.exists(stats_path):
        return False
    with open(stats_path, encoding='utf-8') as file:
        sections = [section.strip('\n').splitlines() for section in file.read().split('\n\n\n') if section.strip()]
    return any(lines[0] == 'MIDI first beat stats' and '_cleaned.mid' in lines[1] for lines in sections if len(lines) > 1)


def keep_bpmfix_sections(stats_path):
    """Drop the report sections of earlier runs that came after song_bpmfix.mid was made."""
    if not os.path.exists(stats_path):
        return
    with open(stats_path, encoding='utf-8') as file:
        sections = [section.strip('\n') for section in file.read().split('\n\n\n') if section.strip()]
    kept = [
        section for section in sections
        if section.splitlines()[0] in BPMFIX_SECTIONS and '_cleaned.mid' not in section.splitlines()[1]
    ]
    with open(stats_path, 'w', encoding='utf-8') as file:
        file.write('\n\n\n'.join(kept) + '\n')


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
        steps.append(('playable', 'Playable reduction ({} notes, {} semitones per hand)'.format(
            args.max_notes_per_hand, args.max_span)))
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

    if 'bpmfix' in names:
        if not os.path.isfile(source_path):
            raise ValueError('Source MIDI does not exist: {}'.format(source_path))
        write_midi_stats(source_path)
    else:
        if not os.path.isfile(bpmfix_path):
            raise ValueError('No BPM-fixed MIDI to reuse; give --bpm or --audio: {}'.format(bpmfix_path))
        if cleaned_first_beat_edits(stats_path) and not args.discard_cleaned_edits:
            raise ValueError(
                'The first beat was set by hand on {}, which this run rebuilds from {}. Set the first beat on '
                'the BPM-fixed MIDI in the workbench, or pass --discard-cleaned-edits.'.format(cleaned_path, bpmfix_path))
        keep_bpmfix_sections(stats_path)

    for name in names:
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
            apply_playable_reduction(cleaned_path, report_path=stats_path, max_notes=args.max_notes_per_hand,
                                     max_span=args.max_span)
        elif name == 'split':
            apply_hand_split(cleaned_path, report_path=stats_path, append_report=True, hands=args.hands)
        elif name == 'trim':
            apply_overlap_trim(cleaned_path, report_path=stats_path, fast_figure_hands=tuple(args.fast_figure_hands))
        elif name == 'snapshot':
            write_snapshots(cleaned_path, layouts=SNAPSHOT_LAYOUTS[args.snapshot])

    summary = '\n'.join(['MIDI post-processing run', 'Output: {}'.format(cleaned_path)]
                        + ['{}. {}'.format(index, label) for index, (_, label) in enumerate(steps, 1)])
    with open(stats_path, 'a', encoding='utf-8') as file:
        file.write('\n\n' + summary + '\n')
    print(summary)
    return cleaned_path, steps


def build_parser():
    parser = argparse.ArgumentParser(
        description='Run the post-processing steps on a transcribed MIDI in order, each one switchable. '
                    'song_bpmfix_cleaned.mid is rebuilt from song_bpmfix.mid on every run, so no MIDI is added.',
    )
    parser.add_argument('input_midi', help='song.mid, song_bpmfix.mid or song_bpmfix_cleaned.mid of one song.')
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
    parser.add_argument('--max-notes-per-hand', type=int, default=DEFAULT_MAX_NOTES_PER_HAND,
                        help='Playable reduction: most notes per hand (default: {}).'.format(DEFAULT_MAX_NOTES_PER_HAND))
    parser.add_argument('--max-span', type=int, default=DEFAULT_MAX_SPAN,
                        help='Playable reduction: widest hand span in semitones (default: {}).'.format(DEFAULT_MAX_SPAN))
    parser.add_argument('--no-split', dest='split', action='store_false', help='Skip the hand split.')
    parser.add_argument('--hands', type=int, choices=(2, 3, 4), default=2, help='Hands for the split (default: 2).')
    parser.add_argument('--no-trim', dest='trim', action='store_false', help='Skip overlap trimming.')
    parser.add_argument('--fast-figure-hands', nargs='+', choices=sorted(CHANNELS), default=list(DEFAULT_FAST_FIGURE_HANDS),
                        help='Overlap trim: hands that get the fast figure rule (default: right second_right).')
    parser.add_argument('--discard-cleaned-edits', action='store_true',
                        help='Rebuild song_bpmfix_cleaned.mid even when its first beat was set by hand.')
    parser.add_argument('--snapshot', choices=sorted(SNAPSHOT_LAYOUTS),
                        help='Also save piano roll images of the result.')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        run_postprocess(args)
    except ValueError as error:
        print(error)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
