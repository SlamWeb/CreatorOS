"""Pure observed-request metrics. No pricing, model calls or filesystem defaults.

Records are individual model requests, never Codex cumulative thread snapshots.
Percentiles linearly interpolate sorted samples at (n - 1) * quantile. Missing
samples stay missing; observed sums are not presented as complete totals.
"""
from __future__ import annotations

from collections.abc import Mapping
import math


TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_hit_tokens", "cache_miss_tokens",
                "context_estimated_tokens", "tool_schema_tokens")
TIME_FIELDS = ("elapsed_seconds", "ttft_ms", "first_tool_ms")


def _number(value, name, *, integer=False):
    if value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0 or integer and not isinstance(value, int)):
        raise ValueError(f"{name} must be a finite nonnegative {'integer' if integer else 'number'} or null")
    return value


def _summary(values, count):
    known = sorted(value for value in values if value is not None)
    def percentile(fraction):
        if not known:
            return None
        position = (len(known) - 1) * fraction
        left = int(position)
        right = min(left + 1, len(known) - 1)
        return known[left] + (known[right] - known[left]) * (position - left)
    return {"p50": percentile(0.5), "p95": percentile(0.95),
            "observed_count": len(known), "total_count": count,
            "coverage": len(known) / count if count else None,
            "status": "no_calls" if not count else "complete" if len(known) == count else "partial"}


def _model(record):
    model = record.get("model")
    if isinstance(model, Mapping):
        return str(model.get("name") or "unknown"), str(model.get("provider") or "")
    return str(model or "unknown"), str(record.get("provider") or "")


def _is_codex(record):
    name, provider = _model(record)
    return "codex" in name.casefold() or "codex" in provider.casefold()


def _bounds(value, name):
    if isinstance(value, (list, tuple)):
        if len(value) != 2:
            raise ValueError(f"{name} requires [minimum, maximum]")
        lower, upper = [_number(item, name) for item in value]
    else:
        lower = upper = _number(value, name)
    if lower is None or upper is None or lower > upper:
        raise ValueError(f"{name} requires finite ordered prices")
    return lower, upper


def _cost(records, pricing):
    codex_calls = sum(_is_codex(row) for row in records)
    eligible = [row for row in records if not _is_codex(row)]
    result = {"currency": pricing.get("currency", "USD") if pricing else None,
              "estimated": True, "minimum": None, "maximum": None,
              "observed_minimum": None, "observed_maximum": None,
              "priced_calls": 0, "unpriced_calls": len(eligible),
              "status": "unavailable", "source": pricing.get("source") if pricing else None,
              "frozen_at": pricing.get("frozen_at") if pricing else None,
              "codex": {"calls": codex_calls, "amount": None, "reason": "billing_not_observed"}}
    if not eligible:
        result.update(status="no_calls", minimum=0.0, maximum=0.0,
                      observed_minimum=0.0, observed_maximum=0.0)
        return result
    if pricing is None:
        return result
    models, aliases = pricing.get("models", {}), pricing.get("aliases", {})
    if not isinstance(models, Mapping) or not isinstance(aliases, Mapping):
        raise ValueError("pricing models and aliases must be objects")
    amounts = []
    for row in eligible:
        model = _model(row)[0]
        model = aliases.get(model, model)
        rates = models.get(model)
        if rates is None:
            continue
        if not isinstance(rates, Mapping):
            raise ValueError("model pricing must be an object")
        hit_rate = _bounds(rates.get("cache_hit_per_million"), "cache_hit_per_million")
        miss_rate = _bounds(rates.get("cache_miss_per_million"), "cache_miss_per_million")
        output_rate = _bounds(rates.get("output_per_million"), "output_per_million")
        input_tokens, output_tokens = row.get("input_tokens"), row.get("output_tokens")
        if input_tokens is None or output_tokens is None:
            continue
        hit, miss = row.get("cache_hit_tokens"), row.get("cache_miss_tokens")
        # Missing cache buckets are bounded, never assumed to be zero cache hits.
        known_hit, known_miss = hit if hit is not None else 0, miss if miss is not None else 0
        remaining = input_tokens - known_hit - known_miss
        lower = (known_hit * hit_rate[0] + known_miss * miss_rate[0]
                 + remaining * min(hit_rate[0], miss_rate[0]) + output_tokens * output_rate[0]) / 1_000_000
        upper = (known_hit * hit_rate[1] + known_miss * miss_rate[1]
                 + remaining * max(hit_rate[1], miss_rate[1]) + output_tokens * output_rate[1]) / 1_000_000
        amounts.append((lower, upper))
    result.update(priced_calls=len(amounts), unpriced_calls=len(eligible) - len(amounts))
    if amounts:
        lower, upper = sum(pair[0] for pair in amounts), sum(pair[1] for pair in amounts)
        result.update(observed_minimum=lower, observed_maximum=upper,
                      status="complete" if len(amounts) == len(eligible) else "partial")
        if len(amounts) == len(eligible):
            result.update(minimum=lower, maximum=upper)
    return result


