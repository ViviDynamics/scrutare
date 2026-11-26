"""Explicit paid/local evaluation commands, independent of ordinary review posting."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from scrutare import __version__
from scrutare.config import ReviewConfig, parse_config
from scrutare.engine.preflight import PreflightError, not_started
from scrutare.engine.session_models import NareRuntime
from scrutare.evaluation.corpus import load_corpus, read_json
from scrutare.evaluation.promotion import evaluate_promotion
from scrutare.evaluation.qualification import inspect_rule_a
from scrutare.evaluation.rail_check import watch_rail
from scrutare.evaluation.runner import RESUME_REASONS, run_experiment, write_json
from scrutare.evaluation.scoring import adjudication_packet, score_experiment
from scrutare.evaluation.screen import load_screen, screen_cases, screen_gate


def session_timeout(config: ReviewConfig, flag: float | None) -> float:
    """The bound each session runs under: the configuration, else the flag, else 600.

    A flag that disagrees with a configured value is refused, so a campaign never records
    one bound in its configuration and runs its sessions under another.
    """
    configured = config.models.session_timeout_seconds
    if configured is not None and flag is not None and float(flag) != float(configured):
        raise ValueError(f"--timeout-seconds {flag:g} disagrees with "
                         f"models.session_timeout_seconds {configured:g}; "
                         "set one or make them equal")
    return config.models.session_seconds if flag is None else float(flag)


def main() -> None:
    parser = argparse.ArgumentParser(description='Non-posting captured review evaluation')
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run', help='explicit model job with three repeats')
    run.add_argument('--corpus', type=Path, required=True)
    run.add_argument('--split', choices=('development', 'holdout', 'all'), default='development')
    run.add_argument('--config', type=Path, required=True)
    run.add_argument('--output', type=Path, required=True)
    run.add_argument('--nare-executable', type=Path, required=True)
    run.add_argument('--timeout-seconds', type=float,
                     help='seconds each session may run when the configuration sets no '
                          'models.session_timeout_seconds (default 600); a value that '
                          'disagrees with a configured one is refused')
    run.add_argument('--concurrency', type=int, default=1)
    run.add_argument('--comparison-set',
                     choices=('baseline', 'procedures', 'architectures', 'focused'),
                     default='baseline')
    run.add_argument('--variants', help='comma-separated subset of the comparison arms')
    run.add_argument('--qualification-rule-a', action='store_true',
                     help='one whole frozen pipeline rerun per failed cell; all attempts charged')
    run.add_argument('--transient-retries', type=int, default=0,
                     help='recorded fresh reruns of a cell stopped only by upstream HTTP 5xx')
    run.add_argument('--transient-retry-delay-seconds', type=float, default=0.0)
    run.add_argument('--transient-retry-budget', type=int,
                     help='most reruns the whole job may spend; unset leaves the per-cell bound')
    run.add_argument('--assessment-tokens', type=int,
                     help='assessor reservation for assessed arms; unset keeps one fifth of '
                          'the total ceiling, which itself never changes')
    run.add_argument('--max-turns', type=int, default=50)
    run.add_argument('--evidence-kind', choices=('model', 'offline'), default='model')
    run.add_argument('--resume', choices=RESUME_REASONS, metavar='HALT',
                     help='continue the rule A campaign in --output after an infrastructure '
                          'halt of this kind (' + ', '.join(RESUME_REASONS) + '), with the '
                          'same arguments as its launch; scored cells never rerun')
    run.add_argument('--screen', type=Path,
                     help='run the cases of this screen manifest (one arm, rule A) instead '
                          'of the whole split')
    gate_screen = sub.add_parser('screen-gate', help='pass/fail a screen run; never promotes')
    gate_screen.add_argument('--experiment', type=Path, required=True)
    gate_screen.add_argument('--output', type=Path, required=True)
    watch = sub.add_parser('rail-check',
                           help='watch a rail before a campaign; exits 5 when it fails')
    watch.add_argument('--config', type=Path, required=True)
    watch.add_argument('--nare-executable', type=Path, required=True)
    watch.add_argument('--output', type=Path, required=True)
    watch.add_argument('--window-seconds', type=float, default=1800)
    watch.add_argument('--interval-seconds', type=float, default=60)
    watch.add_argument('--min-probes', type=int, default=30)
    packet = sub.add_parser('adjudicate', help='export blinded finding packet for human matching')
    packet.add_argument('experiment', type=Path)
    packet.add_argument('--output', type=Path, required=True)
    score = sub.add_parser('score', help='score explicit human decisions; expose pending judgments')
    score.add_argument('experiment', type=Path)
    score.add_argument('--corpus', type=Path, required=True)
    score.add_argument('--decisions', type=Path, required=True)
    score.add_argument('--output', type=Path, required=True)
    operational = sub.add_parser('qualify-rule-a', help='offline rule A gate; never promote')
    operational.add_argument('--experiment', type=Path, required=True)
    operational.add_argument('--gate', type=Path, required=True)
    operational.add_argument('--ledger', type=Path, required=True)
    operational.add_argument('--output', type=Path, required=True)
    gate = sub.add_parser('gate', help='report pass/fail/inconclusive; never change defaults')
    gate.add_argument('--input', type=Path, required=True)
    gate.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'run':
        config = parse_config(args.config.read_bytes())
        try:
            seconds = session_timeout(config, args.timeout_seconds)
        except ValueError as error:
            parser.error(str(error))
        cases = load_corpus(args.corpus, args.split)
        screen = None
        if args.screen is not None:
            if not args.qualification_rule_a or args.variants is None or ',' in args.variants:
                parser.error('--screen needs --qualification-rule-a and exactly one --variants')
            try:
                screen = load_screen(args.screen, args.corpus)
                cases = screen_cases(cases, screen)
            except ValueError as error:
                parser.error(str(error))
        try:
            asyncio.run(run_experiment(
                cases, config, args.output,
                runtime=NareRuntime(args.nare_executable, args.max_turns, seconds),
                evidence_kind=args.evidence_kind, concurrency=args.concurrency,
                comparison_set=args.comparison_set,
                variant_names=(tuple(args.variants.split(','))
                               if args.variants is not None else None),
                transient_retries=args.transient_retries,
                transient_retry_delay_seconds=args.transient_retry_delay_seconds,
                transient_retry_budget=args.transient_retry_budget,
                assessment_tokens=args.assessment_tokens,
                qualification_rule_a=args.qualification_rule_a,
                resume=args.resume, screen=screen))
        except PreflightError as error:
            # No output directory exists: the campaign claimed and spent nothing.
            print(json.dumps(not_started(error.report, __version__), sort_keys=True))
            sys.exit(4)
    elif args.command == 'screen-gate':
        report = screen_gate(args.experiment)
        write_json(args.output, report)
        print(json.dumps({'output': str(args.output), 'decision': report['decision']}))
        sys.exit(0 if report['decision'] == 'pass' else 5)
    elif args.command == 'rail-check':
        config = parse_config(args.config.read_bytes())
        report = asyncio.run(watch_rail(
            config, NareRuntime(args.nare_executable, 50, config.models.session_seconds),
            window_seconds=args.window_seconds, interval_seconds=args.interval_seconds,
            min_probes=args.min_probes))
        write_json(args.output, report)
        print(json.dumps({'output': str(args.output), 'decision': report['decision']}))
        sys.exit(0 if report['decision'] == 'pass' else 5)
    elif args.command == 'adjudicate':
        write_json(args.output, adjudication_packet(read_json(args.experiment)['runs']))
    elif args.command == 'qualify-rule-a':
        write_json(args.output, inspect_rule_a(args.experiment, read_json(args.gate),
                                              read_json(args.ledger)))
    elif args.command == 'gate':
        data = read_json(args.input)
        write_json(args.output, evaluate_promotion(
            data['baseline'], data['candidate'], data['evidence']))
    else:
        experiment = (read_json(args.experiment) if args.experiment.suffix != '.jsonl' else
                      {'evidence_kind': read_json(args.experiment.parent / 'snapshot.json')
                       ['evidence_kind'], 'runs': [json.loads(line) for line in
                                                 args.experiment.read_text().splitlines()]})
        cases = load_corpus(args.corpus, 'all')
        labels = {case.id: read_json(case.labels) for case in cases}
        # Reject changed labels: judgments must refer to the frozen gold snapshot.
        import hashlib
        snapshot = read_json(args.experiment.parent / 'snapshot.json')
        for case in snapshot['cases']:
            path = next(c.labels for c in cases if c.id == case['id'])
            if hashlib.sha256(path.read_bytes()).hexdigest() != case['labels_sha256']:
                raise ValueError('corpus labels changed since experiment')
        write_json(args.output, {'schema_version': 1, 'evidence_kind': experiment['evidence_kind'],
            'metrics': score_experiment(experiment['runs'], labels, read_json(args.decisions),
                                        expected_runs=snapshot['expected_runs'])})
    print(json.dumps({'output': str(args.output)}))


if __name__ == '__main__':
    main()
