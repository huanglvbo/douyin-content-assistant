# Douyin Content Assistant

MCP + Skill for creator research. Internal Python package: Creator Notes.

Enter a Douyin creator's name, public ID, or profile URL to receive the latest video link; like, comment, favorite, and share counts; a full timestamped machine transcript; an AI-reviewed correction; a change log; and a content overview. The project targets WorkBuddy on Windows and exposes standard local MCP tools for Codex and other compatible clients.

This independently maintained project provides its own MCP service, task records, browser collection, media downloading, transcription orchestration, and report delivery. It directly uses public libraries such as Playwright and faster-whisper, together with Whisper models. It does not require installing, importing, or starting the shared-video-transcriber application. The current host AI reviews and summarizes transcripts without an additional paid model API. See [third-party notices and provenance](THIRD_PARTY_NOTICES.md).

**Development preview. Source and test coverage are documented in the validation record. Installation, login, and complete scheduled execution in WorkBuddy on a clean Windows machine have not been accepted. This is not yet a verified one-click release.**

## Quick start on Windows

1. Download and extract the source ZIP, then double-click `scripts/install-workbuddy.cmd`. The installer prepares an isolated Python runtime, dependencies, Chromium, and a speech model. No existing Python installation is required. Initial setup needs Internet access; rerun the installer to retry a failed download.
2. Follow the paths printed by the installer. Import `workbuddy-mcp.json` through WorkBuddy's custom connector interface, upload `creator-notes-skill.zip` through its Skill interface, and enable both. The default installer generates these files without changing the client's configuration.
3. Ask WorkBuddy: "Check Douyin creator ID <creator-name-or-id>. Give me the latest video, all four engagement counts, the full timestamped transcript, a corrected version, and an overview." For the first login, scan the displayed QR code with your own Douyin mobile account. Repeat login when the session expires.

No separate collection or speech API key is required. Correction and summarization use your own WorkBuddy account and model allowance. Transcription runs locally, so performance depends on your computer. Speech recognition does not establish what appears in unspoken captions, charts, or other visual content.

Installation defaults to `%LOCALAPPDATA%\CreatorNotes`. Account state, models, reports, and application source are stored separately. The installer supports `-InstallRoot` for a custom location and `-SkipModelDownload` to defer model download. Optional `-RegisterWorkBuddy` backs up and merges only this project's entry into the documented `.codebuddy` configuration; it stops if a conflicting MCP entry or Skill exists. This registration route still requires acceptance in the target client.

If installation or loading fails, double-click `scripts/diagnose.cmd` to check dependencies and the browser. Diagnostics do not print cookies, tokens, or the complete environment. Rerunning the installer preserves prepared models and account data.

For periodic checks, use a WorkBuddy **local scheduled task**, select the connector and Skill, and specify the creator and frequency. The computer and client must be running with a valid login. Cloud tasks cannot directly access this local application. The project does not install a separate scheduler or background system service.

## Development and other MCP clients

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m creator_notes.cli prepare-model --model small
.\.venv\Scripts\python.exe -m creator_notes.cli doctor
.\.venv\Scripts\python.exe -m creator_notes.server
```

The host starts the final command as a stdio MCP process; no public server is required. The Windows installer also generates a `codex-mcp.toml` fragment. Preserve existing configuration when merging it, reconnect MCP, and install `skills/creator-notes` in the host. Do not copy another user's interpreter paths, login state, or complete client configuration.

Run tests and build a source package without personal data:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests
python scripts/build_release.py
```

The source package uses a file allowlist and includes a hash manifest. Cookies, QR codes, historical transcripts, databases, models, browsers, virtual environments, and personal client configuration are excluded. Complete the [release acceptance checks](docs/acceptance.md) before treating the package as accepted for end users.

## Limits

Ambiguous names require confirmation. CAPTCHAs, private or deleted posts, access restrictions, and unavailable metrics are reported explicitly. Only the newest verifiable post in the recent collection window is checked; the project does not claim to scan all history or retrieve every post published between checks. Original transcripts are never overwritten. Uncertain wording remains unchanged and is listed for review. Host-AI review must be submitted after machine transcription before delivery is complete.

Repository: [huanglvbo/douyin-content-assistant](https://github.com/huanglvbo/douyin-content-assistant).

Licensed under [MIT](LICENSE).
