"""Offline editorial-export contracts; no browser, media or model requests."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from creator_notes import review as postprocess


def fixture(directory, count=229):
    folder = Path(directory) / 'original'
    folder.mkdir()
    segments = [
        {'start': i * 2.0, 'end': i * 2.0 + 1.8,
         'text': 'AI\u80fd\u5e2e\u5fd9\u643a\u5e26\u5417' if i == 2 else f'\u7b2c{i}\u6bb5\u5b8c\u6574\u539f\u6587\uff0c\u4fdd\u7559\u6bcf\u4e00\u53e5\u3002',
         'needs_review': i == 2,
         'words': [{'start': i * 2.0, 'end': i * 2.0 + .2, 'text': '\u539f'}]}
        for i in range(count)
    ]
    report = {'check_id': 'a' * 32, 'status': 'done', 'complete': True,
              'latest': {'video_id': '1234567890123456789', 'title': 'AI\u5f00\u53d1\u4e2a\u4eba\u7f51\u7ad9',
                         'url': 'https://www.douyin.com/video/1234567890123456789'},
              'transcription': {'duration_seconds': count * 2.0, 'segments': count}}
    originals = {
        'report.json': json.dumps(report, ensure_ascii=False, indent=2),
        'segments.json': json.dumps(segments, ensure_ascii=False, indent=2),
        'report.md': '# \u539f\u59cb\u673a\u5668\u7a3f\n\n' + '\n'.join(s['text'] for s in segments),
        'transcript.txt': '\n'.join(s['text'] for s in segments),
        'transcript.srt': '\n\n'.join(
            f"{i}\n{postprocess.stamp(s['start'], True)} --> {postprocess.stamp(s['end'], True)}\n{s['text']}"
            for i, s in enumerate(segments, 1)) + '\n',
    }
    for name, content in originals.items():
        (folder / name).write_text(content, encoding='utf-8')
    request = {
        'source_sha256': postprocess.source_sha256(folder / 'segments.json'),
        'reviewer': 'Offline contract fixture',
        'edits': [{'index': 2, 'before': segments[2]['text'], 'after': 'AI\u80fd\u5e2e\u5fd9\u5199\u4ee3\u7801',
                   'evidence_level': 'user_confirmed', 'confidence': 'high',
                   'reason': '\u7528\u6237\u660e\u786e\u6307\u51fa\u8be5\u5904\u8bef\u8bc6\u522b\u3002', 'evidence': '\u7528\u6237\u63d0\u4f9b\uff1a\u643a\u5e26\u5417\u5e94\u4e3a\u5199\u4ee3\u7801\u3002'}],
        'pending_review': [{'index': 5, 'issue': '\u5177\u4f53\u8bbe\u8ba1\u547d\u540d\u4ec5\u51ed\u4e0a\u4e0b\u6587\u65e0\u6cd5\u786e\u5b9a\uff0c\u4fdd\u7559\u539f\u53e5\u3002'}],
        'overview': {
            'summary': '\u4f5c\u8005\u6f14\u793a\u7528 AI \u5f00\u53d1\u4e2a\u4eba\u7f51\u7ad9\u53ca\u914d\u7f6e\u7ef4\u62a4\u4efb\u52a1\u3002',
            'sections': [{'start': 0, 'end': 10, 'title': '\u7f51\u7ad9\u5f00\u53d1',
                          'summary': '\u4f5c\u8005\u4ecb\u7ecd\u7f51\u7ad9\u5f00\u53d1\u6d41\u7a0b\u3002'},
                         {'start': 10, 'end': count * 2.0, 'title': '\u7ef4\u62a4\u4efb\u52a1',
                          'summary': '\u4f5c\u8005\u5c55\u793a\u4efb\u52a1\u914d\u7f6e\u5e76\u53d1\u8d77\u4e00\u6b21\u8fd0\u884c\u3002'}],
            'limitations': ['\u6982\u89c8\u4ec5\u6982\u62ec\u89c6\u9891\u6240\u8ff0\uff0c\u672a\u72ec\u7acb\u9a8c\u8bc1\u4ea7\u54c1\u80fd\u529b\u6216\u81ea\u52a8\u66f4\u65b0\u6267\u884c\u7ed3\u679c\u3002'],
        },
    }
    return folder / 'report.json', segments, request


def asset_bytes(folder):
    return {name: (folder / name).read_bytes() for name in postprocess.ORIGINALS}


class PostprocessContractTests(unittest.TestCase):
    def test_all_229_segments_and_timestamp_boundaries_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path, source, request = fixture(tmp)
            before = asset_bytes(report_path.parent)
            manifest = postprocess.run_review(report_path, request)
            files = {name: Path(path) for name, path in manifest['files'].items()}
            corrected = json.loads(files['corrected_segments.json'].read_text('utf-8'))
            self.assertEqual(229, manifest['source_segments'])
            self.assertEqual(229, manifest['corrected_segments'])
            self.assertEqual(229, len(corrected))
            self.assertTrue(manifest['originals_preserved'])
            self.assertTrue(manifest['segment_timestamps_preserved'])
            self.assertFalse(manifest['word_timestamps_recomputed'])
            for i, (original, revised) in enumerate(zip(source, corrected)):
                self.assertEqual(i, revised['source_index'])
                self.assertEqual((original['start'], original['end']),
                                 (revised['start'], revised['end']))
                self.assertEqual(request['edits'][0]['after'] if i == 2 else original['text'],
                                 revised['text'])
                self.assertEqual(i == 2, revised['changed'])
                self.assertEqual('not_retimed', revised['word_alignment'])
                self.assertNotIn('words', revised)
            timed = files['corrected_transcript.md'].read_text('utf-8')
            self.assertEqual(229, len(re.findall(r'^\[\d{2}:\d{2}:\d{2}\.\d{3} \u2192 ', timed, re.M)))
            srt = files['corrected_transcript.srt'].read_text('utf-8')
            self.assertEqual(229, srt.count(' --> '))
            self.assertIn('\n229\n', srt)
            self.assertIn('00:07:36,000 --> 00:07:37,800', srt)
            self.assertEqual([s['text'] for s in corrected],
                             files['corrected_transcript.txt'].read_text('utf-8').splitlines())
            self.assertEqual(before, asset_bytes(report_path.parent))
            self.assertEqual({name: hashlib.sha256(data).hexdigest() for name, data in before.items()},
                             manifest['original_hashes'])

    def test_pending_text_is_kept_and_context_is_not_audio_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path, source, request = fixture(tmp)
            request['edits'][0]['evidence_level'] = 'context_inferred'
            result = postprocess.run_review(report_path, request)
            self.assertEqual('reviewed_with_pending', result['status'])
            self.assertEqual(0, result['audio_checked_segments'])
            self.assertEqual(1, result['pending_segments'])
            revised = json.loads(Path(result['files']['corrected_segments.json']).read_text('utf-8'))
            self.assertEqual(source[5]['text'], revised[5]['text'])
            self.assertFalse(revised[5]['changed'])
            markdown = Path(result['files']['corrected_transcript.md']).read_text('utf-8')
            self.assertIn('has not been checked word by word against the audio', markdown)
            self.assertNotIn('has been checked word by word against the audio', markdown)
            differences = Path(result['files']['corrections.md']).read_text('utf-8')
            for phrase in [source[2]['text'], request['edits'][0]['after'], 'context_inferred',
                           request['pending_review'][0]['issue'], source[5]['text']]:
                self.assertIn(phrase, differences)
            overview = Path(result['files']['overview.md']).read_text('utf-8')
            self.assertIn(request['overview']['summary'], overview)
            self.assertIn(request['overview']['limitations'][0], overview)
            self.assertIn('not automatically independently verified facts', overview)

    def test_repeated_review_creates_new_revision_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path, _, request = fixture(tmp)
            source_before = asset_bytes(report_path.parent)
            first = postprocess.run_review(report_path, request)
            first_bytes = {name: Path(path).read_bytes() for name, path in first['files'].items()}
            second_request = copy.deepcopy(request)
            second_request['overview']['summary'] = '\u7b2c\u4e8c\u6b21\u5ba1\u6821\u8865\u5145\u4e86\u5bf9\u89c6\u9891\u6d41\u7a0b\u7684\u6982\u62ec\u3002'
            second = postprocess.run_review(report_path, second_request)
            self.assertNotEqual(first['revision'], second['revision'])
            self.assertEqual(first_bytes, {name: Path(path).read_bytes() for name, path in first['files'].items()})
            self.assertEqual(source_before, asset_bytes(report_path.parent))
            self.assertEqual(second, json.loads((report_path.parent / 'review-latest.json').read_text('utf-8')))

    def test_stale_fingerprint_and_wrong_before_reject_without_exports(self):
        mutations = [lambda r: r.update(source_sha256='0' * 64),
                     lambda r: r['edits'][0].update(before='\u53e6\u4e00\u4efd\u7a3f\u5b50\u7684\u539f\u53e5')]
        for mutate in mutations:
            with self.subTest(mutation=mutate), tempfile.TemporaryDirectory() as tmp:
                report_path, _, request = fixture(tmp)
                original = asset_bytes(report_path.parent)
                mutate(request)
                with self.assertRaises(ValueError):
                    postprocess.run_review(report_path, request)
                self.assertEqual(original, asset_bytes(report_path.parent))
                self.assertEqual([], list(report_path.parent.glob('proofread-*')))
                self.assertFalse((report_path.parent / 'review-latest.json').exists())

    def test_invalid_edits_reject_before_any_revision(self):
        mutations = [
            ('boolean index', lambda r: r['edits'][0].update(index=True)),
            ('negative index', lambda r: r['edits'][0].update(index=-1)),
            ('out of range', lambda r: r['edits'][0].update(index=229)),
            ('duplicate index', lambda r: r['edits'].append(copy.deepcopy(r['edits'][0]))),
            ('blank after', lambda r: r['edits'][0].update(after='   ')),
            ('no-op', lambda r: r['edits'][0].update(after=r['edits'][0]['before'])),
            ('unknown evidence', lambda r: r['edits'][0].update(evidence_level='certain')),
            ('low confidence', lambda r: r['edits'][0].update(confidence='low')),
            ('missing evidence', lambda r: r['edits'][0].update(evidence='')),
            ('pending edited segment', lambda r: r['pending_review'][0].update(index=2)),
            ('newline injection', lambda r: r['edits'][0].update(after='\u65b0\u53e5\n999\n00:00:00,000 --> 00:00:01,000')),
        ]
        for label, mutate in mutations:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                report_path, _, request = fixture(tmp)
                before = asset_bytes(report_path.parent)
                mutate(request)
                with self.assertRaises(ValueError):
                    postprocess.run_review(report_path, request)
                self.assertEqual(before, asset_bytes(report_path.parent))
                self.assertEqual([], list(report_path.parent.glob('proofread-*')))

    def test_invalid_overview_ranges_reject(self):
        mutations = [
            lambda r: r['overview']['sections'][0].update(start=-.1),
            lambda r: r['overview']['sections'][0].update(end=99999),
            lambda r: r['overview']['sections'][0].update(start=float('nan')),
            lambda r: r['overview']['sections'][1].update(start=9),
            lambda r: r['overview'].update(sections=[]),
            lambda r: r['overview'].update(sections=['not an object']),
            lambda r: r['overview'].update(limitations=[]),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate), tempfile.TemporaryDirectory() as tmp:
                report_path, _, request = fixture(tmp)
                mutate(request)
                with self.assertRaises(ValueError):
                    postprocess.run_review(report_path, request)
                self.assertFalse((report_path.parent / 'review-latest.json').exists())

    def test_nonobject_request_is_rejected_without_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path, _, _ = fixture(tmp)
            before = asset_bytes(report_path.parent)
            for invalid in (None, [], 'invalid review'):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    postprocess.run_review(report_path, invalid)
            self.assertEqual(before, asset_bytes(report_path.parent))
            self.assertEqual([], list(report_path.parent.glob('proofread-*')))

    def test_unfinished_and_missing_sources_are_rejected(self):
        for case in ['unfinished', 'missing asset']:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                report_path, _, request = fixture(tmp)
                if case == 'unfinished':
                    report = json.loads(report_path.read_text('utf-8'))
                    report['status'] = 'running'
                    report_path.write_text(json.dumps(report), encoding='utf-8')
                else:
                    (report_path.parent / 'transcript.srt').unlink()
                with self.assertRaises(ValueError):
                    postprocess.run_review(report_path, request)
                self.assertEqual([], list(report_path.parent.glob('proofread-*')))

    def test_source_change_during_export_does_not_publish_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path, _, request = fixture(tmp)
            original_write = Path.write_text
            triggered = False

            def mutate_during_export(path, text, *args, **kwargs):
                nonlocal triggered
                result = original_write(path, text, *args, **kwargs)
                if path.name == 'corrected_transcript.md' and not triggered:
                    triggered = True
                    original_write(report_path.parent / 'transcript.txt', 'concurrent source writer', encoding='utf-8')
                return result

            with patch.object(Path, 'write_text', mutate_during_export):
                with self.assertRaisesRegex(RuntimeError, 'Source changed'):
                    postprocess.run_review(report_path, request)
            self.assertTrue(triggered)
            self.assertFalse((report_path.parent / 'review-latest.json').exists())
            self.assertEqual([], list(report_path.parent.glob('proofread-*/review.json')))

    def test_linked_original_asset_and_linked_report_input_are_rejected(self):
        for kind in ['asset', 'report']:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                report_path, _, request = fixture(tmp)
                if kind == 'asset':
                    linked = report_path.parent / 'transcript.txt'
                    target = Path(tmp) / 'external-transcript.txt'
                    linked.replace(target)
                else:
                    alias = Path(tmp) / 'alias'
                    alias.mkdir()
                    linked = alias / 'report.json'
                    target = report_path
                try:
                    linked.symlink_to(target)
                except OSError as exc:
                    self.skipTest(f'Symlink privilege unavailable: {exc}')
                with self.assertRaises(ValueError):
                    postprocess.run_review(linked if kind == 'report' else report_path, request)
                self.assertFalse((report_path.parent / 'review-latest.json').exists())


if __name__ == '__main__':
    unittest.main()
