import json
from pathlib import Path

import pytest

from creatoros.context import RuntimeContext
from creatoros.session.artifacts import root_for
from creatoros.tools.builtins import read_file, ARCHIVE_SEARCH_SCAN_CHARS
from creatoros.tools.models import ReadFileArgs


def setup_archive(tmp_path):
    session = tmp_path / 'session.json'
    archive = root_for(session)
    archive.mkdir()
    return archive, RuntimeContext(tmp_path, session_file=session, archive_only_reads=True)


def search(path, ctx, query, **kwargs):
    result = read_file(str(path), context=ctx, search=query, **kwargs)
    assert not result.is_error, result.content
    return json.loads(result.content.split('：', 1)[1])


def test_unicode_special_literal_cross_chunk_and_overlapping_matches(tmp_path):
    archive, ctx = setup_archive(tmp_path)
    target = archive / 'result.txt'
    target.write_text('x' * 32767 + '中$[文' + ' aaa AAA aaa', encoding='utf-8')
    result = search(target, ctx, '中$[文')
    assert result['complete'] and result['stop_reason'] == 'eof'
    assert result['matches'][0]['offset'] == 32768
    assert len(search(target, ctx, 'aaa')['matches']) == 2
    assert len(search(target, ctx, 'AAA')['matches']) == 1
    target.write_text('aaaa', encoding='utf-8')
    result = search(target, ctx, 'aa', limit=2)
    assert [m['offset'] for m in result['matches']] == [1, 2]
    assert result['stop_reason'] == 'match_limit' and result['next_offset'] == 3
    result = search(target, ctx, 'aa', offset=result['next_offset'])
    assert [m['offset'] for m in result['matches']] == [3]
    assert result['complete']


def test_budget_boundary_and_continuation(tmp_path):
    archive, ctx = setup_archive(tmp_path)
    target = archive / 'big.txt'
    target.write_text('x' * (ARCHIVE_SEARCH_SCAN_CHARS - 1) + '中文' + 'x' * 30 + '中文', encoding='utf-8')
    result = search(target, ctx, '中文')
    assert [m['offset'] for m in result['matches']] == [ARCHIVE_SEARCH_SCAN_CHARS]
    assert result['stop_reason'] == 'scan_budget'
    assert result['next_offset'] == ARCHIVE_SEARCH_SCAN_CHARS + 1
    more = search(target, ctx, '中文', offset=result['next_offset'])
    assert more['complete'] and len(more['matches']) == 1
    assert more['matches'][0]['offset'] == ARCHIVE_SEARCH_SCAN_CHARS + 32
    assert len(json.dumps(result, ensure_ascii=False)) < 2500


def test_scope_errors_and_bounds(tmp_path):
    archive, ctx = setup_archive(tmp_path)
    target = archive / 'result.txt'
    target.write_text('secret ' + 'hit' * 20, encoding='utf-8')
    other = root_for(tmp_path / 'other.json'); other.mkdir()
    (other / 'file.txt').write_text('secret', encoding='utf-8')
    plain = tmp_path / 'plain.txt'; plain.write_text('secret', encoding='utf-8')
    for path in [plain, other / 'file.txt']:
        assert read_file(str(path), search='secret', context=ctx).error_type == 'path_out_of_scope'
        assert read_file(str(path), search='secret', context=RuntimeContext(tmp_path)).error_type == 'path_out_of_scope'
    assert read_file(str(archive / 'missing'), search='x', context=ctx).error_type == 'file_read_failed'
    assert read_file(str(target), search='', context=ctx).error_type == 'invalid_arguments'
    assert read_file(str(target), search='x' * 201, context=ctx).error_type == 'invalid_arguments'
    assert len(search(target, ctx, 'hit', limit=100)['matches']) == 5
    with (archive / 'huge').open('wb') as f:
        f.truncate(16 * 1024 * 1024 + 1)
    assert read_file(str(archive / 'huge'), search='x', context=ctx).error_type == 'file_too_large'
    assert ReadFileArgs(path='x', search='中文').search == '中文'


def test_symlink_never_searched(tmp_path):
    archive, ctx = setup_archive(tmp_path)
    target = archive / 'real'; target.write_text('hit', encoding='utf-8')
    link = archive / 'link'
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip('creating symbolic links is not permitted on this Windows installation')
    assert read_file(str(link), search='hit', context=ctx).error_type == 'path_out_of_scope'
