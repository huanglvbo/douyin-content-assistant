"""Console interface for installation diagnostics and explicit local operations."""
from __future__ import annotations

import argparse
from importlib import metadata
import json
from pathlib import Path
import platform
import sys
import time

from . import __version__
from .paths import AppPaths


def doctor():
    versions = {}
    for package in ('creator-notes', 'mcp', 'playwright', 'faster-whisper', 'httpx'):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    browser_installed = False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as browser:
            browser_installed = Path(browser.chromium.executable_path).is_file()
    except (ImportError, OSError):
        pass
    paths = AppPaths.current()
    from .recognition import model_inventory
    cached_models = model_inventory(paths.models)
    return {'version': __version__, 'python': platform.python_version(), 'dependencies': versions,
            'chromium_installed': browser_installed, 'data_home': str(paths.home),
            'models': str(paths.models), 'reports': str(paths.reports),
            'session_saved': (paths.session / 'storage-state.json').is_file(),
            'cached_speech_models': cached_models,
            'first_transcription_requires_model_download': not bool(cached_models),
            'ready_for_login': all(versions.values()) and browser_installed,
            'workbuddy_acceptance': 'not_run', 'upstream_application_required': False}


def parser():
    result = argparse.ArgumentParser(prog='creator-notes', description='Local creator checks and reviewed timestamped speech')
    sub = result.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor')
    sub.add_parser('mcp')
    login = sub.add_parser('login')
    login.add_argument('--headless', action='store_true', help='Use a QR-only helper; sliders require a visible retry')
    sub.add_parser('login-status')
    check = sub.add_parser('check')
    check.add_argument('creator')
    check.add_argument('--profile', choices=('fast', 'balanced', 'accurate'), default='balanced')
    check.add_argument('--refresh-identity', action='store_true')
    check.add_argument('--wait', action='store_true', help='Compatibility option; CLI always retains its worker until completion')
    report = sub.add_parser('report')
    report.add_argument('check_id')
    resume = sub.add_parser('resume')
    resume.add_argument('check_id')
    resume.add_argument('--profile', choices=('fast', 'balanced', 'accurate'))
    transcript = sub.add_parser('transcript')
    transcript.add_argument('check_id')
    transcript.add_argument('--offset', type=int, default=0)
    transcript.add_argument('--limit', type=int, default=80)
    transcribe = sub.add_parser('transcribe')
    transcribe.add_argument('source', help='Explicitly selected local audio/video path')
    transcribe.add_argument('--model', choices=('base', 'small', 'medium'), default='small')
    transcribe.add_argument('--output', required=True, help='New JSON output file; never overwrite')
    review = sub.add_parser('review')
    review.add_argument('check_id')
    review.add_argument('--request', required=True)
    prepare = sub.add_parser('prepare-model')
    prepare.add_argument('--model', choices=('base', 'small', 'medium'), default='small')
    return result


def main():
    args = parser().parse_args()
    try:
        if args.command == 'mcp':
            from .server import main as serve
            return serve()
        if args.command == 'doctor':
            output = doctor()
        elif args.command == 'prepare-model':
            from .recognition import ensure_model_cache
            paths = AppPaths.current().prepare()
            selection = ensure_model_cache(paths.models, args.model, prefer_cached=False)
            output = {'status': 'model_ready', 'model': args.model, 'path': selection['cache_path']}
        elif args.command in ('login', 'login-status'):
            from .login import begin_login, login_status
            paths = AppPaths.current().prepare()
            output = (begin_login(paths.session, verification_window=not args.headless)
                      if args.command == 'login' else login_status(paths.session))
        elif args.command == 'transcribe':
            from .recognition import transcribe_file
            destination = Path(args.output).expanduser().resolve()
            if destination.exists():
                raise ValueError('Output already exists; choose a new file')
            source = Path(args.source).expanduser().resolve(strict=True)
            output = transcribe_file(source, AppPaths.current().prepare().models, model=args.model)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open('x', encoding='utf-8') as handle:
                json.dump(output, handle, ensure_ascii=False, indent=2)
            output = {'status': 'done', 'file': str(destination), 'segments': len(output['segments']),
                      'duration_seconds': output['duration'], 'model': output['model']}
        else:
            from .engine import Manager
            manager = Manager()
            try:
                if args.command == 'check':
                    job = manager.submit(args.creator, args.profile, args.refresh_identity)
                    last_stage = None
                    while job['status'] in ('queued', 'running'):
                        if job.get('stage') != last_stage:
                            print(job.get('stage', ''), file=sys.stderr, flush=True)
                            last_stage = job.get('stage')
                        time.sleep(.5)
                        job = manager.report(job['check_id'])
                    output = job
                elif args.command == 'report':
                    output = manager.report(args.check_id)
                elif args.command == 'resume':
                    job = manager.resume(args.check_id, args.profile)
                    while job['status'] in ('queued', 'running'):
                        time.sleep(.5)
                        job = manager.report(job['check_id'])
                    output = job
                elif args.command == 'transcript':
                    output = manager.transcript(args.check_id, args.offset, args.limit)
                elif args.command == 'review':
                    request = json.loads(Path(args.request).read_text(encoding='utf-8-sig'))
                    output = manager.review(args.check_id, request)
            finally:
                manager.close()
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        # Detailed network errors can contain private media URLs. Report safe codes.
        print(json.dumps({'status': 'error', 'error_code': getattr(exc, 'code', type(exc).__name__)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
