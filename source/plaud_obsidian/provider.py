"""Native PLAUD inventories and original recording artifacts, never polished text."""
import hashlib
import json
import math
import re
from .mcp_transport import MCPTransport, RECORD_ID_PATTERN


MAX_PAGES = 10000
ID_PATTERN = re.compile(RECORD_ID_PATTERN + r'\Z')


def valid_id(value):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ValueError('PLAUD_ID_INVALID')
    return value


def safe_text(text, images=False):
    if not isinstance(text, str):
        raise ValueError('PLAUD_TEXT_SCHEMA')
    if images:
        text = re.sub(r'!\[[^\]]*\]\([^\n]*?\)|<img\b[^>]*>', '이미지 첨부: PLAUD에서 확인', text, flags=re.I)
    # Owned source text is preserved. Transport URLs are excluded by field below,
    # never by deleting or rejecting ordinary links mentioned in a meeting.
    return text


def metadata(row):
    if not isinstance(row, dict):
        raise ValueError('PLAUD_METADATA_SCHEMA')
    result = {'id': valid_id(row.get('id')), 'title': safe_text(row.get('name', row.get('title', ''))),
              'created_at': row.get('created_at'), 'start_at': row.get('start_time', row.get('start_at'))}
    for key in ('created_at', 'start_at'):
        value = result[key]
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError('PLAUD_METADATA_SCHEMA')
            if isinstance(value, str):
                if not re.fullmatch(r'[0-9TtZz +.:/-]+', value):
                    raise ValueError('PLAUD_METADATA_SCHEMA')
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError('PLAUD_METADATA_SCHEMA')
    return result


def milliseconds(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or
            (isinstance(value, float) and not math.isfinite(value)) or value != int(value)):
        raise ValueError('PLAUD_TIMESTAMP_SCHEMA')
    hours, rest = divmod(int(value), 3600000)
    minutes, rest = divmod(rest, 60000)
    seconds, millis = divmod(rest, 1000)
    return f'{hours:02}:{minutes:02}:{seconds:02}.{millis:03}'


def transcript_units(content):
    try:
        segments = json.loads(content) if isinstance(content, str) else content
    except ValueError:
        raise ValueError('TRANSCRIPT_SEGMENT_SCHEMA_UNSUPPORTED') from None
    if not isinstance(segments, list):
        raise ValueError('TRANSCRIPT_SEGMENT_SCHEMA_UNSUPPORTED')
    units = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict) or not isinstance(segment.get('content'), str):
            raise ValueError('TRANSCRIPT_SEGMENT_SCHEMA_UNSUPPORTED')
        speaker = segment.get('speaker', '')
        if not isinstance(speaker, (str, int)) or isinstance(speaker, bool):
            raise ValueError('TRANSCRIPT_SEGMENT_SCHEMA_UNSUPPORTED')
        timestamp = milliseconds(segment['start_time']) if 'start_time' in segment else ''
        if 'end_time' in segment:
            if 'start_time' not in segment or segment['end_time'] < segment['start_time']:
                raise ValueError('PLAUD_TIMESTAMP_SCHEMA')
            timestamp += ' - ' + milliseconds(segment['end_time'])
        units.append({'id': str(index), 'speaker': safe_text(str(speaker)), 'timestamp': timestamp, 'text': safe_text(segment['content'])})
    return units


