"""Explicit paid/local evaluation commands, independent of ordinary review posting."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from scrutare.config import parse_config
from scrutare.engine.session_models import NareRuntime
from scrutare.evaluation.corpus import load_corpus, read_json
from scrutare.evaluation.promotion import evaluate_promotion
from scrutare.evaluation.runner import run_experiment, write_json
from scrutare.evaluation.scoring import adjudication_packet, score_experiment


def main() -> None:
    parser = argparse.ArgumentParser(description='Non-posting captured review evaluation')
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run', help='explicit model job with three repeats')
    run.add_argument('--corpus', type=Path, required=True)
    run.add_argument('--split', choices=('development', 'holdout', 'all'), default='development')
    run.add_argument('--config', type=Path, required=True)
    run.add_argument('--output', type=Path, required=True)
    run.add_argument('--nare-executable', type=Path, required=True)
    run.add_argument('--timeout-seconds', type=float, default=600)
    run.add_argument('--concurrency', type=int, default=1)
    run.add_argument('--comparison-set',
                     choices=('baseline', 'procedures', 'architectures', 'focused'),
                     default='baseline')
    run.add_argument('--variants', help='comma-separated subset of the comparison arms')
    run.add_argument('--transient-retries', type=int, default=0,
                     help='recorded fresh reruns of a cell stopped only by upstream HTTP 5xx')
    run.add_argument('--transient-retry-delay-seconds', type=float, default=0.0)
    run.add_argument('--transient-retry-budget', type=int,
                     help='most reruns the whole job may spend; unset leaves the per-cell bound')
    run.add_argument('--max-turns', type=int, default=50)
    run.add_argument('--evidence-kind', choices=('model', 'offline'), default='model')
    packet = sub.add_parser('adjudicate', help='export blinded finding packet for human matching')
    packet.add_argument('experiment', type=Path)
    packet.add_argument('--output', type=Path, required=True)
    score = sub.add_parser('score', help='score explicit human decisions; expose pending judgments')
    score.add_argument('experiment', type=Path)
    score.add_argument('--corpus', type=Path, required=True)
    score.add_argument('--decisions', type=Path, required=True)
    score.add_argument('--output', type=Path, required=True)
    gate = sub.add_parser('gate', help='report pass/fail/inconclusive; never change defaults')
    gate.add_argument('--input', type=Path, required=True)
    gate.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'run':
        asyncio.run(run_experiment(load_corpus(args.corpus, args.split),
            parse_config(args.config.read_bytes()), args.output,
            runtime=NareRuntime(args.nare_executable, args.max_turns, args.timeout_seconds),
            evidence_kind=args.evidence_kind, concurrency=args.concurrency,
            comparison_set=args.comparison_set,
            variant_names=tuple(args.variants.split(',')) if args.variants is not None else None,
            transient_retries=args.transient_retries,
            transient_retry_delay_seconds=args.transient_retry_delay_seconds,
            transient_retry_budget=args.transient_retry_budget))
    elif args.command == 'adjudicate':
        write_json(args.output, adjudication_packet(read_json(args.experiment)['runs']))
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
