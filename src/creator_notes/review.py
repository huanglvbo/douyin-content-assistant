"""Validate host-AI editorial review and export a separate, timestamped edition.

Standard library only. Never calls an external model or edits the ASR originals.
The host AI reads the full source, supplies exact per-segment edits and overview.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import uuid

EVIDENCE = {'user_confirmed', 'metadata_spelling', 'context_inferred', 'audio_checked', 'caption_checked', 'script_normalization'}
ORIGINALS = ('report.json', 'report.md', 'segments.json', 'transcript.txt', 'transcript.srt')


def source_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stamp(value, srt=False):
    ms = round(float(value) * 1000)
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    seconds, ms = divmod(ms, 1000)
    return f'{hours:02}:{minutes:02}:{seconds:02}{"," if srt else "."}{ms:03}'


def cell(value):
    return str(value).replace('|', '\\|').replace('\n', '<br>')


def nonempty(value, label, maximum=12000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError('Invalid ' + label)
    return value.strip()


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def bounds(item, duration):
    values = (item.get('start'), item.get('end'))
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in values):
        raise ValueError('Invalid overview time range')
    start, end = values
    if not 0 <= start < end <= duration + .001:
        raise ValueError('Overview time range exceeds source')
    return start, end


def validate_review(source, request, duration):
    if not isinstance(request, dict):
        raise ValueError('Review must be an object')
    edits, pending = request.get('edits'), request.get('pending_review', [])
    if not isinstance(edits, list) or not isinstance(pending, list):
        raise ValueError('Edits and pending_review must be lists')
    seen = set()
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError('Invalid edit')
        index = edit.get('index')
        if not integer(index) or index < 0 or index >= len(source) or index in seen:
            raise ValueError('Duplicate or invalid edit index')
        seen.add(index)
        if edit.get('before') != source[index]['text']:
            raise ValueError(f'Original text mismatch at segment {index}')
        nonempty(edit.get('after'), 'replacement', 4000)
        if re.search(r'[\x00-\x1f\x7f\u2028\u2029]', edit['after']):
            raise ValueError('Replacement must remain a single subtitle segment')
        if edit['before'] == edit['after']:
            raise ValueError('No-op edit')
        if edit.get('evidence_level') not in EVIDENCE:
            raise ValueError('Unknown evidence level')
        if edit.get('confidence') not in ('high', 'medium'):
            raise ValueError('Low-confidence candidates belong in pending_review')
        nonempty(edit.get('reason'), 'edit reason', 2000)
        nonempty(edit.get('evidence'), 'edit evidence', 3000)
    for item in pending:
        if not isinstance(item, dict) or not integer(item.get('index')) or not 0 <= item['index'] < len(source):
            raise ValueError('Invalid pending-review index')
        if item['index'] in seen:
            raise ValueError('Pending-review segments must retain their original text')
        nonempty(item.get('issue'), 'pending issue', 3000)
    overview = request.get('overview')
    if not isinstance(overview, dict):
        raise ValueError('Missing overview')
    nonempty(overview.get('summary'), 'overview summary')
    sections = overview.get('sections')
    if not isinstance(sections, list) or not sections:
        raise ValueError('Overview needs timestamped sections')
    last = -1
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError('Invalid overview section')
        start, end = bounds(section, duration)
        if start < last:
            raise ValueError('Overview sections must be ordered and non-overlapping')
        last = end
        nonempty(section.get('title'), 'section title', 500)
        nonempty(section.get('summary'), 'section summary')
    limits = overview.get('limitations')
    if not isinstance(limits, list) or not limits or any(not isinstance(x, str) or not x.strip() for x in limits):
        raise ValueError('Overview needs evidence limitations')


def run_review(report_path, request):
    if not isinstance(request, dict):
        raise ValueError('Review must be an object')
    supplied = Path(report_path)
    if supplied.is_symlink():
        raise ValueError('Linked original report is not accepted')
    report_path = supplied.resolve()
    if report_path.name != 'report.json':
        raise ValueError('Select the generated report.json')
    folder = report_path.parent
    paths = {name: folder / name for name in ORIGINALS}
    if any(not path.is_file() or path.is_symlink() for path in paths.values()):
        raise ValueError('Original report assets are missing or linked')
    before_hashes = {name: source_sha256(path) for name, path in paths.items()}
    if request.get('source_sha256') != before_hashes['segments.json']:
        raise ValueError('Stale source SHA256: reread the full original')
    report = json.loads(report_path.read_text(encoding='utf-8-sig'))
    if report.get('status') not in ('done', 'partial'):
        raise ValueError('Source transcription is not finished')
    source = json.loads(paths['segments.json'].read_text(encoding='utf-8-sig'))
    if not isinstance(source, list) or not source:
        raise ValueError('Missing source segments')
    last_start = -1
    for item in source:
        if not isinstance(item, dict) or not isinstance(item.get('text'), str):
            raise ValueError('Invalid source segment')
        bounds(item, float('inf'))
        if item['start'] < last_start:
            raise ValueError('Source segments are out of order')
        last_start = item['start']
    audio_duration = report.get('transcription', {}).get('duration_seconds') or max(item['end'] for item in source)
    if isinstance(audio_duration, bool) or not isinstance(audio_duration, (int, float)) or not math.isfinite(audio_duration) or audio_duration <= 0:
        raise ValueError('Invalid source audio duration')
    duration = max(max(item['end'] for item in source), audio_duration)
    validate_review(source, request, duration)
    reviewer = nonempty(request.get('reviewer', 'Host AI'), 'reviewer', 500)
    changes = {item['index']: item for item in request['edits']}
    corrected = []
    for index, original in enumerate(source):
        edit = changes.get(index)
        # Word timings belong to the original ASR and are not claimed to align
        # with the revised words. Only the original segment boundaries survive.
        item = {'source_index': index, 'start': original['start'], 'end': original['end'],
                'text': edit['after'] if edit else original['text'], 'changed': edit is not None,
                'word_alignment': 'not_retimed'}
        if edit:
            item['evidence_level'] = edit['evidence_level']
        corrected.append(item)
    title = report.get('latest', {}).get('title') or 'Video speech'
    video_url = report.get('latest', {}).get('url') or ''
    has_context = any(e['evidence_level'] not in ('audio_checked', 'caption_checked') for e in request['edits'])
    disclaimer = ('This AI-corrected edition preserves every original segment and timestamp. '
                  'Changes use context, terminology, or user feedback. It has not been checked word by word against the audio; '
                  'uncertain wording is retained. Segment times have not been realigned to revised words.') if has_context else (
                  'This AI-reviewed edition preserves every original segment and timestamp. '
                  'Evidence is listed in the correction table; the machine original is unchanged. '
                  'This does not establish word-by-word audio verification. Revised words have not been realigned.')
    header = f'# Corrected transcript\n\n{title}\n\nSource video: {video_url}\n\n{disclaimer}\n\n'
    timed = '\n'.join(f"[{stamp(s['start'])} → {stamp(s['end'])}] {s['text']}" for s in corrected)
    txt = '\n'.join(s['text'] for s in corrected) + '\n'
    srt = '\n\n'.join(f"{i}\n{stamp(s['start'], True)} --> {stamp(s['end'], True)}\n{s['text']}" for i, s in enumerate(corrected, 1)) + '\n'
    diffs = ['# Corrections and pending review', '', disclaimer, '',
             '| Source index | Time | Original | Corrected | Evidence type | Reason |', '|---:|---|---|---|---|---|']
    for index, edit in sorted(changes.items()):
        when = f"{stamp(source[index]['start'])}–{stamp(source[index]['end'])}"
        diffs.append('| ' + ' | '.join(cell(x) for x in (index, when, edit['before'], edit['after'], edit['evidence_level'], edit['reason'])) + ' |')
    diffs.extend(['', '## Pending review (original wording retained)', ''])
    for item in request.get('pending_review', []):
        index = item['index']
        diffs.extend([f"- Segment {index} [{stamp(source[index]['start'])}–{stamp(source[index]['end'])}]: {source[index]['text']}",
                      f"  - Unresolved issue: {item['issue']}"])
    overview = request['overview']
    overview_lines = ['# Content overview', '', title, '', f'Source video: {video_url}', '',
                      'This summarizes the video. Product capabilities, results, and creator experience are not automatically independently verified facts.', '',
                      '## One-sentence summary', '', overview['summary'], '', '## Chapters', '']
    for section in overview['sections']:
        overview_lines.extend([f"### {stamp(section['start'])}–{stamp(section['end'])} {section['title']}", '', section['summary'], ''])
    overview_lines.extend(['## Evidence limits', ''])
    overview_lines.extend('- ' + item for item in overview['limitations'])
    refs = overview.get('references', [])
    if refs:
        overview_lines.extend(['', '## Terminology references (spelling support, not audio evidence)', ''])
        for item in refs:
            overview_lines.append('- ' + str(item))
    # All semantic and timestamp validation happened before creating a revision.
    revision_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    revision = folder / ('proofread-' + revision_id)
    revision.mkdir()
    outputs = {'corrected_transcript.md': header + timed + '\n', 'corrected_transcript.txt': txt,
               'corrected_transcript.srt': srt, 'corrected_segments.json': json.dumps(corrected, ensure_ascii=False, indent=2),
               'corrections.md': '\n'.join(diffs) + '\n', 'overview.md': '\n'.join(overview_lines) + '\n'}
    for name, content in outputs.items():
        (revision / name).write_text(content, encoding='utf-8')
    if any(source_sha256(paths[name]) != digest for name, digest in before_hashes.items()):
        raise RuntimeError('Source changed while exporting; revision not committed')
    manifest = {'status': 'reviewed_with_pending' if request.get('pending_review') else 'reviewed',
                'revision': revision_id, 'reviewed_at': datetime.now(timezone.utc).isoformat(), 'reviewer': reviewer,
                'source_check_id': report.get('check_id'), 'source_video_id': report.get('latest', {}).get('video_id'),
                'source_report': str(report_path), 'source_sha256': before_hashes['segments.json'], 'original_hashes': before_hashes,
                'originals_preserved': True, 'source_segments': len(source), 'corrected_segments': len(corrected),
                'edited_segments': len(changes), 'pending_segments': len({item['index'] for item in request.get('pending_review', [])}),
                'audio_checked_segments': sum(e['evidence_level'] == 'audio_checked' for e in request['edits']),
                'segment_timestamps_preserved': True, 'word_timestamps_recomputed': False,
                'files': {name: str((revision / name).resolve()) for name in (*outputs, 'review.json')}}
    (revision / 'review.json').write_text(json.dumps({'manifest': manifest, 'request': copy.deepcopy(request)}, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary = folder / ('.review-latest-' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(folder / 'review-latest.json')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, help='Absolute path to the generated report.json')
    parser.add_argument('--review', help='JSON request produced after host-AI review')
    parser.add_argument('--inspect', action='store_true', help='Read full source plus fingerprint; does not modify files')
    args = parser.parse_args()
    report_path = Path(args.report).resolve()
    if args.inspect:
        source_path = report_path.parent / 'segments.json'
        print(json.dumps({'source_sha256': source_sha256(source_path), 'segments': json.loads(source_path.read_text(encoding='utf-8-sig'))}, ensure_ascii=False))
        return
    if not args.review:
        parser.error('--review is required unless --inspect is used')
    request = json.loads(Path(args.review).read_text(encoding='utf-8-sig'))
    print(json.dumps(run_review(report_path, request), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