def highlights_text(content):
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            return safe_text(content, images=True)
    if isinstance(content, str):
        return safe_text(content, images=True)
    if not isinstance(content, list):
        raise ValueError('HIGHLIGHTS_SCHEMA_UNSUPPORTED')
    blocks = []
    text_keys = {'title', 'content', 'expanded_content', 'mark_content', 'mark_type_string', 'text'}
    opaque_keys = {'id', 'data_id', 'file_id', 'mark_id', 'mark_type', 'created_at', 'updated_at', 'start_time', 'end_time',
                   'data_link', 'presigned_url', 'ocr_data_link', 'link_expire_timestamp',
                   'is_user_edited', 'mark_status', 'retry_count', 'last_failed_time'}
    for item in content:
        if isinstance(item, str):
            blocks.append(safe_text(item, images=True))
            continue
        if not isinstance(item, dict):
            raise ValueError('HIGHLIGHTS_SCHEMA_UNSUPPORTED')
        deleted = item.get('is_user_deleted')
        if deleted is not None and (not isinstance(deleted, (bool, int)) or deleted not in (0, 1)):
            raise ValueError('HIGHLIGHTS_SCHEMA_UNSUPPORTED')
        if deleted:
            continue
        lines = []
        for key, value in item.items():
            if value is None:
                continue
            if key == 'timestamp':
                lines.append(milliseconds(value))
            elif 'image' in key.lower() or 'picture' in key.lower():
                if value:
                    lines.append('이미지 첨부: PLAUD에서 확인')
            elif key in text_keys or key.endswith(('_text', '_content')):
                if not isinstance(value, str):
                    raise ValueError('HIGHLIGHTS_SCHEMA_UNSUPPORTED')
                lines.append(safe_text(value, images=True))
            elif key in opaque_keys or key == 'is_user_deleted':
                continue
            elif value is not None:
                raise ValueError('HIGHLIGHTS_SCHEMA_UNSUPPORTED')
        blocks.append('\n\n'.join(lines))
    return '\n\n'.join(blocks)


