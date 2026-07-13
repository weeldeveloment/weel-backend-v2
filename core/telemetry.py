"""
OpenTelemetry initialization for the Weel Django backend.
Call init_telemetry() once during ASGI/Celery worker startup.

Imports are deferred so the app boots safely before opentelemetry packages
are installed (e.g. local dev or during container build).
"""
import os
import logging

logger = logging.getLogger(__name__)

_Initialized = False
_OtelAvailable = None


def _check_otel():
    global _OtelAvailable
    if _OtelAvailable is not None:
        return _OtelAvailable
    try:
        import opentelemetry  # noqa: F401
        _OtelAvailable = True
    except Exception:
        _OtelAvailable = False
    return _OtelAvailable


def init_telemetry(service_name: str = "weel-backend"):
    """
    Initialize the OpenTelemetry tracer provider and auto-instrument
    Django, Celery, Redis, and PostgreSQL (psycopg2).
    Safe to call multiple times (idempotent).
    Gracefully no-ops if opentelemetry is not installed or endpoint is not set.
    """
    global _Initialized
    if _Initialized:
        return

    if not _check_otel():
        logger.info("OpenTelemetry packages not installed. Tracing disabled.")
        _Initialized = True
        return

    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not otlp_endpoint or otlp_endpoint in {
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "${OTEL_EXPORTER_OTLP_ENDPOINT}",
    }:
        logger.info(
            "OTEL_EXPORTER_OTLP_ENDPOINT not set. Tracing disabled. "
            "Set it to http://tempo:4318 to enable distributed tracing."
        )
        _Initialized = True
        return

    # OTLPSpanExporter speaks HTTP, not gRPC. Tempo exposes gRPC on 4317 and HTTP on 4318.
    # Auto-correct the common misconfiguration so batches don't silently time out.
    if otlp_endpoint.endswith(":4317"):
        corrected = otlp_endpoint[:-5] + ":4318"
        logger.warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT points to gRPC port :4317 but the HTTP "
            "exporter is in use. Auto-correcting to %s. If you intended gRPC, "
            "switch core/telemetry.py to OTLPGrpcSpanExporter.",
            corrected,
        )
        otlp_endpoint = corrected

    # Deferred imports — only executed when OTel is actually used
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION, DEPLOYMENT_ENVIRONMENT
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor

    resource = Resource.create(
        {
            SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", service_name),
            SERVICE_VERSION: os.getenv("OTEL_SERVICE_VERSION", "unknown"),
            DEPLOYMENT_ENVIRONMENT: os.getenv("OTEL_DEPLOYMENT_ENVIRONMENT", "production"),
        }
    )

    provider = TracerProvider(resource=resource)
    # OTLP HTTP exporter needs port :4318, not :4317 (gRPC).
    if otlp_endpoint.endswith(":4317") or ":4317/" in otlp_endpoint:
        otlp_endpoint = otlp_endpoint.replace(":4317", ":4318")
        logger.warning(
            "OTEL endpoint uses gRPC port :4317; corrected to HTTP port :4318 "
            "for HTTP exporter. Set OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4318."
        )
    # OTLP HTTP exporter expects the full traces URL (including /v1/traces).
    if not otlp_endpoint.endswith("/v1/traces"):
        otlp_endpoint = otlp_endpoint.rstrip("/") + "/v1/traces"
    exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
    processor = BatchSpanProcessor(
        exporter,
        max_queue_size=2048,
        max_export_batch_size=512,
        schedule_delay_millis=5000,
    )
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    # Auto-instrument libraries
    DjangoInstrumentor().instrument()
    CeleryInstrumentor().instrument()
    RedisInstrumentor().instrument()
    Psycopg2Instrumentor().instrument()

    _Initialized = True
    logger.info("OpenTelemetry initialized: endpoint=%s", otlp_endpoint)

    # Suppress noisy retry warnings from the OTLP HTTP exporter
    # (it retries with exponential backoff and logs every attempt).
    logging.getLogger("opentelemetry.exporter.otlp.proto.http.trace_exporter").setLevel(logging.ERROR)


def get_trace_context() -> dict:
    """
    Return the current trace_id and span_id as strings for log injection.
    Returns empty strings if OTel is not initialized.
    """
    if not _check_otel():
        return {"trace_id": "", "span_id": ""}

    from opentelemetry import trace
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        return {
            "trace_id": format(ctx.trace_id, "032x"),
            "span_id": format(ctx.span_id, "016x"),
        }
    return {"trace_id": "", "span_id": ""}
