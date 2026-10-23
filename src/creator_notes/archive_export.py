"""Export verified, already-collected public content without copying runtime state."""
from __future__ import annotations

import copy
import csv
from datetime import datetime, timezone
import hashlib
import html
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import zipfile
import uuid

from .archive import _complete, _coverage, _profile, _work
from .engine import valid_id


METRICS = ('like', 'comment', 'collect', 'share')
ORIGINALS = ('report.md', 'segments.json', 'transcript.txt', 'transcript.srt', 'report.json')
REVIEW_FILES = ('corrected_transcript.md', 'corrected_transcript.txt', 'corrected_transcript.srt',
                'corrected_segments.json', 'corrections.md', 'overview.md')
RAW_NAMES = {'report.md': 'raw.md', 'segments.json': 'segments.json',
             'transcript.txt': 'raw.txt', 'transcript.srt': 'raw.srt'}
SAFE_ID = re.compile(r'[0-9]{1,32}')
SAFE_CHECK = re.compile(r'[a-f0-9]{32}')
SAFE_HASH = re.compile(r'[a-f0-9]{64}')
SAFE_REL = re.compile(r'(?:works/[0-9]{1,32}/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+|index\.html|inventory\.csv|content-manifest\.json|SHA256SUMS\.txt|README\.md)')
MAX_PORTABLE_BYTES = 512 * 1024 * 1024
MAX_WORKS = 10000


def safe_directory(path, base):
    """Create only ordinary directories inside the configured output root."""
    path, base = Path(path), Path(base).resolve()
    if not path.resolve().is_relative_to(base):
        raise ValueError('Artifact directory is outside the configured output root')
    for parent in (path, *path.parents):
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError('Linked artifact directories are not accepted')
        if parent.resolve() == base:
            break
    path.mkdir(parents=True, exist_ok=True)
    return path


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    path = Path(path)
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('A content metadata file exceeds the export limit')
    return json.loads(path.read_text(encoding='utf-8'))


