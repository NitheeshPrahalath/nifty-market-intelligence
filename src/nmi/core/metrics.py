"""A tiny, dependency-free Prometheus metrics registry.

The exposition format is small and stable, and the platform already prefers
hand-rolled primitives (``nmi.api.kv``, ``nmi.core.logging``), so a single
in-process registry is enough to expose request rates, latency and cache/auth
outcomes without pulling in a client library.

The registry is thread-safe because the ASGI app runs request handlers on a
thread pool under ``TestClient`` and under some uvicorn workers.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field

# Prometheus' default HTTP latency buckets, in seconds.
_DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


def _escape(value: str) -> str:
    """Escape a HELP string: backslashes, newlines and double quotes."""
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _fmt_labels(labels: dict[str, object] | None) -> str:
    if not labels:
        return ""
    parts = [f'{key}="{_escape(str(value))}"' for key, value in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


@dataclass
class _Histogram:
    buckets: tuple[float, ...]
    counts: list[int] = field(default_factory=list)
    total: float = 0.0
    count: int = 0

    def __post_init__(self) -> None:
        self.counts = [0] * len(self.buckets)

    def observe(self, value: float) -> None:
        self.total += value
        self.count += 1
        for index, bound in enumerate(self.buckets):
            if value <= bound:
                self.counts[index] += 1


class MetricsRegistry:
    """Counters, gauges and histograms rendered in Prometheus text format."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._meta: dict[str, tuple[str, str]] = {}
        self._counters: dict[tuple[str, tuple], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple], float] = {}
        self._histograms: dict[tuple[str, tuple], _Histogram] = {}
        self._bucket_overrides: dict[str, tuple[float, ...]] = {}

    # -- registration ------------------------------------------------------

    def _register(self, name: str, kind: str, help_text: str) -> None:
        existing = self._meta.get(name)
        if existing is None:
            self._meta[name] = (kind, help_text)
        elif existing[0] != kind:
            raise ValueError(f"metric {name!r} already registered as {existing[0]}")

    @staticmethod
    def _key(labels: dict[str, object] | None) -> tuple:
        return tuple(sorted((str(k), str(v)) for k, v in (labels or {}).items()))

    # -- write -------------------------------------------------------------

    def inc_counter(
        self,
        name: str,
        *,
        help: str = "",
        labels: dict[str, object] | None = None,
        value: float = 1.0,
    ) -> None:
        with self._lock:
            self._register(name, "counter", help)
            self._counters[(name, self._key(labels))] += value

    def set_gauge(
        self, name: str, value: float, *, help: str = "", labels: dict[str, object] | None = None
    ) -> None:
        with self._lock:
            self._register(name, "gauge", help)
            self._gauges[(name, self._key(labels))] = value

    def observe(
        self,
        name: str,
        value: float,
        *,
        help: str = "",
        labels: dict[str, object] | None = None,
        buckets: tuple[float, ...] | None = None,
    ) -> None:
        with self._lock:
            self._register(name, "histogram", help)
            bounds = buckets or self._bucket_overrides.get(name, _DEFAULT_BUCKETS)
            self._bucket_overrides.setdefault(name, bounds)
            key = (name, self._key(labels))
            histogram = self._histograms.get(key)
            if histogram is None:
                histogram = _Histogram(buckets=bounds)
                self._histograms[key] = histogram
            histogram.observe(value)

    # -- read --------------------------------------------------------------

    def render(self) -> str:
        """The whole registry as Prometheus text exposition format (v0.0.4)."""
        with self._lock:
            lines: list[str] = []
            for name, (kind, help_text) in sorted(self._meta.items()):
                lines.append(f"# HELP {name} {_escape(help_text)}")
                lines.append(f"# TYPE {name} {kind}")
                if kind == "counter":
                    for (metric, key), value in sorted(self._counters.items()):
                        if metric == name:
                            lines.append(f"{name}{_fmt_labels(dict(key))} {value}")
                elif kind == "gauge":
                    for (metric, key), value in sorted(self._gauges.items()):
                        if metric == name:
                            lines.append(f"{name}{_fmt_labels(dict(key))} {value}")
                else:
                    for (metric, key), histogram in sorted(self._histograms.items()):
                        if metric != name:
                            continue
                        base = dict(key)
                        for bound, count in zip(
                            histogram.buckets, histogram.counts, strict=True
                        ):
                            lines.append(
                                f"{name}_bucket{_fmt_labels({**base, 'le': bound})} {count}"
                            )
                        lines.append(
                            f"{name}_bucket{_fmt_labels({**base, 'le': '+Inf'})} {histogram.count}"
                        )
                        lines.append(f"{name}_sum{_fmt_labels(base)} {histogram.total}")
                        lines.append(f"{name}_count{_fmt_labels(base)} {histogram.count}")
            return "\n".join(lines) + ("\n" if lines else "")

    def reset(self) -> None:
        with self._lock:
            self._meta.clear()
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._bucket_overrides.clear()


#: The process-wide registry the gateway writes to and ``/metrics`` reads.
REGISTRY = MetricsRegistry()
