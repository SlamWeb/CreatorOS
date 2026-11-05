"""Append-only diagnostics; account trees are explicit local business snapshots."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from time import monotonic
from uuid import uuid4

from ..ai.context import ContextBudget
from .request_trace import RequestSnapshots, redact


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


def breakdown(context, *, kind, skill_text='', summary_text='', account_text=''):
    """Disjoint payload estimates; framing/rounding remains in overhead."""
    parts = dict.fromkeys(('system', 'tools', 'summary', 'recent_messages',
                          'tool_results', 'skills', 'summary_source'), 0)
    if account_text:
        parts['account_context'] = 0
    if context.tools:
        parts['tools'] = _units(list(context.tools)) // 4
    for message in (*context.system_messages, *context.messages):
        role = message.get('role')
        category = ('system' if role in {'system', 'developer'} else
                    'summary_source' if kind == 'compaction' else
                    'tool_results' if role == 'tool' else 'recent_messages')
        copy = dict(message)
        content = copy.get('content')
        if isinstance(content, list):
            image_count = sum(isinstance(block, dict) and block.get('type') == 'image_ref' for block in content)
            if image_count:
                parts['images'] = parts.get('images', 0) + 1536 * image_count
        if account_text and role == 'user' and content == account_text:
            parts['account_context'] += _units(copy) // 4
            continue
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


def detailed_breakdown(context, *, kind, parts=None):
    """Subdivide the existing accounting without changing its total or the request.

    Values use the same serialization estimate as ``breakdown``, not provider
    tokenization. Tool result names are limited to declared schemas; unknown or
    unmatchable calls are grouped under a fixed label instead of logging model text.
    """
    detail = dict(parts if parts is not None else breakdown(context, kind=kind))
    detail['history_reasoning'] = 0
    detail['tool_arguments'] = 0
    result_by_tool = {}
    if kind != 'main':
        return {'parts': detail, 'tool_results_by_tool': result_by_tool}

    declared = set()
    for schema in context.tools:
        if not isinstance(schema, dict):
            continue
        function = schema.get('function')
        name = function.get('name') if isinstance(function, dict) else schema.get('name')
        if isinstance(name, str):
            declared.add(name)

    call_names = {}
    for message in context.messages:
        role = message.get('role')
        if role == 'assistant':
            copy = deepcopy(message)
            original = _units(copy) // 4
            reasoning = copy.get('reasoning_content')
            if isinstance(reasoning, str) and reasoning:
                del copy['reasoning_content']
                remaining = _units(copy) // 4
                detail['history_reasoning'] += original - remaining
                original = remaining
            calls = copy.get('tool_calls')
            batch_names = {}
            if isinstance(calls, list):
                for call in calls:
                    if not isinstance(call, dict):
                        continue
                    function = call.get('function')
                    target = function if isinstance(function, dict) else call
                    name, call_id = target.get('name'), call.get('id')
                    if isinstance(call_id, str):
                        # Duplicate IDs in a single batch cannot be safely paired.
                        batch_names[call_id] = (name if call_id not in batch_names else None)
                    target.pop('arguments', None)
                call_names.update(batch_names)
            remaining = _units(copy) // 4
            detail['tool_arguments'] += original - remaining
        elif role == 'tool':
            call_id = message.get('tool_call_id')
            name = call_names.get(call_id) if isinstance(call_id, str) else None
            name = name if isinstance(name, str) and name in declared and name != 'unknown' else 'unknown'
            result_by_tool[name] = result_by_tool.get(name, 0) + _units(message) // 4

    detail['recent_messages'] -= detail['history_reasoning'] + detail['tool_arguments']
    return {'parts': detail, 'tool_results_by_tool': result_by_tool}


_TRACE_TAIL_LIMIT = 8 * 1024 * 1024


def _last_sent_main(path):
    """Find the latest sent main request in a bounded tail of this session only."""
    if not path.exists():
        return None
    with path.open('rb') as stream:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(max(0, size - _TRACE_TAIL_LIMIT))
        if size > _TRACE_TAIL_LIMIT:
            stream.readline()  # Discard the possibly incomplete first line.
        lines = stream.read().splitlines()
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if (isinstance(record, dict) and record.get('event') == 'finished'
                and record.get('request_kind') == 'main' and record.get('sent') is True):
            return record
    return None


def _message_category(message):
    if not isinstance(message, dict):
        return 'other'
    role, content = message.get('role'), message.get('content')
    if role in {'system', 'developer'}:
        return 'system'
    if role == 'tool':
        return 'tool_result'
    if role == 'assistant':
        return 'assistant'
    if role == 'user':
        if isinstance(content, str) and content.startswith('[宿主提供的当前账号目录；'):
            return 'account_context'
        if isinstance(content, str) and content.startswith('The conversation history before this point was compacted'):
            return 'summary'
        if isinstance(content, list):
            return 'user_multimodal_or_request_context'
        return 'user'
    return 'other'


def _compare_context(previous, current):
    """Return only coarse structural facts; never return message content or names."""
    old_messages, new_messages = previous['messages'], current['messages']
    same = 0
    for old, new in zip(old_messages, new_messages):
        if old != new:
            break
        same += 1
    if same == min(len(old_messages), len(new_messages)):
        relation = ('unchanged' if len(old_messages) == len(new_messages) else
                    'appended' if len(old_messages) < len(new_messages) else 'removed_suffix')
        categories = []
    else:
        relation = 'changed'
        categories = sorted({_message_category(old_messages[same]), _message_category(new_messages[same])})
    return {'messages_relation': relation, 'same_message_count': same,
            'first_difference_categories': categories,
            'tools_changed': previous['tools'] != current['tools']}


class ContextTrace:
    def __init__(self, session_file, *, capture_payload=False):
        self.path = trace_path(session_file)
        self.session_id = hashlib.sha256(str(Path(session_file).resolve()).encode()).hexdigest()[:24]
        self.snapshots = RequestSnapshots(session_file) if capture_payload else None
        self._previous_main_loaded = False
        self._previous_main = None

    def compare_previous(self, context, model, turn_id):
        """Best-effort local comparison; not a measurement of provider cache reuse."""
        if self.snapshots is None:
            return {'status': 'unavailable', 'reason': 'snapshots_disabled'}
        try:
            if not self._previous_main_loaded:
                self._previous_main = _last_sent_main(self.path)
                self._previous_main_loaded = True
            previous = self._previous_main
            if previous is None:
                return {'status': 'unavailable', 'reason': 'no_previous_sent_main_in_trace_tail'}
            if not previous.get('snapshot_available') or not isinstance(previous.get('request_id'), str):
                return {'status': 'unavailable', 'reason': 'previous_snapshot_not_recorded'}
            old = self.snapshots.read(previous['request_id'])
            messages, tools = context.to_request()
            current, current_redacted = redact({'messages': messages, 'tools': tools})
            comparison = _compare_context(old['context'], current)
            comparison.update(status='available', previous_request_id=previous['request_id'],
                              same_turn=previous.get('turn_id') == turn_id,
                              same_model=previous.get('model') == model,
                              redacted_comparison=bool(old.get('redacted') or current_redacted),
                              basis='redacted_model_context_before_provider_conversion')
            return comparison
        except Exception:
            # Diagnostics must never prevent a model request or reveal the error payload.
            return {'status': 'unavailable', 'reason': 'comparison_failed'}

    def append(self, record):
        if self.snapshots is not None:
            record = redact(record)[0]
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
                if kind == 'main' and span.record.get('sent') is True:
                    self._previous_main = {key: span.record.get(key) for key in
                                           ('request_id', 'turn_id', 'model', 'snapshot_available')}
                    self._previous_main_loaded = True


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

    def begin(self, context, budget, *, skill_text='', summary_text='', account_text=''):
        parts = breakdown(context, kind=self.record['request_kind'], skill_text=skill_text,
                          summary_text=summary_text, account_text=account_text)
        self.record.update(estimated_parts=parts,
                           estimated_input_tokens=budget.estimated_input_tokens,
                           input_limit=budget.input_limit, context_window=budget.context_window,
                           reserve_output_tokens=budget.reserve_output_tokens,
                           started_at=datetime.now(timezone.utc).isoformat())
        if self.record['request_kind'] == 'main':
            self.record['estimated_detail'] = detailed_breakdown(context, kind='main', parts=parts)
        self.started = monotonic()
        self.capture('begin', self.record['turn_id'], context)
        if self.record['request_kind'] == 'main':
            self.record['prefix_comparison'] = self.owner.compare_previous(
                context, self.record['model'], self.record['turn_id'])
        self.owner.append({**self.record, 'status': 'started', 'sent': None})

    def capture(self, operation, *args):
        if self.owner.snapshots is None:
            return
        try:
            getattr(self.owner.snapshots, operation)(self.record['request_id'], *args)
            self.record['snapshot_available'] = True
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            # Diagnostics must not turn an already-effective tool into a failed action.
            self.record['snapshot_available'] = False

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