def owned(path, base, *, basename=None):
    candidate = Path(path)
    expected = Path(base).resolve()
    resolved = candidate.resolve()
    if not candidate.is_file() or (basename is not None and candidate.name != basename):
        raise ValueError('An expected owned content file is missing')
    if resolved != expected and expected not in resolved.parents:
        raise ValueError('Content source is outside its application-owned directory')
    for part in (candidate, *candidate.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Linked content inputs are not accepted')
        if part.resolve() == expected:
            break
    return resolved


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        return result.isoformat() if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def number(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def observation_time(value, duration):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or value < 0 or not isinstance(duration, (int, float)) or value > duration + .5):
        raise ValueError('A visual observation has an invalid video timestamp')
    return float(value)


def string(value, maximum=32768):
    return value[:maximum] if isinstance(value, str) else ''


def relative(value):
    if not isinstance(value, str) or not SAFE_REL.fullmatch(value) or '..' in value.split('/'):
        raise ValueError('Invalid relative artifact link')
    return value


def write_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == data:
        return
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def text_bytes(value):
    return value.encode('utf-8')


def json_bytes(value):
    return text_bytes(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


STYLE = '''
:root{color-scheme:light;font-family:Segoe UI,Microsoft YaHei,Arial,sans-serif;color:#162437;background:#f1f4f8}
*{box-sizing:border-box}body{margin:0}header{padding:32px max(24px,calc((100vw - 1460px)/2));background:#102a43;color:white}
h1{font-size:30px;letter-spacing:-.6px;margin:0 0 10px}p{line-height:1.6}header p{color:#d4e2ef;margin:0;max-width:1000px}
main{max-width:1508px;margin:auto;padding:24px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:20px}
.card,.panel{background:white;border:1px solid #d9e2ec;border-radius:10px;padding:18px}.card strong{font-size:25px;display:block}.muted{color:#526579;font-size:13px}
.controls{display:flex;gap:12px;flex-wrap:wrap;align-items:end}label{display:flex;flex-direction:column;gap:5px;font-size:13px;font-weight:600}
input,select,button{font:inherit;border:1px solid #aabac9;border-radius:6px;padding:9px;background:white;color:#162437}input[type=search]{min-width:300px}
input[type=number]{width:110px}button{cursor:pointer}a{color:#155e91;text-underline-offset:3px}header a{color:#c9edff}
.table-wrap{overflow:auto;margin-top:16px;background:white;border:1px solid #d9e2ec;border-radius:10px}table{border-collapse:collapse;width:100%;font-size:13px}
th{background:#e8eef5;text-align:left;white-space:nowrap;padding:12px;position:sticky;top:0}td{border-top:1px solid #e2e8f0;padding:12px;vertical-align:top}td:first-child{min-width:165px}
td.title{min-width:300px;max-width:600px;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.55}td.title a{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.metric{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.badge{display:inline-block;padding:4px 7px;border-radius:5px;background:#e6edf5;white-space:nowrap}.reviewed,.images_ready{background:#dff3e8;color:#1a5d3d}.no_speech{background:#e2edf9;color:#204f78;white-space:normal;max-width:190px}
.reviewed_with_pending{background:#fff0ca;color:#775600}.pending,.raw_ready{background:#e9edf3}.failed{background:#fbe3e3;color:#8c2525}.actions{display:flex;gap:7px;flex-wrap:wrap;min-width:140px}
.notice{border-left:4px solid #c18619;padding:12px 16px;background:#fff8e6;margin:16px 0}.good{border-left-color:#238255;background:#ebf8f0}
.quality-warning{font-size:12px;line-height:1.5;max-width:230px;padding:7px;border-left:3px solid #b24d20;background:#fff0e7;color:#773717}
details{margin:14px 0}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65;background:white;padding:18px;border:1px solid #d9e2ec;border-radius:8px}
.gallery{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}.gallery img{max-width:100%;max-height:640px;object-fit:contain;background:white;border:1px solid #d9e2ec}
video{max-width:100%;max-height:600px}.spaced{margin-top:16px}footer{padding:20px 0;color:#526579;font-size:13px}@media(max-width:600px){main{padding:12px}header{padding:24px 16px}input[type=search]{min-width:220px}h1{font-size:25px}}
'''


def document(title, content, script=''):
    return text_bytes('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{html.escape(title)}</title><style>{STYLE}</style></head><body>{content}{script}</body></html>')


def index_html(records, summary, portable=False):
    payload = json.dumps(records, ensure_ascii=False).replace('<', '\\u003c').replace('&', '\\u0026').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    cards = ''.join(f'<div class="card"><strong>{summary[key]}</strong><span class="muted">{label}</span></div>' for key, label in
        [('total_works', 'Public works in verified inventory'), ('raw_transcripts', 'Raw transcripts available'),
         ('reviewed_videos', 'Reviewed speech transcripts'), ('no_speech_videos', 'Videos with no deliverable speech recognized'),
         ('visual_video_overviews', 'Video visual overviews'), ('speech_quality_flagged_videos', 'Videos with speech quality warnings'),
         ('image_posts_ready', 'Image posts captured'), ('unprocessed_videos', 'Videos awaiting content processing')])
    if portable:
        message = ('Text and images companion. Original MP4 files are excluded; open the primary local folder for those videos. '
                   + ('All content processing is complete.' if summary['content_processing_complete'] else 'Content processing is still partial.'))
    else:
        message = ('All inventoried content deliveries are available in this folder.' if summary['delivery_complete'] else
                   'Partial folder export: some requested content is still pending. No transcript or overview is fabricated.')
    controls = '''<section class="panel"><div class="controls">
<label>Search title or ID<input id="search" type="search" placeholder="Search public works…"></label>
<label>Type<select id="kind"><option value="">All works</option><option value="video">Videos</option><option value="image">Image posts</option></select></label>
<label>Status<select id="status"><option value="">All statuses</option><option value="reviewed">Reviewed</option><option value="reviewed_with_pending">Reviewed with pending wording</option><option value="raw_ready">Raw ready; review pending</option><option value="no_speech">No deliverable speech recognized</option><option value="pending">Pending</option><option value="images_ready">Images ready</option><option value="failed">Incomplete / failed</option></select></label>
<label>Sort<select id="sort"><option value="date-desc">Newest first</option><option value="date-asc">Oldest first</option><option value="like">Most likes</option><option value="comment">Most comments</option><option value="collect">Most saves</option><option value="share">Most shares</option><option value="status">Status</option></select></label>
<button id="reset" type="button">Reset</button></div><details><summary>Date and minimum-count filters</summary><div class="controls spaced">
<label>Published from<input id="from" type="date"></label><label>Published through<input id="through" type="date"></label>
<label>Minimum likes<input id="min-like" type="number" min="0"></label><label>Minimum comments<input id="min-comment" type="number" min="0"></label>
<label>Minimum saves<input id="min-collect" type="number" min="0"></label><label>Minimum shares<input id="min-share" type="number" min="0"></label>
</div></details><p id="result-count" class="muted" aria-live="polite"></p></section>'''
    download = 'Portable text and images edition · ' if portable else '<a href="text-and-images.zip">Portable text and images ZIP</a> · '
    body = ('<header><h1>Public Creator Content Archive</h1><p>Verified public-work inventory, interaction counts, original timestamped transcripts, reviewed editions, and captured images.</p>'
            f'<p>{download}<a href="inventory.csv">Spreadsheet CSV</a> · <a href="content-manifest.json">Content manifest</a> · <a href="README.md">Read me</a></p></header>'
            f'<main><div class="cards">{cards}</div><div class="notice {"good" if summary["delivery_complete"] else ""}">{html.escape(message)}<br>'
            f'Export generated: {html.escape(summary["generated_at"])}. Counters retain their individual sampling times.</div>{controls}'
            '<div class="table-wrap"><table><thead><tr><th>Published / ID</th><th>Title preview · full text in Details</th><th>Likes</th><th>Comments</th><th>Saves</th><th>Shares</th><th>Status</th><th>Content</th></tr></thead><tbody id="rows"></tbody></table></div>'
            '<noscript><p>Enable JavaScript to search and filter this local index. The CSV and per-work files remain readable.</p></noscript>'
            '<footer>Scope: accessible public posts returned by the verified official profile. Private, deleted, or access-restricted works are outside this inventory. Unknown counts remain unavailable. Reviewed wording can retain explicitly marked uncertainty.</footer></main>')
    script = '<script type="application/json" id="archive-data">' + payload + '</script>' + r'''<script>
'use strict';
const data=JSON.parse(document.getElementById('archive-data').textContent), $=id=>document.getElementById(id), metrics=['like','comment','collect','share'];
const labels={reviewed:'Reviewed',reviewed_with_pending:'Reviewed; wording pending',raw_ready:'Raw ready; review pending',no_speech:'No deliverable speech recognized',pending:'Pending',images_ready:'Images ready',failed:'Incomplete / failed'};
function localLink(href,label){if(!/^works\/[0-9]{1,32}\/(?:[A-Za-z0-9_.-]+\/)*[A-Za-z0-9_.-]+$/.test(href)||href.split('/').includes('..'))throw Error('Invalid local link');const a=document.createElement('a');a.href=href;a.textContent=label;return a;}
function render(){const query=$('search').value.toLocaleLowerCase(),kind=$('kind').value,status=$('status').value;
let rows=data.filter(w=>(!query||(w.title+' '+w.video_id).toLocaleLowerCase().includes(query))&&(!kind||w.kind===kind)&&(!status||w.status===status)&&(!$('from').value||(w.published_at||'').slice(0,10)>=$('from').value)&&(!$('through').value||(w.published_at||'').slice(0,10)<=$('through').value)&&metrics.every(m=>$('min-'+m).value===''||(w.statistics[m]!==null&&w.statistics[m]>=Number($('min-'+m).value))));
const sort=$('sort').value;rows.sort((a,b)=>sort==='date-asc'?(a.create_time||0)-(b.create_time||0):sort==='date-desc'?(b.create_time||0)-(a.create_time||0):sort==='status'?a.status.localeCompare(b.status):(b.statistics[sort]??-1)-(a.statistics[sort]??-1));
const fragment=document.createDocumentFragment();for(const w of rows){const tr=document.createElement('tr'),date=document.createElement('td');date.style.whiteSpace='nowrap';date.title=w.published_at||'Unavailable';for(const value of [(w.published_at||'Unavailable').slice(0,10),w.published_at?w.published_at.slice(11):'',w.video_id]){const line=document.createElement('div');line.textContent=value;date.append(line);}tr.append(date);
const title=document.createElement('td');title.className='title';const original=document.createElement('a');original.href='https://www.douyin.com/video/'+w.video_id;original.target='_blank';original.rel='noopener noreferrer';original.textContent=w.title||w.video_id;original.title=w.title||w.video_id;title.append(original);tr.append(title);
for(const m of metrics){const td=document.createElement('td');td.className='metric';td.textContent=w.statistics[m]===null?'Unavailable':w.statistics[m].toLocaleString('en-US');tr.append(td);}
const state=document.createElement('td'),badge=document.createElement('span');badge.className='badge '+w.status;badge.textContent=labels[w.status]||w.status;state.append(badge);if(w.pending_segments){const p=document.createElement('p');p.className='muted';p.textContent=w.pending_segments+' segments require wording review';state.append(p);}if(Array.isArray(w.speech_quality_notes)&&w.speech_quality_notes.length){const warning=document.createElement('p');warning.className='quality-warning';warning.textContent='Speech quality warning: '+w.speech_quality_notes.map(n=>n.summary).join(' ');state.append(warning);}tr.append(state);
const actions=document.createElement('td'),links=document.createElement('div');links.className='actions';links.append(localLink(w.links.details,'Details'));for(const [key,label] of [['raw_md','Raw'],['corrected_md','Corrected'],['overview','Overview'],['image_overview','Image overview'],['visual_overview','Visual overview'],['video_original','Video file']])if(w.links[key])links.append(localLink(w.links[key],label));actions.append(links);tr.append(actions);fragment.append(tr);}
$('rows').replaceChildren(fragment);$('result-count').textContent='Showing '+rows.length+' of '+data.length+' inventoried works. Dates include their source timezone; metric sampling times are on each details page.';}
for(const id of ['search','kind','status','sort','from','through',...metrics.map(m=>'min-'+m)])$(id).addEventListener('input',render);
$('reset').addEventListener('click',()=>{for(const id of ['search','kind','status','from','through',...metrics.map(m=>'min-'+m)])$(id).value='';$('sort').value='date-desc';render();});render();
</script>'''
    return document('Public Creator Content Archive', body, script)


def work_html(record, contents):
    def link(path, label):
        return f'<a href="{html.escape(os.path.relpath(relative(path), f"works/{record["video_id"]}").replace(os.sep, "/"), quote=True)}">{html.escape(label)}</a>'
    links = ' · '.join(link(value, key.replace('_', ' ').title()) for key, value in record['links'].items() if key != 'details')
    metrics = '; '.join(f'{label}: {record["statistics"][key] if record["statistics"][key] is not None else "Unavailable"}'
                        for key, label in zip(METRICS, ('Likes', 'Comments', 'Saves', 'Shares')))
    body = f'<header><h1>{html.escape(record["title"] or record["video_id"])}</h1><p><a href="../../index.html">Archive index</a> · <a href="{record["url"]}">Original public post</a></p></header><main>'
    body += f'<section class="panel"><p>Published: {html.escape(record.get("published_at") or "Unavailable")}<br>Counts sampled: {html.escape(record.get("sampled_at") or "Unavailable")}<br>{html.escape(metrics)}<br>Status: {html.escape(record["status"])}</p><p>{links}</p></section>'
    if record['status'] == 'pending':
        body += '<p class="notice">This work is inventoried. Its transcript and review have not yet been delivered.</p>'
    if record['status'] == 'no_speech':
        body += '<p class="notice">No deliverable speech was recognized by the local ASR pass. This does not prove absolute silence. No raw or corrected speech transcript is invented; any visual observations describe sampled frames only.</p>'
        if not record.get('visual_content_ready'):
            body += '<p class="notice">Verified media and sampled-frame observations are still pending.</p>'
        elif not record['links'].get('video_original'):
            body += '<p class="notice">This companion contains sampled frames and visual observations. Open the primary local folder for the original video file.</p>'
    if record.get('pending_segments'):
        body += f'<p class="notice">{record["pending_segments"]} segments retain uncertain wording. The corrections file identifies those segments.</p>'
    for note in record.get('speech_quality_notes', []):
        body += f'<div class="notice quality-warning"><strong>Speech quality warning</strong><br>{html.escape(note["summary"])}<br><span class="muted">Issue: {html.escape(note["issue_code"])}</span></div>'
    for key, label in [('overview', 'Content overview'), ('image_overview', 'Image observations'), ('visual_overview', 'Sampled video-frame observations'), ('corrected_md', 'Reviewed timestamped transcript'), ('raw_md', 'Original timestamped machine transcript'), ('corrections', 'Corrections and pending review')]:
        if record['links'].get(key):
            text = contents[record['links'][key]].decode('utf-8-sig')
            body += f'<details {"open" if key in ("overview", "image_overview", "visual_overview") else ""}><summary>{label}</summary><pre>{html.escape(text)}</pre></details>'
    if record.get('images'):
        body += '<div class="gallery">' + ''.join(f'<a href="{os.path.relpath(image, f"works/{record["video_id"]}").replace(os.sep, "/")}"><img loading="lazy" src="{os.path.relpath(image, f"works/{record["video_id"]}").replace(os.sep, "/")}" alt="Public image {index}"></a>' for index, image in enumerate(record['images'], 1)) + '</div>'
    if record.get('video_frames'):
        body += '<div class="gallery">' + ''.join(f'<figure><a href="{os.path.relpath(frame["path"], f"works/{record["video_id"]}").replace(os.sep, "/")}"><img loading="lazy" src="{os.path.relpath(frame["path"], f"works/{record["video_id"]}").replace(os.sep, "/")}" alt="Sampled video frame at {frame["time_seconds"]:.3f} seconds"></a><figcaption>{frame["time_seconds"]:.3f} seconds</figcaption></figure>' for frame in record['video_frames']) + '</div>'
    if record['links'].get('video_original'):
        media = os.path.relpath(record['links']['video_original'], f'works/{record["video_id"]}').replace(os.sep, '/')
        body += f'<details><summary>Captured original video</summary><video controls preload="none" src="{media}"></video></details>'
    return document('Public work · ' + record['video_id'], body + '</main>')


class ArchiveExporter:
    """Create a new portable snapshot of a persisted public creator archive."""

    def __init__(self, engine, archive_manager=None):
        self.engine = engine
        self.paths = engine.paths
        self.archive_manager = archive_manager
        self.contents = {}
        self.media = {}
        self._portable_bytes = 0
        self._sources = {}

    def _add(self, name, data):
        relative(name)
        self._portable_bytes += len(data) - len(self.contents.get(name, b''))
        if self._portable_bytes > MAX_PORTABLE_BYTES:
            raise ValueError('Portable content exceeds the bounded export size limit')
        self.contents[name] = data

    def _inventory(self, archive_id):
        archive_id = valid_id(archive_id)
        path = owned(self.paths.home / 'archives' / (archive_id + '.json'),
                     self.paths.home / 'archives', basename=archive_id + '.json')
        snapshot = read_json(path)
        if not isinstance(snapshot, dict) or snapshot.get('archive_id') != archive_id:
            raise ValueError('Invalid persisted archive identity')
        if not isinstance(snapshot.get('submissions', {}), dict):
            raise ValueError('Invalid persisted archive submission links')
        profile = _profile(snapshot.get('profile'))
        rows = snapshot.get('works')
        if not isinstance(rows, list) or len(rows) > MAX_WORKS:
            raise ValueError('The persisted archive exceeds the bounded export work limit')
        rows = [_work(row, profile['sec_uid']) for row in rows]
        if len({row['video_id'] for row in rows}) != len(rows):
            raise ValueError('Persisted public works are duplicated')
        return snapshot, profile, rows

    def _report(self, snapshot, work, profile):
        linked = snapshot.get('submissions', {}).get(work['video_id'])
        if not linked:
            return {}
        if not isinstance(linked, dict):
            raise ValueError('Invalid persisted archive check link')
        check_id = valid_id(linked.get('check_id'))
        report = self.engine.report(check_id)
        if not isinstance(report, dict):
            raise ValueError('Invalid persisted native content check')
        public_source = report.get('archive_source', {})
        if not isinstance(public_source, dict):
            raise ValueError('Invalid persisted archive source')
        latest = report.get('latest') or public_source.get('latest', {})
        author = report.get('profile') or public_source.get('profile', {})
        if not isinstance(latest, dict) or not isinstance(author, dict):
            raise ValueError('Invalid persisted public check identity')
        if (not isinstance(report, dict) or report.get('check_id') != check_id
                or report.get('archive_id') != snapshot['archive_id']
                or latest.get('video_id') != work['video_id']
                or author.get('sec_uid') != profile['sec_uid']):
            raise ValueError('A linked check is not bound to this public archive work')
        expected_time = timestamp(work.get('sampled_at')) or timestamp(work.get('collected_at')) or timestamp(snapshot.get('collected_at'))
        reported_time = timestamp(report.get('collected_at')) or timestamp(public_source.get('collected_at'))
        if latest.get('statistics') != work['statistics'] or reported_time != expected_time:
            raise ValueError('Native counters or their sampling time differ from this archive snapshot')
        return dict(report, latest=latest, profile=author)

    def copy(self, source, destination, expected=None):
        destination = relative(destination)
        source = Path(source)
        observed = sha(source)
        if expected is not None and observed != expected:
            raise ValueError('Original content hash changed; export stopped')
        self._sources[source] = observed
        target = self.output / destination
        if target.exists():
            if sha(target) != observed:
                raise ValueError('An existing exported original differs; export stopped without overwriting it')
        else:
            safe_directory(target.parent, self.output)
            temporary = target.with_suffix(target.suffix + '.copying')
            try:
                shutil.copyfile(source, temporary)
                if sha(temporary) != observed:
                    raise ValueError('Content changed while copying; export stopped')
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
        if target.suffix.lower() == '.mp4':
            self.media[destination] = {'sha256': observed, 'bytes': target.stat().st_size}
        else:
            if target.stat().st_size > 64 * 1024 * 1024:
                raise ValueError('An individual portable content file exceeds the export limit')
            self._add(destination, target.read_bytes())
        return destination

    def raw(self, report, record):
        check_id = report.get('check_id')
        if not isinstance(check_id, str) or not SAFE_CHECK.fullmatch(check_id):
            raise ValueError('Invalid source check identifier')
        folder = self.paths.reports / check_id
        files, hashes = report.get('files', {}), report.get('original_hashes', {})
        if set(files) != set(ORIGINALS) or set(hashes) != set(ORIGINALS):
            raise ValueError('Raw source lacks the complete original hash receipt')
        sources = {}
        for name in ORIGINALS:
            if not isinstance(hashes[name], str) or not SAFE_HASH.fullmatch(hashes[name]):
                raise ValueError('Invalid original hash receipt')
            sources[name] = owned(files[name], folder, basename=name)
            if sources[name].parent != folder.resolve() or sha(sources[name]) != hashes[name]:
                raise ValueError('An immutable raw source changed')
            self._sources[sources[name]] = hashes[name]
        if hashes['segments.json'] != report.get('source_sha256'):
            raise ValueError('Raw segment source SHA does not match')
        segments = read_json(sources['segments.json'])
        if not isinstance(segments, list) or not segments:
            raise ValueError('Raw source contains no delivered speech')
        previous_start = -1
        duration = report.get('transcription', {}).get('duration_seconds')
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(duration) or duration <= 0):
            raise ValueError('Raw source has no valid complete audio duration')
        for segment in segments:
            if not isinstance(segment, dict) or not isinstance(segment.get('text'), str):
                raise ValueError('Invalid raw segment')
            if any(isinstance(segment.get(key), bool) or not isinstance(segment.get(key), (float, int))
                   or not math.isfinite(segment[key]) for key in ('start', 'end')) or segment['end'] <= segment['start']:
                raise ValueError('Invalid raw timeline')
            if segment['start'] < max(0, previous_start) or segment['end'] > duration + .5:
                raise ValueError('Raw timeline is outside its complete source audio')
            previous_start = segment['start']
        record.update(status='raw_ready', check_id=check_id, raw_source_sha256=hashes['segments.json'],
                      originals_verified=True, source_original_hashes=hashes,
                      segments=len(segments), characters=number(report.get('transcription', {}).get('characters')),
                      duration_transcribed_seconds=report.get('transcription', {}).get('duration_seconds'))
        base = f'works/{record["video_id"]}/raw-{check_id}'
        for original, exported in RAW_NAMES.items():
            key = {'report.md': 'raw_md', 'segments.json': 'raw_segments', 'transcript.txt': 'raw_txt', 'transcript.srt': 'raw_srt'}[original]
            record['links'][key] = self.copy(sources[original], f'{base}/{exported}', hashes[original])
        return segments

    def reviewed(self, report, record, raw):
        review = report.get('review')
        if (not isinstance(review, dict) or not report.get('delivery_complete') or report.get('complete') is not True
                or review.get('status') not in ('reviewed', 'reviewed_with_pending')):
            return
        revision = review.get('revision')
        if not isinstance(revision, str) or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}', revision):
            return
        if (review.get('source_check_id') != report['check_id'] or review.get('source_video_id') != record['video_id']
                or review.get('source_sha256') != report['source_sha256'] or review.get('originals_preserved') is not True
                or review.get('segment_timestamps_preserved') is not True
                or review.get('original_hashes') != report.get('original_hashes')):
            raise ValueError('Review is not bound to the verified immutable original')
        folder = self.paths.reports / report['check_id'] / ('proofread-' + revision)
        available = review.get('files', {})
        if any(name not in available or not Path(available[name]).is_file() for name in REVIEW_FILES):
            return
        sources = {name: owned(available[name], folder, basename=name) for name in REVIEW_FILES}
        before_hashes = {name: sha(path) for name, path in sources.items()}
        revised = read_json(sources['corrected_segments.json'])
        if not isinstance(revised, list) or len(revised) != len(raw):
            raise ValueError('Reviewed transcript lost original segments')
        if review.get('source_segments') != len(raw) or review.get('corrected_segments') != len(raw):
            raise ValueError('Review segment counts differ from their original source')
        for index, (original, corrected) in enumerate(zip(raw, revised)):
            if corrected.get('source_index') != index or any(corrected.get(key) != original[key] for key in ('start', 'end')):
                raise ValueError('Reviewed segment timestamps or identity changed')
        base = f'works/{record["video_id"]}/review-{revision}'
        keys = {'corrected_transcript.md': 'corrected_md', 'corrected_transcript.txt': 'corrected_txt',
                'corrected_transcript.srt': 'corrected_srt', 'corrected_segments.json': 'corrected_segments',
                'corrections.md': 'corrections', 'overview.md': 'overview'}
        for name, path in sources.items():
            record['links'][keys[name]] = self.copy(path, f'{base}/{name}', before_hashes[name])
        record.update(status='reviewed_with_pending' if review.get('pending_segments') else 'reviewed',
                      reviewed_at=timestamp(review.get('reviewed_at')), edited_segments=number(review.get('edited_segments')),
                      pending_segments=number(review.get('pending_segments')) or 0,
                      audio_checked_segments=number(review.get('audio_checked_segments')) or 0,
                      review_source_sha256=review['source_sha256'], delivery_complete=True)

    def original_media(self, report, record, force_verify=False):
        if not self.include_media and not force_verify:
            return
        if not report or (not report.get('media_checkpoint') and not report.get('transcription', {}).get('reused')):
            return
        from .archive_visual import MediaUnavailable, verified_media
        try:
            supplied = verified_media(self.engine, report)
        except MediaUnavailable:
            record['media_unavailable'] = True
            return
        if supplied is None:
            return
        media, source_check_id, digest = supplied
        media = owned(media, self.paths.work / valid_id(source_check_id))
        if media.suffix.lower() != '.mp4' or not SAFE_HASH.fullmatch(str(digest)) or sha(media) != digest:
            raise ValueError('Verified original media changed before export')
        record['source_media_verified'] = True
        record['source_media_receipt'] = {'sha256': digest, 'bytes': media.stat().st_size}
        self._sources[media] = digest
        if self.include_media:
            record['links']['video_original'] = self.copy(media, f'works/{record["video_id"]}/media/original.mp4', digest)

    def visual_video(self, report, record, supplied):
        """Publish sampled-frame observations independently of speech evidence."""
        if not supplied:
            return
        observation = supplied
        owner = self.paths.reports / 'archives' / self.archive_id / 'visual' / record['video_id']
        if (observation.get('video_id') != record['video_id'] or observation.get('check_id') != report.get('check_id')
                or observation.get('asr_status') != ('no_speech' if record['status'] == 'no_speech' else 'speech')):
            raise ValueError('Video observations do not match the native content check')
        if observation.get('speech_quality') == 'unresolved':
            note = observation.get('quality_note')
            if not isinstance(note, str) or not note.strip() or len(note) > 4096:
                raise ValueError('An explicit unresolved speech quality note is required')
            record['speech_quality_notes'] = [{'video_id': record['video_id'], 'check_id': report['check_id'],
                'issue_code': 'speech_quality_unresolved', 'summary': note}]
        if not record.get('source_media_verified'):
            return
        if observation.get('media_sha256') != record.get('source_media_receipt', {}).get('sha256'):
            raise ValueError('Video observations do not match the verified original media')
        summary = observation.get('summary')
        descriptions = observation.get('observations')
        limitations = observation.get('limitations')
        frames = observation.get('frames')
        if (not isinstance(summary, str) or not summary.strip() or len(summary) > 8192
                or not isinstance(descriptions, list) or not 1 <= len(descriptions) <= 30
                or not isinstance(limitations, list) or not 1 <= len(limitations) <= 20
                or not all(isinstance(item, str) and item.strip() and len(item) <= 4096 for item in limitations)
                or not isinstance(frames, list) or not 1 <= len(frames) <= 12):
            raise ValueError('A no-speech visual overview requires real sampled frames, observations, and limitations')
        duration = record.get('duration_seconds')
        validated = []
        for item in descriptions:
            if not isinstance(item, dict) or not isinstance(item.get('description'), str) or not item['description'].strip() or len(item['description']) > 8192:
                raise ValueError('Invalid sampled-frame observation')
            validated.append((observation_time(item.get('time_seconds'), duration), item['description']))
        base = f'works/{record["video_id"]}/visual-{report["check_id"]}'
        exported = []
        seen = set()
        for index, item in enumerate(frames, 1):
            if not isinstance(item, dict):
                raise ValueError('Invalid sampled video frame')
            seconds = observation_time(item.get('time_seconds'), duration)
            if seconds in seen:
                raise ValueError('Duplicate sampled video frame timestamp')
            seen.add(seconds)
            supplied_path = item.get('path')
            if not isinstance(supplied_path, str):
                raise ValueError('A sampled video frame path is required')
            candidate = Path(supplied_path)
            if not candidate.is_absolute():
                candidate = owner / candidate
            image = owned(candidate, owner)
            if image.suffix.lower() != '.png' or not 0 < image.stat().st_size <= 30 * 1024 * 1024:
                raise ValueError('Only bounded, application-owned PNG video frames are accepted')
            with image.open('rb') as stream:
                if stream.read(8) != b'\x89PNG\r\n\x1a\n':
                    raise ValueError('A video frame is not a PNG image')
            if (not SAFE_HASH.fullmatch(str(item.get('sha256', '')))
                    or sha(image) != item['sha256'] or image.stat().st_size != item.get('bytes')):
                raise ValueError('A sampled frame no longer matches its native hash receipt')
            link = self.copy(image, f'{base}/frame-{index:03d}-{round(seconds * 1000):09d}.png', item['sha256'])
            exported.append({'time_seconds': seconds, 'path': link, 'sha256': sha(image), 'bytes': image.stat().st_size})
        if len(validated) != len(seen) or {seconds for seconds, _ in validated} != seen:
            raise ValueError('Visual descriptions must match every actual sampled frame timestamp')
        lines = ['# Sampled video-frame observations', '', f'Source video: {record["url"]}', '']
        if record['status'] == 'no_speech':
            lines += ['ASR status: No deliverable speech recognized. This does not prove absolute silence.',
                      'No raw or corrected speech transcript has been invented for this work.', '']
        else:
            lines += ['Visual descriptions are separate from the original and reviewed speech transcripts.', '']
        lines += ['## Summary', '', summary, '', '## Observed frames', '']
        lines.extend(f'- At {seconds:.3f} seconds: {description}' for seconds, description in validated)
        lines += ['', '## Limitations', ''] + ['- ' + text for text in limitations]
        lines += ['- These are observations of sampled frames, not a verification of every frame or the entire audio.',
                  '- Quiet, background, sung, or otherwise unrecognized words are not ruled out by the ASR result.',
                  '- The portable text and images companion excludes MP4 files; use the primary local folder for original videos.', '']
        overview = f'{base}/visual_overview.md'
        self._add(overview, text_bytes('\n'.join(lines)))
        record.update(visual_content_ready=True, video_frames=exported,
                      visual_observations=[{'time_seconds': seconds, 'description': description} for seconds, description in validated],
                      visual_limitations=limitations, speech_transcribed=bool(record['links'].get('raw_md')))
        record['links']['visual_overview'] = overview

    def image_post(self, photo, record, overview):
        if not isinstance(photo, dict):
            return
        if photo.get('post_url') != record['url'] or photo.get('content_kind') != 'image_post':
            raise ValueError('Captured images are not bound to this public image post')
        paths = photo.get('images', [])
        if not isinstance(paths, list) or len(paths) > 50:
            raise ValueError('Invalid captured image list')
        images = []
        for index, value in enumerate(paths, 1):
            image = owned(value, self.paths.reports / 'archives' / self.archive_id / 'images' / record['video_id'])
            suffix = image.suffix.lower()
            if suffix not in ('.webp', '.png', '.jpg', '.jpeg') or not 0 < image.stat().st_size <= 30 * 1024 * 1024:
                raise ValueError('Invalid captured public image')
            with image.open('rb') as stream:
                signature = stream.read(16)
            if not (signature.startswith(b'\x89PNG\r\n\x1a\n') or signature.startswith(b'\xff\xd8\xff')
                    or signature.startswith(b'RIFF') and signature[8:12] == b'WEBP'):
                raise ValueError('Captured image has an unexpected file signature')
            images.append(self.copy(image, f'works/{record["video_id"]}/images/image-{index:03d}{suffix}'))
        record['images'] = images
        record['images_captured_at'] = timestamp(photo.get('captured_at'))
        record['image_count'] = len(images)
        record['speech_transcribed'] = False
        if photo.get('complete') is True and photo.get('status') == 'done' and number(photo.get('image_count')) == len(images) and images:
            record.update(status='images_ready', image_capture_complete=True)
        if overview:
            fields = [('summary', 'Summary'), ('observations', 'Visible observations'), ('limitations', 'Limitations')]
            lines = ['# Image-post observations', '', f'Source post: {record["url"]}', '']
            for key, heading in fields:
                value = overview.get(key) if isinstance(overview, dict) else None
                if isinstance(value, str):
                    lines += ['## ' + heading, '', value, '']
                elif isinstance(value, list):
                    lines += ['## ' + heading, ''] + ['- ' + item for item in value if isinstance(item, str)] + ['']
            path = f'works/{record["video_id"]}/images/overview.md'
            self._add(path, text_bytes('\n'.join(lines)))
            write_atomic(self.output / path, self.contents[path])
            record['links']['image_overview'] = path

    def export(self, archive_id, include_media=False):
        """Export a unique snapshot; never trigger collection, ASR, or login."""
        return ArchiveExporter(self.engine, self.archive_manager)._export(archive_id, include_media)

    def _export(self, archive_id, include_media=False):
        if not isinstance(include_media, bool):
            raise ValueError('Include media must be a boolean')
        snapshot, profile, rows = self._inventory(archive_id)
        self.archive_id = valid_id(archive_id)
        self.include_media = include_media
        self.contents, self.media, self._portable_bytes, self._sources = {}, {}, 0, {}
        export_id = uuid.uuid4().hex
        self.output = safe_directory(self.paths.reports / 'archives' / archive_id / 'exports' / export_id,
                                     self.paths.reports)
        records = []
        for source in rows:
            video_id = source['video_id']
            record = dict(source, sampled_at=timestamp(source.get('sampled_at'))
                          or timestamp(source.get('collected_at')) or timestamp(snapshot.get('collected_at')),
                          status='pending', delivery_complete=False, pending_segments=0,
                          visual_content_ready=False, video_content_ready=False,
                          speech_quality_notes=[], links={'details': f'works/{video_id}/index.html'})
            report = self._report(snapshot, source, profile)
            if record['kind'] == 'image':
                folder = self.paths.reports / 'archives' / archive_id / 'images' / video_id
                photo_path = folder / 'photos.json'
                if photo_path.is_file():
                    photo = read_json(owned(photo_path, folder, basename='photos.json'))
                    self.image_post(photo, record, None)
                    record['delivery_complete'] = bool(record.get('image_capture_complete')
                        and all(value is not None for value in record['statistics'].values()))
            elif report.get('files') and report.get('status') in ('done', 'partial'):
                raw = self.raw(report, record)
                self.reviewed(report, record, raw)
            elif report.get('error_code') == 'no_speech' or report.get('status') == 'no_speech':
                record.update(status='no_speech', asr_status='no_speech', check_id=valid_id(report['check_id']),
                              speech_transcribed=False, native_check_status=report['status'])
            elif report and report.get('status') not in ('queued', 'running'):
                record.update(status='failed', incomplete_reason='Native content processing is incomplete')
            if record['kind'] == 'video':
                supplied = None
                if report and record['status'] in ('no_speech', 'raw_ready', 'reviewed', 'reviewed_with_pending'):
                    from .archive_visual import load_video_overview
                    supplied = load_video_overview(self.paths, archive_id, video_id, report['check_id'])
                self.original_media(report, record, force_verify=record['status'] == 'no_speech' or bool(supplied))
                if supplied:
                    self.visual_video(report, record, supplied)
                if record['status'] == 'no_speech':
                    record['delivery_complete'] = bool(record['visual_content_ready']
                        and record['links'].get('video_original')
                        and all(value is not None for value in record['statistics'].values()))
                elif include_media and not record['links'].get('video_original'):
                    record['delivery_complete'] = False
                record['video_content_ready'] = record['delivery_complete']
            records.append(record)
        records.sort(key=lambda item: (item.get('create_time') or 0, item['video_id']), reverse=True)
        reviewed_count = sum(item['status'] in ('reviewed', 'reviewed_with_pending') for item in records)
        videos = sum(item['kind'] == 'video' for item in records)
        images = len(records) - videos
        images_ready = sum(item['status'] == 'images_ready' for item in records)
        visual_count = sum(item['kind'] == 'video' and item['visual_content_ready'] for item in records)
        no_speech_visual_count = sum(item['status'] == 'no_speech' and item['visual_content_ready'] for item in records)
        processed = reviewed_count + no_speech_visual_count
        ready = sum(item['video_content_ready'] for item in records)
        inventory_complete = snapshot.get('inventory_complete') is True and _complete(_coverage(snapshot.get('coverage')), len(records))
        folder_complete = inventory_complete and all(item['delivery_complete'] for item in records)
        summary = {
            'generated_at': datetime.now(timezone.utc).isoformat(), 'total_works': len(records),
            'videos': videos, 'image_posts': images,
            'raw_transcripts': sum(bool(item['links'].get('raw_md')) for item in records),
            'reviewed_videos': reviewed_count, 'image_posts_ready': images_ready,
            'no_speech_videos': sum(item['status'] == 'no_speech' for item in records),
            'visual_video_overviews': visual_count,
            'speech_quality_flagged_videos': sum(bool(item['speech_quality_notes']) for item in records),
            'video_content_ready': ready, 'video_content_processed': processed,
            'unprocessed_videos': videos - processed, 'remaining_videos': videos - ready,
            'captured_images': sum(item.get('image_count', 0) for item in records),
            'sampled_video_frames': sum(len(item.get('video_frames', [])) for item in records),
            'inventory_complete': inventory_complete,
            'content_processing_complete': inventory_complete and processed == videos and images_ready == images,
            'folder_delivery_complete': folder_complete, 'delivery_complete': folder_complete,
            'media_excluded': not include_media, 'delivery_scope': 'local_folder',
            'review_pending_segments': sum(item['pending_segments'] for item in records),
            'original_media_files': len(self.media), 'media_included_in_zip': False,
        }
        for record in records:
            self._add(record['links']['details'], work_html(record, self.contents))
        self._add('index.html', index_html(records, summary))
        self._add('inventory.csv', inventory_csv(records))
        self._add('README.md', readme(summary))
        if any(not path.is_file() or sha(path) != digest for path, digest in self._sources.items()):
            raise ValueError('A verified source changed during archive export')
        for name, data in self.contents.items():
            write_atomic(self.output / relative(name), data)
        metadata = {
            'format_version': 1, 'archive_id': archive_id, 'export_id': export_id,
            'creator': {key: string(profile.get(key), 4096) for key in ('nickname', 'unique_id', 'sec_uid')},
            'scope': 'Accessible public works returned by the verified official creator profile',
            'speech_quality_notes': [note for item in records for note in item['speech_quality_notes']],
            'summary': summary, 'works': records,
        }
        manifest_bytes = json_bytes(dict(metadata, files=receipts(self.contents, self.media)))
        write_atomic(self.output / 'content-manifest.json', manifest_bytes)
        write_atomic(self.output / 'SHA256SUMS.txt', checksum_text(
            dict(self.contents, **{'content-manifest.json': manifest_bytes}), self.media))
        portable = copy.deepcopy(records)
        for record in portable:
            record['links'].pop('video_original', None)
            if record['status'] == 'no_speech':
                record.update(delivery_complete=False, video_content_ready=False)
        portable_summary = dict(summary, original_media_files=0, media_excluded=True,
            delivery_scope='text_and_images_companion', video_content_ready=reviewed_count,
            remaining_videos=videos-reviewed_count, source_folder_delivery_complete=folder_complete,
            folder_delivery_complete=False, delivery_complete=False)
        packed = dict(self.contents)
        for record in portable:
            packed[record['links']['details']] = work_html(record, packed)
        packed['index.html'] = index_html(portable, portable_summary, portable=True)
        packed['inventory.csv'] = inventory_csv(portable)
        packed['README.md'] = readme(portable_summary)
        packed['content-manifest.json'] = json_bytes(dict(metadata, summary=portable_summary,
            works=portable, files=receipts(packed, {})))
        packed['SHA256SUMS.txt'] = checksum_text(packed, {})
        target = self.output / 'text-and-images.zip'
        temporary = self.output / '.text-and-images.zip.tmp'
        try:
            with zipfile.ZipFile(temporary, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for name, data in sorted(packed.items()):
                    archive.writestr(relative(name), data)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        audit(self.output, packed, records, forbidden_paths=(self.paths.home, self.paths.reports, self.paths.session))
        caveats = [
            'Inventory completeness covers accessible public works only; private, deleted, and restricted posts are outside scope.',
            'Completion describes available artifacts and does not assert word-perfect ASR or word-by-word audio verification.',
            'The ZIP is a text and images companion and always excludes original video files.',
        ]
        if summary['review_pending_segments']:
            caveats.append('Uncertain wording remains marked in reviewed editions and overview limitations.')
        if summary['no_speech_videos']:
            caveats.append('No deliverable speech recognized does not prove silence; visual observations describe sampled frames only.')
        result = {'archive_id': archive_id, 'export_id': export_id,
            'export_paths': {'folder': str(self.output), 'index': str(self.output / 'index.html'),
                'manifest': str(self.output / 'content-manifest.json'), 'csv': str(self.output / 'inventory.csv'),
                'companion_zip': str(target)},
            'summary': summary, 'quality_caveats': caveats,
            'zip_bytes': target.stat().st_size, 'zip_sha256': sha(target), 'validation': 'passed'}
        write_atomic(self.output / 'export-status.json', json_bytes({
            key: value for key, value in result.items() if key != 'export_paths'}))
        return result


def inventory_csv(records):
    def cell(value):
        if isinstance(value, str) and (value.startswith(('\t', '\r', '\n'))
                or value.lstrip(' \t\r\n').startswith(('=', '+', '-', '@'))):
            return "'" + value
        return value
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['Video ID (text)', 'Published at (ISO)', 'Title', 'Kind', 'Likes', 'Comments', 'Saves', 'Shares',
                     'Counts sampled at (ISO)', 'Status', 'Pending wording segments', 'Original post',
                     'Details', 'Raw transcript', 'Corrected transcript', 'Overview', 'Visual video overview',
                     'Image count', 'Sampled video frames', 'Original video file', 'Video content ready', 'Speech quality issue codes', 'Speech quality warning'])
    for row in records:
        title = row['title']
        if title.startswith(('=', '+', '-', '@', '\t', '\r')):
            title = "'" + title
        values = ["'" + row['video_id'], row['published_at'], title, row['kind'],
                         *(row['statistics'][key] for key in METRICS), row['sampled_at'], row['status'],
                         row.get('pending_segments', 0), row['url'], row['links']['details'], row['links'].get('raw_md'),
                         row['links'].get('corrected_md'), row['links'].get('overview'), row['links'].get('visual_overview'),
                         row.get('image_count', 0), len(row.get('video_frames', [])),
                         row['links'].get('video_original'), str(row.get('video_content_ready', False)).lower(),
                         '; '.join(note['issue_code'] for note in row.get('speech_quality_notes', [])),
                         ' '.join(note['summary'] for note in row.get('speech_quality_notes', []))]
        writer.writerow([cell(value) for value in values])
    return b'\xef\xbb\xbf' + output.getvalue().encode('utf-8')


