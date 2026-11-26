"""Watch a rail before a campaign launches: a campaign never starts on a rail failing it.

The watch repeats the route preflight's two checks (`GET /models` on each openai route,
and one tiny schema-bound streamed request through nare per rail) at a fixed interval
over a window, and records every probe. Version 1 of the objective is strict: at least
`min_probes` probes spanning the window and not one failure. Each request probe is a
model call; its usage is recorded and charged to no campaign.
"""
from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from scrutare.config import PreflightSettings, ReviewConfig
from scrutare.engine.preflight import PreflightError, PreflightReport, run_preflight
from scrutare.engine.session_models import NareRuntime

SLO_VERSION = 1
_KEPT = ('check', 'provider', 'model', 'status', 'reason', 'seconds', 'http_status',
         'stop_reason', 'usage', 'skipped')

Probe = Callable[[ReviewConfig, NareRuntime], Awaitable[PreflightReport | None]]


def watched(config: ReviewConfig) -> ReviewConfig:
    """The configuration with both checks on, keeping its own preflight timeout."""
    settings = config.models.preflight
    route = any(rail.provider == 'openai' for rail in config.models.rails)
    checks = PreflightSettings(route=route, request=True,
                               timeout_seconds=settings.timeout_seconds)
    return replace(config, models=replace(config.models, preflight=checks))


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)]


async def watch_rail(config: ReviewConfig, runtime: NareRuntime, *,
                     window_seconds: float = 1800, interval_seconds: float = 60,
                     min_probes: int = 30, probe: Probe = run_preflight,
                     clock: Callable[[], float] = time.monotonic,
                     sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
                     ) -> dict[str, Any]:
    if (type(min_probes) is not int or min_probes < 1
            or not all(type(v) in (int, float) and math.isfinite(v) and v >= 0
                       for v in (window_seconds, interval_seconds))
            or interval_seconds <= 0 or window_seconds > 86400):
        raise ValueError('a rail watch needs a window of at most a day, a positive interval '
                         'and at least one probe')
    conf = watched(config)
    started = clock()
    probes: list[dict[str, Any]] = []
    # Probes at both ends of the window, so its span is the whole window.
    while not probes or clock() - started <= window_seconds or len(probes) < min_probes:
        offset = clock() - started
        try:
            report = await probe(conf, runtime)
            assert report is not None
        except PreflightError as error:
            report = error.report
        probes.append({'probe': len(probes) + 1, 'offset_seconds': round(offset, 3),
                       'status': 'passed' if report.passed else 'failed',
                       'reason': report.reason,
                       'checks': [{k: check[k] for k in _KEPT if k in check}
                                  for check in report.checks]})
        if clock() - started >= window_seconds and len(probes) >= min_probes:
            break
        await sleep(interval_seconds)
    failed = [p for p in probes if p['status'] == 'failed']
    requests = [c for p in probes for c in p['checks'] if c['check'] == 'request']
    routes = [c for p in probes for c in p['checks'] if c['check'] == 'route'
              and c['status'] != 'skipped']
    latency = [c['seconds'] for c in requests if c['status'] == 'passed' and 'seconds' in c]
    span = probes[-1]['offset_seconds']
    # The loop ends only once the window elapsed and the probe floor was met.
    passed = not failed
    return {'schema_version': 1,
            'slo': {'version': SLO_VERSION, 'window_seconds': window_seconds,
                    'interval_seconds': interval_seconds, 'min_probes': min_probes,
                    'objective': 'every probe passes: each openai route lists the model and '
                                 'every rail completes one streamed schema-bound request'},
            # Provider and model only: a route's address stays out of reports.
            'rails': [{'provider': rail.provider, 'model': rail.model}
                      for rail in conf.models.rails],
            'probes': len(probes), 'span_seconds': span,
            'failed_probes': len(failed),
            'reasons': sorted({p['reason'] for p in failed if p['reason']}),
            'route_checks': {'passed': sum(c['status'] == 'passed' for c in routes),
                             'total': len(routes)},
            'request_checks': {'passed': sum(c['status'] == 'passed' for c in requests),
                               'total': len(requests),
                               'p50_seconds': _percentile(latency, 0.5),
                               'p95_seconds': _percentile(latency, 0.95)},
            'decision': 'pass' if passed else 'fail', 'probe_records': probes}
