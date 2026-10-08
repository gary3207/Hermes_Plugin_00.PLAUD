import importlib.util
import json
import unittest
from unittest.mock import patch

provider = None
if importlib.util.find_spec('plaud_obsidian.provider'):
    from plaud_obsidian import provider

ID = 'of_' + 'a' * 32


class FakeMCP:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        pass
    def call(self, name, args=None):
        self.calls.append((name, args))
        return self.responses.pop(0)


class ProviderTests(unittest.TestCase):
    def native(self, responses):
        self.assertTrue(hasattr(provider, 'NativePlaud'), 'native provider is missing')
        fake = FakeMCP(responses)
        patcher = patch.object(provider, 'MCPTransport', return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return provider.NativePlaud({}), fake

    def test_inventory_fetches_every_page_without_filters_and_preserves_ids(self):
        rows = [{'id': f'{i:032x}', 'name':f'Name {i}', 'created_at':i, 'start_time':i+1,
                 'presigned_url':'https://private.invalid/secret'} for i in range(100)]
        native, fake = self.native([{'data':rows, 'page':1}, {'data':[], 'page':2}])
        with native:
            result = native.list_records()
        self.assertEqual(len(result), 100)
        self.assertEqual(result[0], {'id':'0'*32, 'title':'Name 0', 'created_at':0, 'start_at':1})
        self.assertEqual(fake.calls, [('list_files', {'page':1, 'page_size':100}),
                                     ('list_files', {'page':2, 'page_size':100})])


    def test_inline_original_segments_all_summaries_and_merged_highlights(self):
        segments = [{'speaker':'A', 'start_time':1, 'end_time':1999, 'content':' First\nsecond\r\n '},
                    {'speaker':'A', 'start_time':2000, 'end_time':2001, 'content':''}]
        notes = [{'data_type':'auto_sum_note', 'data_id':'one', 'title':'Tab One', 'data_content':'Summary one'},
                 {'data_type':'auto_sum_note', 'data_id':'two', 'title':'Tab Two', 'data_content':'Summary two'},
                 {'data_type':'high_light', 'data_content':'Source note\n\nSecond paragraph'}]
        file = {'id':ID, 'name':'Meeting', 'source_list':[
            {'data_type':'transaction', 'data_content':json.dumps(segments)},
            {'data_type':'transaction_polish', 'data_content':'DO NOT IMPORT'},
            {'data_type':'mark_memo', 'data_content':json.dumps([{'timestamp':1001, 'title':'Flag', 'content':'Marked', 'expanded_content':'Expanded', 'custom_text':'Keep custom', 'image_url':'https://private.invalid/picture'}])}],
            'note_list':notes, 'presigned_url':'https://private.invalid/audio'}
        native, fake = self.native([file])
        result = native.read_record({'id':ID})
        self.assertEqual(result['id'], ID)
        transcript = result['artifacts'][0]
        self.assertEqual(transcript['key'], 'Transcript')
        self.assertEqual([u['text'] for u in transcript['units']], [s['content'] for s in segments])
        self.assertEqual([u['id'] for u in transcript['units']], ['0', '1'])
        self.assertEqual(transcript['units'][0]['timestamp'], '00:00:00.001 - 00:00:01.999')
        summaries = [a for a in result['artifacts'] if a['kind'] == 'Summary']
        self.assertEqual(len(summaries), 2)
        self.assertIn('Tab One', summaries[0]['text'])
        self.assertIn('Summary two', summaries[1]['text'])
        self.assertNotEqual(summaries[0]['key'], summaries[1]['key'])
        highlights = next(a['text'] for a in result['artifacts'] if a['kind'] == 'Highlights')
        for text in ['Source note\n\nSecond paragraph', 'Flag', 'Marked', 'Expanded', 'Keep custom', '00:00:01.001', '이미지 첨부: PLAUD에서 확인']:
            self.assertIn(text, highlights)
        self.assertNotIn('private.invalid', str(result))
        self.assertNotIn('DO NOT IMPORT', str(result))
        self.assertEqual(fake.calls, [('get_file', {'file_id':ID})])


    def test_linked_sources_follow_cursors_and_resolve_full_note_list(self):
        segment = lambda text: {'speaker':'S', 'start_time':0, 'end_time':1, 'content':text}
        file = {'id':ID, 'name':'Linked', 'source_list':[
            {'data_type':'transaction', 'data_content':'', 'data_link':'https://private.invalid/transcript'},
            {'data_type':'mark_memo', 'data_link':'https://private.invalid/marks'}],
            'note_list':[{'data_type':'auto_sum_note', 'data_id':'x', 'data_link':'https://private.invalid/note'}]}
        page = lambda block, total, offset, data, cursor: {'file_id':ID, 'block':block, 'total':total, 'offset':offset, 'returned':len(data), 'next_cursor':cursor, 'marks' if block == 'mark_memo' else 'segments':data}
        native, fake = self.native([file, page('transaction', 2, 0, [segment('First\nparagraph')], 'next'),
                                   page('transaction', 2, 1, [segment('Second')], None),
                                   [{'data_type':'auto_sum_note', 'data_id':'x', 'data_content':'Resolved note'}],
                                   page('mark_memo', 1, 0, [{'mark_content':'Resolved mark'}], None)])
        result = native.read_record({'id':ID})
        self.assertEqual([u['text'] for u in result['artifacts'][0]['units']], ['First\nparagraph', 'Second'])
        self.assertIn('Resolved note', str(result))
        self.assertIn('Resolved mark', str(result))
        self.assertEqual(fake.calls, [('get_file', {'file_id':ID}),
            ('get_transcript', {'file_id':ID, 'block':'transaction', 'limit':500}),
            ('get_transcript', {'file_id':ID, 'block':'transaction', 'limit':500, 'cursor':'next'}),
            ('get_note', {'file_id':ID}),
            ('get_transcript', {'file_id':ID, 'block':'mark_memo', 'limit':500})])
        self.assertNotIn('private.invalid', str(result))


    def test_note_resolution_prefers_inline_and_rejects_missing_requested_tabs(self):
        inline = {'data_type':'auto_sum_note', 'data_id':'inline', 'title':'Original title', 'data_content':'Original inline'}
        linked = {'data_type':'auto_sum_note', 'data_id':'linked', 'data_link':'https://private.invalid/note'}
        file = {'id':ID, 'note_list':[inline, linked]}
        fetched = [dict(inline, data_content='Changed while reading'), dict(linked, data_content='Fetched')]
        native, _ = self.native([file, fetched])
        result = native.read_record({'id':ID})
        self.assertIn('Original inline', str(result))
        self.assertNotIn('Changed while reading', str(result))
        native, _ = self.native([file, [fetched[0]]])
        with self.assertRaisesRegex(ValueError, 'PLAUD_NOTES_INCOMPLETE'):
            native.read_record({'id':ID})


    def test_images_are_markers_and_owned_text_references_are_preserved(self):
        note = {'data_type':'high_light', 'data_content':'Before\n\n![photo](https://private.invalid/image?secret=x)\n\nAfter'}
        native, _ = self.native([{'id':ID, 'note_list':[note]}])
        result = native.read_record({'id':ID})
        text = result['artifacts'][0]['text']
        self.assertEqual(text, 'Before\n\n이미지 첨부: PLAUD에서 확인\n\nAfter')
        for body in ['https://private.invalid/signed?token=secret', 'C:\\Users\\Private\\secret.txt']:
            native, _ = self.native([{'id':ID, 'source_list':[{'data_type':'mark_memo', 'data_content':json.dumps([{'custom_text':body}])}]}])
            with self.subTest(body=body):
                self.assertEqual(native.read_record({'id':ID})['artifacts'][0]['text'], body)


    def test_malformed_record_containers_fail_instead_of_becoming_empty(self):
        for key, value in [('source_list', {}), ('source_list', [1]), ('note_list', ''), ('note_list', [False])]:
            native, _ = self.native([{'id':ID, key:value}])
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, '^PLAUD_RECORD_SCHEMA$'):
                native.read_record({'id':ID})


    def test_login_returns_only_authentication_boolean(self):
        native, fake = self.native([{'authenticated':True, 'token':'never return this', 'url':'private'}])
        self.assertEqual(native.login(), {'authenticated':True})
        self.assertEqual(fake.calls, [('login', {})])


    def test_inventory_reported_total_must_match_the_complete_scan(self):
        native, _ = self.native([{'page':1, 'data':[{'id':ID}], 'total':2}])
        with self.assertRaisesRegex(ValueError, 'PLAUD_INVENTORY_COUNT_MISMATCH'):
            native.list_records()


    def test_highlight_text_variants_keep_text_but_ignore_opaque_links(self):
        rows = ['Plain paragraph', {'content':'Body', 'description_text':'Additional', 'data_link':'https://private.invalid/first', 'mark_id':'opaque-id'}]
        file = {'id':ID, 'source_list':[{'data_type':'mark_memo', 'data_content':json.dumps(rows)}]}
        native, _ = self.native([file])
        text = native.read_record({'id':ID})['artifacts'][0]['text']
        self.assertEqual(text, 'Plain paragraph\n\nBody\n\nAdditional')
        rows[1]['data_link'] = 'https://private.invalid/changed-expiry'
        native, _ = self.native([file | {'source_list':[{'data_type':'mark_memo', 'data_content':json.dumps(rows)}]}])
        self.assertEqual(native.read_record({'id':ID})['artifacts'][0]['text'], text)


    def test_invalid_timestamps_fail_with_a_safe_schema_category(self):
        for timestamp in [float('nan'), float('inf'), True, -1, 1.5, '123']:
            segment = {'content':'Exact', 'start_time':timestamp}
            native, _ = self.native([{'id':ID, 'source_list':[{'data_type':'transaction', 'data_content':json.dumps([segment])}]}])
            with self.subTest(timestamp=timestamp), self.assertRaisesRegex(ValueError, '^PLAUD_TIMESTAMP_SCHEMA$'):
                native.read_record({'id':ID})


    def test_metadata_does_not_publish_nested_or_private_date_fields(self):
        for value in [{'url':'https://private.invalid'}, 'C:\\Users\\Private\\auth', True]:
            native, _ = self.native([{'data':[{'id':ID, 'created_at':value}], 'page':1}])
            with self.subTest(value=value), self.assertRaises(ValueError):
                native.list_records()


    def test_incomplete_highlight_components_stay_pending_without_partial_artifact(self):
        file = {'id':ID, 'note_list':[{'data_type':'high_light', 'data_content':'Ready half'}],
                'source_list':[{'data_type':'mark_memo', 'data_content':''}]}
        native, _ = self.native([file])
        result = native.read_record({'id':ID})
        self.assertFalse(any(a['kind'] == 'Highlights' for a in result['artifacts']))
        self.assertTrue(any(p.startswith('Highlights:') for p in result['pending']))


    def test_ambiguous_deleted_flag_cannot_silently_drop_highlight_text(self):
        file = {'id':ID, 'source_list':[{'data_type':'mark_memo', 'data_content':json.dumps([{'content':'Must not disappear', 'is_user_deleted':'false'}])}]}
        native, _ = self.native([file])
        with self.assertRaisesRegex(ValueError, 'HIGHLIGHTS_SCHEMA_UNSUPPORTED'):
            native.read_record({'id':ID})


    def test_inventory_exceeds_native_query_scan_budget_and_detects_duplicates(self):
        rows = [{'id':f'{i:032x}', 'name':str(i)} for i in range(601)]
        pages = [{'page':i//100+1, 'data':rows[i:i+100]} for i in range(0, 601, 100)]
        native, fake = self.native(pages)
        self.assertEqual(len(native.list_records()), 601)
        self.assertEqual(len(fake.calls), 7)
        native, _ = self.native([{'page':1, 'data':[{'id':ID}, {'id':ID}]}])
        with self.assertRaisesRegex(ValueError, 'PLAUD_INVENTORY_DUPLICATE'):
            native.list_records()

    def test_page_ceiling_fails_instead_of_truncating_inventory(self):
        rows = [{'id':f'{i:032x}'} for i in range(100)]
        native, _ = self.native([{'page':1, 'data':rows}])
        with patch.object(provider, 'MAX_PAGES', 1), self.assertRaisesRegex(ValueError, 'PLAUD_INVENTORY_PAGE_LIMIT'):
            native.list_records()

    def test_ids_are_literal_and_never_repaired(self):
        for bad in [ID+'\n', ' '+ID, ID.upper().replace('OF_', 'OF_'), 'a'*31, 'of_'+ 'z'*32]:
            native, fake = self.native([])
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'PLAUD_ID_INVALID'):
                native.read_record({'id':bad})
            self.assertEqual(fake.calls, [])
        upper_id = 'of_'+'AB'*16
        native, _ = self.native([{'page':1, 'data':[{'id':upper_id}]}])
        self.assertEqual(native.list_records()[0]['id'], upper_id)

    def test_plain_transcript_is_not_split_into_fabricated_segments(self):
        native, _ = self.native([{'id':ID, 'source_list':[{'data_type':'transaction', 'data_content':'First\nSecond'}]}])
        with self.assertRaisesRegex(ValueError, 'TRANSCRIPT_SEGMENT_SCHEMA_UNSUPPORTED'):
            native.read_record({'id':ID})

    def test_missing_artifacts_remain_pending_and_unknown_note_payload_is_ignored(self):
        native, _ = self.native([{'id':ID, 'note_list':[{'data_type':'unknown', 'data_content':'private opaque payload'}]}])
        result = native.read_record({'id':ID})
        self.assertEqual(result['artifacts'], [])
        for kind in ['Transcript', 'Summary', 'Highlights']:
            self.assertTrue(any(p.startswith(kind+':') for p in result['pending']))
        self.assertNotIn('private opaque payload', str(result))

    def test_note_link_failure_is_visible_not_empty_content(self):
        note = {'data_type':'auto_sum_note', 'data_id':'literal', 'data_link':'private'}
        native, _ = self.native([{'id':ID, 'note_list':[note]}, [note | {'data_content_error':'secret failure detail'}]])
        with self.assertRaisesRegex(ValueError, '^PLAUD_NOTE_CONTENT_ERROR$'):
            native.read_record({'id':ID})

    def test_linked_pagination_rejects_inconsistent_counts_and_repeated_cursors(self):
        source = {'data_type':'transaction', 'data_link':'private'}
        file = {'id':ID, 'source_list':[source]}
        page = {'file_id':ID, 'block':'transaction', 'total':3, 'offset':0, 'returned':1,
                'next_cursor':'next', 'segments':[{'content':'One'}]}
        for broken in [page | {'returned':2}, page | {'next_cursor':None}, page | {'offset':1}]:
            native, _ = self.native([file, broken])
            with self.subTest(broken=broken), self.assertRaisesRegex(ValueError, 'PLAUD_SOURCE_'):
                native.read_record({'id':ID})
        native, _ = self.native([file, page, page | {'offset':1}])
        with self.assertRaisesRegex(ValueError, 'PLAUD_SOURCE_CURSOR_INVALID'):
            native.read_record({'id':ID})

    def test_unknown_highlight_fields_fail_visibly_without_dropping_text(self):
        native, _ = self.native([{'id':ID, 'source_list':[{'data_type':'mark_memo', 'data_content':json.dumps([{'unclassified':'Important source text'}])}]}])
        with self.assertRaisesRegex(ValueError, 'HIGHLIGHTS_SCHEMA_UNSUPPORTED'):
            native.read_record({'id':ID})


    def test_highlight_nullable_text_and_operational_metadata_keep_owned_text(self):
        mark = {'timestamp': 1000, 'mark_type': 1, 'mark_type_string': None,
                'mark_content': 'Keep the entire marked paragraph.', 'content': None,
                'title': None, 'expanded_content': None, 'picture_link': None,
                'mark_id': None, 'ocr_data_link': None, 'link_expire_timestamp': None,
                'picture_local_path': None, 'picture_full_url': None,
                'is_user_edited': False, 'mark_status': 0, 'error_message': None,
                'retry_count': None, 'last_failed_time': None, 'is_user_deleted': None}
        native, _ = self.native([{'id': ID, 'source_list': [
            {'data_type': 'mark_memo', 'data_content': json.dumps([mark])}]}])
        result = native.read_record({'id': ID})
        self.assertIn('Keep the entire marked paragraph.', result['artifacts'][0]['text'])
        self.assertNotIn('None', result['artifacts'][0]['text'])


if __name__ == '__main__':
    unittest.main()