def readme(summary):
    return text_bytes(f'''# Public creator content archive

Open `index.html` in a browser. It works offline and supports title/ID search,
type/status/date filters, minimum interaction counts, and date/count/status sorting.

- Inventoried public works: {summary['total_works']}
- Raw timestamped video transcripts: {summary['raw_transcripts']} / {summary['videos']}
- Reviewed speech transcripts: {summary['reviewed_videos']} / {summary['videos']}
- Videos with no deliverable speech recognized: {summary['no_speech_videos']}
- Sampled-frame visual video overviews: {summary['visual_video_overviews']}
- Videos with explicit speech quality warnings: {summary['speech_quality_flagged_videos']}
- Video content ready in this delivery: {summary['video_content_ready']} / {summary['videos']}
- Captured image posts: {summary['image_posts_ready']} / {summary['image_posts']} ({summary['captured_images']} images)
- Content processing complete: {str(summary['content_processing_complete']).lower()}
- Primary-folder delivery complete: {str(summary['folder_delivery_complete']).lower()}
- Delivery scope: {summary['delivery_scope']}
- Original media excluded from this package: {str(summary['media_excluded']).lower()}
- Export generated: {summary['generated_at']}

The index preserves original public titles and speech. English labels describe
the export. CSV uses UTF-8 with a BOM and a leading apostrophe on long numeric IDs
to keep them as text in spreadsheet applications. Dates retain ISO timezones.

Delivered speech originals, when present, were verified against all five original hash receipts, including
the original report JSON, which is deliberately excluded from this distribution.
Reviewed editions require a real completed review, available outputs, and exact
preservation of every original segment and timestamp. Explicit uncertain wording
remains marked for review. A machine transcript alone is not a reviewed delivery.
Image posts contain downloaded public images; no speech transcript is invented.
Videos whose native ASR check returned `no_speech` retain that explicit status.
Their delivered content uses verified original media and actual sampled-frame
observations, with limitations. This status does not prove complete silence, and
it never creates placeholder raw/corrected speech transcripts or claims every
video has a transcript. Sampled frames do not verify the entire video or audio.

Collection, content-processing, or folder-delivery completion describes the
availability of the requested artifacts. It does NOT assert word-complete,
reliable speech recognition for clips with explicit speech quality warnings.
Those clips retain their original machine transcripts and unresolved language
or sparse-speech issues. Read their warnings in the index/details and the
sanitized speech quality notes in the manifest; do not treat a reviewed file's
existence as proof that every spoken word or language identification is correct.

`content-manifest.json` contains sanitized metadata, relative content links,
and SHA-256 receipts. No session state, cookies, models, logs, or source report/review
JSON is included. The portable ZIP excludes original video media. A folder export
created with `include_media=true` contains available verified original MP4 files.
The ZIP is a text and images companion, not a self-contained original-video
delivery. Open the PRIMARY LOCAL FOLDER for original videos, including videos
with no deliverable recognized speech. Its frame overviews and original-media
hash receipts remain available in the companion without claiming its MP4s exist.

Scope is accessible public works returned by the verified official profile.
Private, deleted, and restricted works are outside that inventory. Interaction
counts reflect each work's recorded sampling time, rather than a live counter.
Speaker statements and image observations are not independent factual verification.
''')


