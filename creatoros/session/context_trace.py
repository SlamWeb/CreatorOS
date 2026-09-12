"""Content-free, append-only diagnostics for one session's model requests."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from time import monotonic
from uuid import uuid4

from ..ai.context import ContextBudget


def trace_path(session_file):
    return Path(session_file).with_suffix('.context-trace.jsonl')


def checkpoint_id(checkpoint):
    if checkpoint is None:
        return None
    value = json.dumps(checkpoint.to_dict(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def _units(value):
    text = json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    return sum(1 if char.isascii() else 4 for char in text)


def breakdown(context, *, kind, skill_text='', summary_text=''):
    """Disjoint payload estimates; framing/rounding remains in overhead."""
    parts = dict.fromkeys(('system', 'tools', 'summary', 'recent_messages',
                          'tool_results', 'skills', 'summary_source'), 0)
    if context.tools:
        parts['tools'] = _units(list(context.tools)) // 4
    for message in (*context.system_messages, *context.messages):
        role = message.get('role')
        category = ('system' if role in {'system', 'developer'} else
                    'summary_source' if kind == 'compaction' else
                    'tool_results' if role == 'tool' else 'recent_messages')
        copy = dict(message)
        content = copy.get('content')
        if isinstance(content, str):
            for text, target in ((skill_text if category == 'system' else '', 'skills'),
                                 (summary_text if role == 'user' else '', 'summary')):
                if text and text in content:
                    content = content.replace(text, '', 1)
                    # Remove JSON string quotes: they belong to message framing.
                    parts[target] += (_units(text) - 2) // 4
            copy['content'] = content
        parts[category] += _units(copy) // 4
    total = ContextBudget.from_context(context).estimated_input_tokens
    parts['serialization_overhead'] = total - sum(parts.values())
    return parts


class ContextTrace:
    def __init__(self, session_file):
        self.path = trace_path(session_file)
        self.session_id = hashlib.sha256(str(Path(session_file).resolve()).encode()).hexdigest()[:24]

    def append(self, record):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A process killed mid-write may leave an incomplete final JSON line.
        if self.path.exists() and self.path.stat().st_size:
            with self.path.open('rb+') as stream:
                stream.seek(-1, 2)
                if stream.read(1) != b'\n':
                    stream.seek(0, 2)
                    stream.write(b'\n')
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')

    @contextmanager
    def request(self, kind, provider, *, turn_id, checkpoint=None, **metadata):
        span = TraceRequest(self, kind, provider, turn_id, checkpoint, metadata)
        try:
            yield span
        except BaseException as error:
            if span.record['status'] != 'blocked':
                span.record['status'] = ('interrupted' if isinstance(error, (KeyboardInterrupt, GeneratorExit))
                                         or type(error).__name__ == 'ChatStopped' else 'failed')
            span.record['error_type'] = type(error).__name__
            raise
        finally:
            if span.started is not None:
                span.record['elapsed_ms'] = round((monotonic() - span.started) * 1000, 2)
                span.record['event'] = 'finished'
                self.append(span.record)


class TraceRequest:
    def __init__(self, owner, kind, provider, turn_id, checkpoint, metadata):
        self.owner = owner
        self.started = None
        self.record = dict(schema_version=1, event='started', request_id=uuid4().hex,
                           session_id=owner.session_id, turn_id=turn_id, request_kind=kind,
                           model=getattr(provider, 'model', type(provider).__name__),
                           checkpoint_id=checkpoint_id(checkpoint), status='succeeded',
                           sent=False, usage=None, error_type=None, **metadata)
        self.record['externalized'] = bool(metadata.get('externalized_count', 0))

    def begin(self, context, budget, *, skill_text='', summary_text=''):
        self.record.update(estimated_parts=breakdown(context, kind=self.record['request_kind'],
                           skill_text=skill_text, summary_text=summary_text),
                           estimated_input_tokens=budget.estimated_input_tokens,
                           input_limit=budget.input_limit, context_window=budget.context_window,
                           reserve_output_tokens=budget.reserve_output_tokens,
                           started_at=datetime.now(timezone.utc).isoformat())
        self.started = monotonic()
        self.owner.append({**self.record, 'status': 'started', 'sent': None})

    def usage(self, usage):
        if usage is not None:
            self.record['usage'] = {**{'cache_hit_tokens': None, 'cache_miss_tokens': None},
                                    **usage.to_dict()}


def read_trace(session_file, after=0, limit=50):
    """Cursor counts physical lines, including a partial crash tail."""
    items, cursor, more = [], after, False
    path = trace_path(session_file)
    if path.exists():
        with path.open(encoding='utf-8', errors='replace') as stream:
            for number, line in enumerate(stream, 1):
                if number <= after:
                    continue
                if not line.endswith('\n'):
                    # A live writer can finish this same line on the next poll.
                    break
                if len(items) >= limit:
                    more = True
                    break
                cursor = number
                try:
                    items.append(json.loads(line))
                except ValueError:
                    continue
    return {'items': items, 'next_cursor': cursor, 'has_more': more}