class NativePlaud:
    def __init__(self, settings):
        self.transport = MCPTransport(settings)

    def __enter__(self):
        self.transport.__enter__()
        return self

    def __exit__(self, *exc):
        return self.transport.__exit__(*exc)

    def login(self):
        result = self.transport.call('login', {})
        return {'authenticated': isinstance(result, dict) and result.get('authenticated') is True}

    def list_records(self):
        records, seen = [], set()
        total = None
        for page in range(1, MAX_PAGES + 1):
            result = self.transport.call('list_files', {'page': page, 'page_size': 100})
            if not isinstance(result, dict) or result.get('page') != page or not isinstance(result.get('data'), list):
                raise ValueError('PLAUD_INVENTORY_SCHEMA')
            rows = result['data']
            if 'total' in result:
                count = result['total']
                if isinstance(count, bool) or not isinstance(count, int) or count < 0 or (total is not None and count != total):
                    raise ValueError('PLAUD_INVENTORY_COUNT_MISMATCH')
                total = count
            if len(rows) > 100:
                raise ValueError('PLAUD_INVENTORY_COUNT_MISMATCH')
            for row in rows:
                item = metadata(row)
                if item['id'] in seen:
                    raise ValueError('PLAUD_INVENTORY_DUPLICATE')
                seen.add(item['id'])
                records.append(item)
            if len(rows) < 100:
                if total is not None and len(records) != total:
                    raise ValueError('PLAUD_INVENTORY_COUNT_MISMATCH')
                return records
        raise ValueError('PLAUD_INVENTORY_PAGE_LIMIT')

    def read_record(self, row):
        file_id = valid_id(row.get('id'))
        file = self.transport.call('get_file', {'file_id': file_id})
        result = metadata(file)
        if result['id'] != file_id:
            raise ValueError('PLAUD_RECORD_ID_MISMATCH')
        result.update(artifacts=[], pending=[])
        sources, notes = file.get('source_list', []), file.get('note_list', [])
        sources = [] if sources is None else sources
        notes = [] if notes is None else notes
        if (not isinstance(sources, list) or not isinstance(notes, list) or
                not all(isinstance(item, dict) for item in sources + notes)):
            raise ValueError('PLAUD_RECORD_SCHEMA')
        original = [s for s in sources if s.get('data_type') == 'transaction']
        if len(original) > 1:
            raise ValueError('PLAUD_TRANSCRIPT_DUPLICATE')
        if original:
            content = self._source_content(file_id, original[0])
            units = transcript_units(content) if content else []
            if units:
                result['artifacts'].append({'key': 'Transcript', 'kind': 'Transcript', 'units': units,
                                            'text': json.dumps(units, ensure_ascii=False)})
        if not any(a['kind'] == 'Transcript' for a in result['artifacts']):
            result['pending'].append('Transcript: unavailable')
        highlights, summary_keys = [], set()
        if any(n.get('data_type') in {'auto_sum_note', 'high_light'} and not n.get('data_content') and n.get('data_link') for n in notes):
            fetched = self.transport.call('get_note', {'file_id': file_id})
            if not isinstance(fetched, list) or not all(isinstance(n, dict) for n in fetched):
                raise ValueError('PLAUD_NOTES_SCHEMA')
            def identity(note, index):
                return (note.get('data_type'), note.get('data_id', index))
            original_notes = {identity(n, i): n for i, n in enumerate(notes)}
            fetched_keys = {identity(n, i) for i, n in enumerate(fetched)}
            if any(identity(n, i) not in fetched_keys for i, n in enumerate(notes)
                   if n.get('data_type') in {'auto_sum_note', 'high_light'}):
                raise ValueError('PLAUD_NOTES_INCOMPLETE')
            notes = [original_notes.get(identity(n, i), n)
                     if original_notes.get(identity(n, i), {}).get('data_content') else n
                     for i, n in enumerate(fetched)]
        for note in notes:
            kind = note.get('data_type')
            if kind in {'auto_sum_note', 'high_light'} and note.get('data_content_error'):
                raise ValueError('PLAUD_NOTE_CONTENT_ERROR')
            if kind == 'auto_sum_note':
                identity = note.get('data_id')
                if not isinstance(identity, str) or not identity:
                    raise ValueError('PLAUD_SUMMARY_ID_MISSING')
                key = 'Summary-' + hashlib.sha256(identity.encode('utf-8')).hexdigest()
                if key in summary_keys:
                    raise ValueError('PLAUD_SUMMARY_DUPLICATE')
                summary_keys.add(key)
                content = note.get('data_content')
                if content:
                    if not isinstance(content, str):
                        raise ValueError('PLAUD_SUMMARY_SCHEMA')
                    title = safe_text(note.get('title', note.get('data_title', '')))
                    content = safe_text(content, images=True)
                    text = f'# {title}\n\n{content}' if title else content
                    result['artifacts'].append({'key': key, 'kind': 'Summary', 'text': text})
                else:
                    result['pending'].append(key + ': unavailable')
            elif kind == 'high_light':
                if note.get('data_content'):
                    highlights.append(highlights_text(note['data_content']))
                else:
                    result['pending'].append('Highlights: unavailable')
            else:
                result['pending'].append('Note: unsupported type')
        for source in sources:
            if source.get('data_type') == 'mark_memo':
                content = self._source_content(file_id, source)
                if content:
                    highlights.append(highlights_text(content))
                else:
                    result['pending'].append('Highlights: unavailable')
        if not summary_keys:
            result['pending'].append('Summary: unavailable')
        highlight = '\n\n'.join(text for text in highlights if text)
        if highlight and not any(p.startswith('Highlights:') for p in result['pending']):
            result['artifacts'].append({'key': 'Highlights', 'kind': 'Highlights', 'text': highlight})
        else:
            result['pending'].append('Highlights: unavailable')
        return result

    def _source_content(self, file_id, source):
        if source.get('data_content'):
            return source['data_content']
        if not source.get('data_link'):
            return None
        block = source['data_type']
        items, seen, cursor, total = [], set(), None, None
        for _ in range(MAX_PAGES):
            args = {'file_id': file_id, 'block': block, 'limit': 500}
            if cursor is not None:
                args['cursor'] = cursor
            page = self.transport.call('get_transcript', args)
            key = 'marks' if block == 'mark_memo' else 'segments'
            if not isinstance(page, dict) or not isinstance(page.get(key), list):
                raise ValueError('PLAUD_SOURCE_PAGE_SCHEMA')
            batch = page[key]
            if page.get('file_id') != file_id or page.get('block') != block or page.get('offset') != len(items):
                raise ValueError('PLAUD_SOURCE_PAGE_SCHEMA')
            count = page.get('total')
            if isinstance(count, bool) or not isinstance(count, int) or count < 0 or page.get('returned') != len(batch) or len(batch) > 500:
                raise ValueError('PLAUD_SOURCE_COUNT_MISMATCH')
            if total is not None and count != total:
                raise ValueError('PLAUD_SOURCE_COUNT_MISMATCH')
            total = count
            items.extend(batch)
            cursor = page.get('next_cursor')
            if cursor is None:
                if len(items) != total:
                    raise ValueError('PLAUD_SOURCE_COUNT_MISMATCH')
                return items
            if not batch or len(items) >= total or not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ValueError('PLAUD_SOURCE_CURSOR_INVALID')
            seen.add(cursor)
        raise ValueError('PLAUD_SOURCE_PAGE_LIMIT')
