"""Synchronous event-loop-owned admission and high-water token accounting."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from scrutare.config import BudgetSettings
from scrutare.engine.session_models import TokenUsage, _boolean, _integer, _persona, _text


@dataclass(frozen=True)
class BudgetLease:
    """One admitted invocation with a cumulative nare token threshold."""

    persona: str
    session_key: str
    baseline: TokenUsage
    limit_tokens: int
    allocated_tokens: int

    def __post_init__(self) -> None:
        _persona(self.persona)
        _text(self.session_key, "session_key")
        if not isinstance(self.baseline, TokenUsage):
            raise ValueError("baseline: expected TokenUsage")
        _integer(self.limit_tokens, "limit_tokens", positive=True)
        _integer(self.allocated_tokens, "allocated_tokens", positive=True)
        if self.limit_tokens != self.baseline.total + self.allocated_tokens:
            raise ValueError("limit_tokens: expected baseline total plus allocation")


def _add(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    return TokenUsage(
        left.input + right.input,
        left.output + right.output,
        left.cache_read + right.cache_read,
        left.cache_write + right.cache_write,
    )


def _delta(current: TokenUsage, previous: TokenUsage) -> TokenUsage:
    if not isinstance(current, TokenUsage):
        raise ValueError("usage: expected TokenUsage")
    values = (
        current.input - previous.input,
        current.output - previous.output,
        current.cache_read - previous.cache_read,
        current.cache_write - previous.cache_write,
    )
    if any(value < 0 for value in values):
        raise ValueError("usage: cumulative counters must not decrease")
    return TokenUsage(*values)


class ReviewBudgetLedger:
    """Fixed fair quotas, with reservations and actual usage across attempts."""

    def __init__(self, persona_names: tuple[str, ...], settings: BudgetSettings) -> None:
        if not isinstance(persona_names, tuple) or not persona_names:
            raise ValueError("persona_names: expected a nonempty tuple")
        for persona in persona_names:
            _persona(persona)
        if len(set(persona_names)) != len(persona_names):
            raise ValueError("persona_names: duplicate persona")
        if not isinstance(settings, BudgetSettings):
            raise ValueError("settings: expected BudgetSettings")
        _integer(settings.per_persona_tokens, "per_persona_tokens", positive=True)
        _integer(settings.review_max_tokens, "review_max_tokens", positive=True)
        self._names = persona_names
        self._settings = settings
        total = min(settings.review_max_tokens, len(persona_names) * settings.per_persona_tokens)
        quotient, remainder = divmod(total, len(persona_names))
        self._allocations = {
            name: quotient + (index < remainder) for index, name in enumerate(persona_names)
        }
        self._persona_usage = dict.fromkeys(persona_names, TokenUsage())
        self._sessions: dict[str, tuple[str, TokenUsage]] = {}
        self._active: dict[str, BudgetLease] = {}
        self._usage = TokenUsage()
        self._accounting_complete = True

    @property
    def allocations(self) -> Mapping[str, int]:
        return MappingProxyType(self._allocations)

    @property
    def usage(self) -> TokenUsage:
        return self._usage

    @property
    def exhausted(self) -> bool:
        return self._usage.total >= self._settings.review_max_tokens

    @property
    def accounting_complete(self) -> bool:
        return self._accounting_complete

    def _remaining(self, lease: BudgetLease) -> int:
        return max(0, lease.limit_tokens - self._sessions[lease.session_key][1].total)

    def admit(
        self, persona: str, session_key: str, baseline: TokenUsage = TokenUsage()
    ) -> BudgetLease | None:
        _persona(persona)
        if persona not in self._allocations:
            raise ValueError("persona: expected a configured persona")
        _text(session_key, "session_key")
        if not isinstance(baseline, TokenUsage):
            raise ValueError("baseline: expected TokenUsage")
        previous = self._sessions.get(session_key)
        if previous is None:
            if baseline != TokenUsage():
                raise ValueError("baseline: a fresh session must start at zero")
        elif previous != (persona, baseline):
            raise ValueError("baseline: expected this persona's exact session high-water vector")
        if not self.accounting_complete or self.exhausted or persona in self._active:
            return None
        available = self._allocations[persona] - self._persona_usage[persona].total
        reserved = sum(self._remaining(lease) for lease in self._active.values())
        grant = min(
            available, max(0, self._settings.review_max_tokens - self.usage.total - reserved)
        )
        if grant <= 0:
            return None
        lease = BudgetLease(persona, session_key, baseline, baseline.total + grant, grant)
        self._sessions[session_key] = (persona, baseline)
        self._active[persona] = lease
        return lease

    def _validate_lease(self, lease: BudgetLease) -> None:
        if not isinstance(lease, BudgetLease) or self._active.get(lease.persona) is not lease:
            raise ValueError("lease: expected the active ledger-issued lease")

    def observe(self, lease: BudgetLease, cumulative_usage: TokenUsage) -> None:
        self._validate_lease(lease)
        previous = self._sessions[lease.session_key][1]
        delta = _delta(cumulative_usage, previous)
        self._sessions[lease.session_key] = (lease.persona, cumulative_usage)
        self._persona_usage[lease.persona] = _add(self._persona_usage[lease.persona], delta)
        self._usage = _add(self._usage, delta)

    def settle(
        self, lease: BudgetLease, final_usage: TokenUsage, accounting_complete: bool
    ) -> None:
        self._validate_lease(lease)
        _boolean(accounting_complete, "accounting_complete")
        self.observe(lease, final_usage)
        del self._active[lease.persona]
        self._accounting_complete = self._accounting_complete and accounting_complete

    def snapshot(self) -> dict[str, object]:
        return {
            "configured": {
                "review_max_tokens": self._settings.review_max_tokens,
                "per_persona_tokens": self._settings.per_persona_tokens,
            },
            "allocations": dict(self._allocations),
            "usage": self.usage.to_dict(),
            "personas": [
                {
                    "persona": name,
                    "allocated_tokens": self._allocations[name],
                    "usage": self._persona_usage[name].to_dict(),
                    "exhausted": self._persona_usage[name].total >= self._allocations[name],
                    "overshoot_tokens": max(
                        0, self._persona_usage[name].total - self._allocations[name]
                    ),
                }
                for name in self._names
            ],
            "sessions": [
                {"session_key": key, "persona": persona, "usage": usage.to_dict()}
                for key, (persona, usage) in sorted(self._sessions.items())
            ],
            "active_reservations": [
                {
                    "persona": name,
                    "session_key": lease.session_key,
                    "baseline": lease.baseline.to_dict(),
                    "allocated_tokens": lease.allocated_tokens,
                    "limit_tokens": lease.limit_tokens,
                    "remaining_tokens": self._remaining(lease),
                }
                for name in self._names
                if (lease := self._active.get(name)) is not None
            ],
            "accounting_complete": self.accounting_complete,
            "exhausted": self.exhausted,
            "overshoot_tokens": max(0, self.usage.total - self._settings.review_max_tokens),
        }
