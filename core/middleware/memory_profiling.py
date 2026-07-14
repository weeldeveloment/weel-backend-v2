"""
Active Python memory leak detection middleware.
Uses tracemalloc to measure per-request heap growth and exports
Prometheus-style metrics for Grafana dashboards.

NOTE: pympler was removed from the per-request path because
muppy.get_objects() + summary.summarize() takes ~4.7s on a
fully-loaded Django heap (711k+ objects), causing Daphne worker
pool exhaustion and 503 timeouts under concurrent load.
"""
import logging
import os
import random
import tracemalloc

from django.http import HttpRequest
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)


def _parse_sample_rate() -> float:
    raw = os.getenv("MEMORY_PROFILING_SAMPLE_RATE", "0.01").strip()
    try:
        rate = float(raw)
        return max(0.0, min(rate, 1.0))
    except ValueError:
        logger.warning("Invalid MEMORY_PROFILING_SAMPLE_RATE=%r, falling back to 0.01", raw)
        return 0.01


class MemoryProfilingMiddleware(MiddlewareMixin):
    """
    Middleware that captures memory allocation growth per HTTP request.
    Exposes Prometheus metrics via the django-prometheus registry if available.
    """

    def __init__(self, get_response=None):
        super().__init__(get_response)
        self._enabled = os.getenv("MEMORY_PROFILING_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
        self._sample_rate = _parse_sample_rate()
        self._trace_started = False
        if self._enabled and not tracemalloc.is_tracing():
            try:
                tracemalloc.start()
                self._trace_started = True
                logger.info("tracemalloc started for memory profiling")
            except Exception as exc:
                logger.warning("Failed to start tracemalloc: %s", exc)
                self._enabled = False

        # Register custom prometheus metrics (lazy init)
        self._prom_growth = None
        try:
            from prometheus_client import Histogram
            self._prom_growth = Histogram(
                "django_request_memory_growth_bytes",
                "Heap allocation growth per request in bytes",
                ["method", "path"],
                buckets=[0, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216],
            )
        except Exception:
            pass

    def _should_sample(self) -> bool:
        return random.random() < self._sample_rate

    def process_request(self, request: HttpRequest):
        if self._enabled and self._should_sample():
            request._mem_snapshot_before = tracemalloc.take_snapshot()

    def process_response(self, request, response):
        if not self._enabled or not hasattr(request, "_mem_snapshot_before"):
            return response

        try:
            after = tracemalloc.take_snapshot()
            before = request._mem_snapshot_before
            top_stats = after.compare_to(before, "lineno")

            total_growth = sum(stat.size_diff for stat in top_stats if stat.size_diff > 0)
            method = request.method or "UNKNOWN"
            path = request.path or "/"

            if self._prom_growth is not None:
                self._prom_growth.labels(method=method, path=path).observe(total_growth)

            # Log top 3 allocations if growth is suspicious (>1MB)
            if total_growth > 1_048_576:
                top3 = top_stats[:3]
                lines = [f"{s.traceback.format()}: {s.size_diff / 1024:.1f} KiB" for s in top3]
                logger.warning(
                    "Large memory growth on %s %s: %d bytes. Top allocations:\n%s",
                    method,
                    path,
                    total_growth,
                    "\n".join(lines),
                )

        except Exception:
            logger.exception("Memory profiling failed for %s %s", request.method, request.path)

        return response


class CeleryMemoryProfiler:
    """
    Decorator / context manager for profiling Celery task memory.
    Usage in a task:
        @app.task
        @celery_memory_profile
        def my_task():
            ...
    """

    def __init__(self, task_func):
        self.task_func = task_func
        self._enabled = os.getenv("MEMORY_PROFILING_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
        self._sample_rate = _parse_sample_rate()
        self._prom_growth = None
        try:
            from prometheus_client import Histogram
            self._prom_growth = Histogram(
                "django_celery_task_memory_growth_bytes",
                "Heap allocation growth per Celery task in bytes",
                ["task_name"],
                buckets=[0, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216],
            )
        except Exception:
            pass

    def _should_sample(self) -> bool:
        return random.random() < self._sample_rate

    def __call__(self, *args, **kwargs):
        if not self._enabled or not tracemalloc.is_tracing() or not self._should_sample():
            return self.task_func(*args, **kwargs)

        before = tracemalloc.take_snapshot()
        try:
            return self.task_func(*args, **kwargs)
        finally:
            after = tracemalloc.take_snapshot()
            top_stats = after.compare_to(before, "lineno")
            total_growth = sum(stat.size_diff for stat in top_stats if stat.size_diff > 0)
            task_name = getattr(self.task_func, "__name__", "unknown")
            if self._prom_growth is not None:
                self._prom_growth.labels(task_name=task_name).observe(total_growth)
            if total_growth > 1_048_576:
                logger.warning(
                    "Large memory growth in task %s: %d bytes",
                    task_name,
                    total_growth,
                )


celery_memory_profile = CeleryMemoryProfiler
