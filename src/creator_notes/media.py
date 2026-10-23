"""Acquire one observed Douyin video through a dedicated browser session.

The browser uses only the caller's dedicated login state. Download addresses
are selected from the requested work's observed response; they are never
manufactured, included in errors, or persisted in reports.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
from pathlib import Path
import re
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit
import uuid


MAX_MEDIA_BYTES = 512 * 1024**2
MAX_RESPONSE_BYTES = 4 * 1024**2
MAX_REDIRECTS = 5
MAX_DOWNLOAD_SECONDS = 10 * 60
MEDIA_HOST_SUFFIXES = (
    'douyin.com', 'douyinvod.com', 'douyincdn.com', 'bytecdn.cn',
    'bytecdn.com', 'ibytedtos.com', 'byteimg.com', 'snssdk.com',
)


class MediaError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _notify(progress, stage: str, fraction: float | None = None):
    if progress is not None and progress(stage, fraction) is False:
        raise MediaError('cancelled', 'Video acquisition was cancelled.')


def _video_id(url: str) -> str:
    if not isinstance(url, str):
        raise MediaError('invalid_video_url', 'Provide the canonical URL of a public Douyin video.')
    match = re.fullmatch(r'https://www\.douyin\.com/video/([0-9]{5,30})/?', url)
    if not match:
        raise MediaError('invalid_video_url', 'Provide the canonical URL of a public Douyin video.')
    return match.group(1)


def _allowed_host(host: str) -> bool:
    return any(host == suffix or host.endswith('.' + suffix) for suffix in MEDIA_HOST_SUFFIXES)


def _public_target(url: str) -> tuple[str, str, list[str]]:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').encode('idna').decode('ascii').lower()
        if (parsed.scheme != 'https' or not host or not _allowed_host(host)
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.fragment):
            raise ValueError
        addresses = list(dict.fromkeys(
            item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ))
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError
        target = parsed.path or '/'
        if parsed.query:
            target += '?' + parsed.query
        return host, target, addresses
    except (ValueError, UnicodeError, OSError):
        raise MediaError('unsafe_media_address', 'The media address failed safety checks. Download stopped.') from None


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Keep TLS verification on the original host while fixing its public IP."""
    def __init__(self, host: str, address: str):
        super().__init__(host, timeout=25, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        connection = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(connection, server_hostname=self.host)
        except BaseException:
            connection.close()
            raise


def _download(url: str, destination: Path, progress=None):
    current = url
    deadline = time.monotonic() + MAX_DOWNLOAD_SECONDS
    for hop in range(MAX_REDIRECTS + 1):
        if time.monotonic() > deadline:
            raise MediaError('media_timeout', 'The media download timed out. Check your network and retry.')
        host, target, addresses = _public_target(current)
        connection = _PinnedHTTPSConnection(host, addresses[0])
        try:
            connection.request('GET', target, headers={
                'Referer': 'https://www.douyin.com/',
                'User-Agent': 'Mozilla/5.0', 'Accept': 'video/mp4,application/octet-stream;q=0.9,*/*;q=0.5',
            })
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location or hop == MAX_REDIRECTS:
                    raise MediaError('media_redirect', 'The media redirect chain is invalid or too long. Download stopped.')
                current = urljoin(current, location)
                continue
            if response.status != 200:
                raise MediaError('media_unavailable', 'The video media is unavailable. Check access to the work and retry.')
            content_type = (response.getheader('Content-Type') or '').lower()
            if any(kind in content_type for kind in ('text/', 'json', 'html', 'xml')):
                raise MediaError('invalid_media', 'The download response is not a transcribable media file.')
            length = response.getheader('Content-Length')
            try:
                size = int(length) if length is not None else None
            except (TypeError, ValueError):
                raise MediaError('invalid_media', 'The reported media file size is invalid.') from None
            if size is not None and not 0 < size <= MAX_MEDIA_BYTES:
                raise MediaError('media_size', 'The media file is empty or exceeds the 512 MiB limit.')
            downloaded = 0
            with destination.open('xb') as output:
                while True:
                    if time.monotonic() > deadline:
                        raise MediaError('media_timeout', 'The media download timed out. Check your network and retry.')
                    block = response.read(64 * 1024)
                    if not block:
                        break
                    downloaded += len(block)
                    if downloaded > MAX_MEDIA_BYTES:
                        raise MediaError('media_size', 'The media file exceeds the download size limit.')
                    output.write(block)
                    _notify(progress, 'Downloading video audio', downloaded / size if size else None)
            if not downloaded or (size is not None and downloaded != size):
                raise MediaError('incomplete_media', 'The media download is incomplete. Retry the download.')
            return
        except MediaError:
            raise
        except (OSError, http.client.HTTPException, ValueError):
            raise MediaError('media_download_failed', 'The media download did not complete. Check your network and retry.') from None
        finally:
            connection.close()
    raise MediaError('media_redirect', 'The media redirect chain is invalid or too long. Download stopped.')


def _addresses(payload, requested_id: str) -> list[str]:
    """Read only a matching work's muxed MP4 address structures."""
    results = []

    def visit(value, depth=0):
        if depth > 8:
            return
        if isinstance(value, list):
            for item in value[:100]:
                visit(item, depth + 1)
        elif isinstance(value, dict):
            if str(value.get('aweme_id', '')) == requested_id:
                video = value.get('video') or {}
                if not isinstance(video, dict):
                    return
                containers = [video.get('play_addr'), video.get('play_addr_h264'), video.get('download_addr')]
                for bitrate in (video.get('bit_rate') or [])[:20]:
                    if isinstance(bitrate, dict):
                        containers.append(bitrate.get('play_addr'))
                for address in containers:
                    if not isinstance(address, dict) or 'dash' in str(address.get('url_key', '')).lower():
                        continue
                    for url in (address.get('url_list') or [])[:10]:
                        if isinstance(url, str) and url.startswith('https://') and len(url) <= 16_384:
                            if url not in results:
                                results.append(url)
                return
            for key in ('aweme_detail', 'aweme_list', 'data', 'items', 'item_list'):
                if key in value:
                    visit(value[key], depth + 1)

    visit(payload)
    return results[:20]


def _captcha_visible(page) -> bool:
    try:
        matches = page.locator("[id*='captcha'], [class*='captcha'], iframe[src*='captcha']")
        return any(matches.nth(index).is_visible() for index in range(min(matches.count(), 12)))
    except Exception:
        return False


def _has_audio(path: Path) -> bool:
    try:
        import av
        with av.open(str(path)) as container:
            return bool(container.streams.audio)
    except Exception:
        return False


def acquire_video(video_url: str, directory: Path, state_path: Path | None = None, progress=None) -> Path:
    """Download a single public work with an actual audio stream.

    Captcha, absent response metadata, login restrictions, non-public download
    destinations, and incomplete downloads are explicit failure states.
    """
    requested_id = _video_id(video_url)
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if state_path is not None:
        state_path = Path(state_path)
        if state_path.is_symlink() or not state_path.is_file():
            raise MediaError('login_required', 'The dedicated Douyin login state is missing. Sign in with the QR code first.')
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise MediaError('dependency_missing', 'Browser dependencies are missing. Run the installer.') from None
    _notify(progress, 'Opening the requested Douyin video', None)
    observed = []
    temporary = directory / f'{requested_id}-{uuid.uuid4().hex}.partial'
    try:
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(channel='chromium', headless=True)
            try:
                options = {'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36', 'locale': 'zh-CN'}
                if state_path:
                    options['storage_state'] = str(state_path)
                context = browser.new_context(**options)
                page = context.new_page()

                def capture(response):
                    # Other response bodies and other works are not collected.
                    if (urlsplit(response.url).hostname != 'www.douyin.com'
                            or '/aweme/' not in response.url or not response.ok):
                        return
                    try:
                        announced = int(response.headers.get('content-length', '0'))
                        if announced > MAX_RESPONSE_BYTES:
                            return
                        body = response.body()
                        if len(body) > MAX_RESPONSE_BYTES:
                            return
                        for item in _addresses(json.loads(body), requested_id):
                            if item not in observed:
                                observed.append(item)
                    except Exception:
                        return

                page.on('response', capture)
                page.goto(video_url, wait_until='domcontentloaded', timeout=45_000)
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if _captcha_visible(page):
                        raise MediaError('captcha', 'Douyin requires human verification. Complete its normal login process and retry.')
                    if observed:
                        break
                    _notify(progress, 'Waiting for public video media metadata', None)
                    page.wait_for_timeout(500)
                if not observed:
                    raise MediaError('media_not_observed', 'Media metadata for this work was not observed. Login may be required or access may be restricted.')
                last_error = None
                for address in observed:
                    try:
                        _download(address, temporary, progress)
                        if not _has_audio(temporary):
                            raise MediaError('no_audio', 'No transcribable audio track was found in the media.')
                        complete = temporary.with_suffix('.mp4')
                        _notify(progress, 'Video audio is ready', 1.0)
                        temporary.replace(complete)
                        return complete
                    except MediaError as exc:
                        temporary.unlink(missing_ok=True)
                        if exc.code in ('cancelled', 'unsafe_media_address'):
                            raise
                        last_error = exc
                raise last_error or MediaError('media_unavailable', 'No transcribable media file could be acquired.')
            finally:
                browser.close()
    except MediaError:
        raise
    except KeyboardInterrupt:
        raise MediaError('cancelled', 'Video acquisition was cancelled.') from None
    except Exception:
        raise MediaError('browser_failed', 'The browser could not acquire the video. Check browser installation and login status.') from None
    finally:
        temporary.unlink(missing_ok=True)
