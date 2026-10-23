"""Native MCP stdio service. All mutable state belongs to the current user."""
from functools import lru_cache
from mcp.server.fastmcp import FastMCP

from .engine import Manager

mcp = FastMCP('creator-notes', instructions='Collect creator updates and full timestamped speech. Read every transcript page, then use the host AI to review and submit_review. Raw collection is not final delivery. Respect login, captcha, ambiguity, partial metrics and evidence limits.')


@lru_cache(maxsize=1)
def manager():
    return Manager()


@lru_cache(maxsize=1)
def archive_manager():
    from .archive import ArchiveManager
    return ArchiveManager(manager())


@lru_cache(maxsize=1)
def archive_visuals():
    from .archive_visual import ArchiveVisuals
    return ArchiveVisuals(manager(), archive_manager())


@mcp.tool()
def capture_archive_video_frames(archive_id: str, video_id: str, timestamps: list[float] | None = None) -> dict:
    """Sample verified saved video frames for actual host inspection. Defaults to three frames at 20/50/80 percent of actual duration; accepts at most twelve explicit times. Returns frame_set_id, actual frame times, PNG paths and hashes. Sampling does not prove silence or complete speech and never replaces the original ASR."""
    return archive_visuals().capture(archive_id, video_id, timestamps)


@mcp.tool()
def submit_archive_video_overview(archive_id: str, video_id: str, frame_set_id: str, overview: dict) -> dict:
    """Save visual context only after inspecting every returned frame. Requires summary, observations[{time_seconds,description}], and limitations; optional speech_quality='unresolved' with quality_note. Native frame/media hashes bind this to the exact work; caller filesystem paths are not accepted. Captions remain visual evidence, separate from spoken-word corrections."""
    return archive_visuals().submit(archive_id, video_id, frame_set_id, overview)


@mcp.tool()
def export_creator_archive(archive_id: str, include_media: bool = False) -> dict:
    """Create a new portable archive snapshot with inventory, counts, reviewed speech, image posts, and inspected visual context. Include media explicitly for original MP4 copies. The companion ZIP excludes videos and credentials. Read returned completion flags and quality caveats; artifact completion does not establish word-perfect speech."""
    from .archive_export import ArchiveExporter
    return ArchiveExporter(manager(), archive_manager()).export(archive_id, include_media)


@mcp.tool()
def start_creator_archive(creator: str, max_scrolls: int = 2000, max_seconds: int = 1800) -> dict:
    """Enumerate public creator works by normal profile scrolling. Poll get_creator_archive. Only an explicit accepted terminal page establishes complete inventory; stalled/limited/blocked scans retain partial work and never imply all history. No speech transcription is started by listing."""
    return archive_manager().start(creator, max_scrolls=max_scrolls, max_seconds=max_seconds)


@mcp.tool()
def get_creator_archive(archive_id: str, offset: int = 0, limit: int = 50) -> dict:
    """Read archive status, coverage, exact metrics, CSV/index paths and ordered works. Follow next_offset until null for every captured work. Inventory completion is separate from per-work transcript/review completion."""
    return archive_manager().report(archive_id, offset=offset, limit=limit)


@mcp.tool()
def transcribe_archive_work(archive_id: str, video_id: str, profile: str = 'balanced') -> dict:
    """Transcribe an exact work selected from this archive's persisted verified inventory; preserves its sampling time and avoids a newest-video lookup. Retain check_id, poll get_report, read every transcript page, and submit_review. A repeat submission returns the same check; challenges stop the batch for human action."""
    return archive_manager().submit_work(archive_id, video_id, profile=profile)


@mcp.tool()
async def capture_archive_images(archive_id: str, video_id: str) -> dict:
    """Capture one exact image post from the verified archive: published images and a normal page screenshot. Returns local files, explicit completeness and errors. This does not invent a speech transcript or bypass human verification."""
    offset = 0
    while True:
        inventory = archive_manager().report(archive_id, offset=offset, limit=300)
        work = next((row for row in inventory['works'] if row['video_id'] == video_id), None)
        if work is not None:
            break
        offset = inventory['next_offset']
        if offset is None:
            raise ValueError('This work does not belong to the persisted inventory')
    if inventory['status'] in ('queued', 'running') or work['kind'] != 'image':
        raise ValueError('Select a captured image post after inventory enumeration')
    from .photos import capture_image_post
    return await capture_image_post(inventory['profile'], work,
        manager().paths.reports / 'archives' / archive_id / 'images' / video_id,
        manager().paths.session)


@mcp.tool()
def check_creator(creator: str, profile: str = 'balanced', refresh_identity: bool = False) -> dict:
    """Start a local creator-name/ID/profile check and return check_id immediately. Poll get_report. No scheduler is created; only a complete reviewed report advances delivery history."""
    return manager().submit(creator, profile, refresh_identity)


@mcp.tool()
def get_report(check_id: str) -> dict:
    """Get live status, newest link, sampled like/comment/collect/share counts and raw/reviewed file paths. done+complete means raw readiness; delivery_complete additionally requires host-AI review."""
    return manager().report(check_id)


@mcp.tool()
def resume_check(check_id: str, profile: str | None = None) -> dict:
    """Resume the SAME interrupted, model-failed, or invalid-timeline check without recollecting verified saved audio. For captcha/login_required, first complete a newer dedicated user login/verification. Never automatically retry or solve challenges. Poll get_report after resume."""
    return manager().resume(check_id, profile)


@mcp.tool()
def get_transcript(check_id: str, offset: int = 0, limit: int = 80) -> dict:
    """Read ordered original ASR segments with indices and source_sha256. Follow next_offset until null before reviewing. Never summarize the title as speech."""
    return manager().transcript(check_id, offset, limit)


@mcp.tool()
def submit_review(check_id: str, review: dict) -> dict:
    """Save a semantic review supplied by the current host AI, never call another LLM. Requires source_sha256, edits[{index,before,after,reason,evidence,evidence_level,confidence}], pending_review[{index,issue}], overview{summary,sections[{start,end,title,summary}],limitations}. Leaves originals immutable; uncertain text remains pending."""
    return manager().review(check_id, review)


@mcp.tool()
def start_login(verification_window: bool = True) -> dict:
    """Prepare a dedicated local Douyin QR login. The visible verification window keeps the same browser open for human-handled sliders. User scans; poll login_status, then resume_check the saved check. No other browser profiles or cookie contents are read."""
    from .login import begin_login
    return begin_login(manager().paths.session, verification_window=verification_window)


@mcp.tool()
def login_status() -> dict:
    """Return QR path and dedicated-session status only. Saved state can expire; never return browser credentials."""
    from .login import login_status as read_status
    return read_status(manager().paths.session)


@mcp.tool()
async def resolve_creator_from_videos(creator: str, candidate_video_urls: list[str]) -> dict:
    """Fallback: host web search supplies 1-5 observed official Douyin video URLs; verify live author matches exact requested identity. Search index is discovery only, never proof of newest work or counts. Stops on captcha/ambiguity."""
    return await manager().resolve(creator, candidate_video_urls)


def main():
    try:
        mcp.run(transport='stdio')
    finally:
        if archive_manager.cache_info().currsize:
            archive_manager().close()
        if manager.cache_info().currsize:
            manager().close()


if __name__ == '__main__':
    main()