def receipts(contents, media):
    result = {name: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)} for name, data in sorted(contents.items())}
    result.update(media)
    return result


def checksum_text(contents, media):
    return text_bytes(''.join(f'{item["sha256"]}  {name}\n' for name, item in sorted(receipts(contents, media).items())))


def audit(output, packed, records, forbidden_paths=()):
    for name in packed:
        relative(name)
        if Path(name).name in ('report.json', 'review.json', 'storage-state.json') or Path(name).suffix.lower() in ('.mp4', '.sqlite3', '.log'):
            raise ValueError('Private or excluded content reached the portable ZIP')
        for path in forbidden_paths:
            raw = str(path)
            if len(raw) >= 8 and any(value.encode('utf-8') in packed[name]
                    for value in (raw, raw.replace('\\', '\\\\'), raw.replace('\\', '/'))):
                raise ValueError('A private runtime path reached the portable archive')
    for record in records:
        for link in list(record['links'].values()) + record.get('images', []) + [frame['path'] for frame in record.get('video_frames', [])]:
            if not (output / relative(link)).is_file():
                raise ValueError('An exported local content link is missing')
            if not link.endswith('.mp4') and link not in packed:
                raise ValueError('A portable content link is missing')
    with zipfile.ZipFile(output / 'text-and-images.zip') as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(packed):
            raise ValueError('Portable archive verification failed')
        for name, data in packed.items():
            if hashlib.sha256(archive.read(name)).digest() != hashlib.sha256(data).digest():
                raise ValueError('Portable archive content hash changed')
    for name, data in packed.items():
        if name.endswith('.html'):
            for href in re.findall(r'(?:href|src)="([^"]+)"', data.decode('utf-8')):
                if href.startswith('https://www.douyin.com/video/') or href.startswith('#'):
                    continue
                resolved = (Path(name).parent / href).as_posix()
                normalized = os.path.normpath(resolved).replace(os.sep, '/')
                if normalized not in packed:
                    raise ValueError('A portable HTML link has no included target')
