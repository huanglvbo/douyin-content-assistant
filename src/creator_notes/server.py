"""Native MCP stdio service. All mutable state belongs to the current user."""
from functools import lru_cache
from mcp.server.fastmcp import FastMCP

from .engine import Manager

mcp = FastMCP('creator-notes', instructions='Collect creator updates and full timestamped speech. Read every transcript page, then use the host AI to review and submit_review. Raw collection is not final delivery. Respect login, captcha, ambiguity, partial metrics and evidence limits.')


@lru_cache(maxsize=1)
def manager():
    return Manager()


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
    """Resume the SAME interrupted/model-failed check without recollecting valid saved audio. For captcha/login_required, first complete a newer dedicated user login/verification. Never automatically retry or solve challenges. Poll get_report after resume."""
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
        if manager.cache_info().currsize:
            manager().close()


if __name__ == '__main__':
    main()