def aggregate_metrics(records, pricing=None):
    """Aggregate request DTOs, keeping missing usage and unobserved bills explicit.

    ``pricing`` is a frozen caller-supplied object with currency/source/frozen_at,
    optional aliases, and models keyed by model name. Each model supplies
    cache_hit_per_million, cache_miss_per_million and output_per_million as a
    scalar or [min,max]. Unknown models remain unpriced. Codex records should
    carry model.provider='Codex'; their monetary bill is always unknown.
    """
    if pricing is not None and not isinstance(pricing, Mapping):
        raise ValueError("pricing must be an object or null")
    rows, identifiers = [], set()
    for original in records:
        if not isinstance(original, Mapping):
            raise ValueError("request record must be an object")
        row = dict(original)
        identifier = row.get("request_id")
        if identifier is not None:
            if not isinstance(identifier, str) or not identifier or identifier in identifiers:
                raise ValueError("request_id must be unique and nonempty")
            identifiers.add(identifier)
        if row.get("usage_scope") not in {None, "request"}:
            raise ValueError("aggregate_metrics only accepts per-request usage, not cumulative snapshots")
        for field in TOKEN_FIELDS:
            row[field] = _number(row.get(field), field, integer=True)
        for field in TIME_FIELDS:
            row[field] = _number(row.get(field), field)
        buckets = [row[key] for key in ("cache_hit_tokens", "cache_miss_tokens") if row[key] is not None]
        if row["input_tokens"] is not None and sum(buckets) > row["input_tokens"]:
            raise ValueError("cache token buckets exceed input_tokens")
        rows.append(row)
    count = len(rows)
    observed = {key: [row[key] for row in rows if row[key] is not None] for key in TOKEN_FIELDS}
    result = {"schema_version": 1, "model_calls": count,
              "error_calls": sum(row.get("error") is not None for row in rows),
              "usage": {}, "coverage": {}, "latency": {},
              "percentile_method": "linear_interpolation_(n-1)*q"}
    for field, values in observed.items():
        result["usage"][field] = sum(values) if len(values) == count else None
        result["usage"]["observed_" + field] = sum(values) if values or not count else None
        result["coverage"][field] = {"observed_calls": len(values), "total_calls": count,
                                      "ratio": len(values) / count if count else None}
    result["usage"]["peak_input_tokens"] = max(observed["input_tokens"], default=None)
    result["usage"]["peak_context_estimated_tokens"] = max(observed["context_estimated_tokens"], default=None)
    result["usage"]["peak_tool_schema_tokens"] = max(observed["tool_schema_tokens"], default=None)
    complete_usage = sum(row["input_tokens"] is not None and row["output_tokens"] is not None for row in rows)
    result["usage_status"] = "no_calls" if not count else "complete" if complete_usage == count else "partial"
    result["usage_complete_calls"] = complete_usage
    for field in TIME_FIELDS:
        result["latency"][field] = _summary([row[field] for row in rows], count)
    cache_rows = [row for row in rows if row["cache_hit_tokens"] is not None and row["cache_miss_tokens"] is not None
                  and row["input_tokens"] is not None
                  and row["cache_hit_tokens"] + row["cache_miss_tokens"] == row["input_tokens"]]
    hit = sum(row["cache_hit_tokens"] for row in cache_rows)
    denominator = sum(row["input_tokens"] for row in cache_rows)
    ratio = hit / denominator if denominator else None
    total_input = result["usage"]["input_tokens"]
    result["cache"] = {"hit_ratio": ratio if len(cache_rows) == count else None,
                       "observed_hit_ratio": ratio, "observed_calls": len(cache_rows), "total_calls": count,
                       "request_coverage": len(cache_rows) / count if count else None,
                       "token_coverage": denominator / total_input if total_input else None,
                       "status": "no_calls" if not count else "complete" if len(cache_rows) == count else "partial"}
    result["cost"] = _cost(rows, pricing)
    return result
