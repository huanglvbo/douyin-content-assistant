# Douyin Content Assistant

MCP + Skill for creator research. Internal Python package: Creator Notes.

Enter a Douyin creator's name, public ID, or profile URL to receive the latest video link; like, comment, favorite, and share counts; a full timestamped machine transcript; an AI-reviewed correction; a change log; and a content overview. The project targets WorkBuddy on Windows and exposes standard local MCP tools for Codex and other compatible clients.

This independently maintained project provides its own MCP service, task records, browser collection, media downloading, transcription orchestration, and report delivery. It directly uses public libraries such as Playwright and faster-whisper, together with Whisper models. It does not require installing, importing, or starting the shared-video-transcriber application. The current host AI reviews and summarizes transcripts without an additional paid model API. See [third-party notices and provenance](THIRD_PARTY_NOTICES.md).

**Development preview. Source and test coverage are documented in the validation record. Installation, login, and complete scheduled execution in WorkBuddy on a clean Windows machine have not been accepted. This is not yet a verified one-click release.**

## Validation status

An isolated real MCP check completed public creator collection, all four engagement counters, local timestamped transcription, and host-AI review. Creator identities, source links, sampled values, media, transcripts, and login diagnostics are retained only in private local acceptance records. See the [validation record](docs/validation.md) for scope and remaining target-client acceptance work. Completion does not imply word-perfect transcription.

## Quick start on Windows

For an AI assistant deploying this repository into WorkBuddy, follow [the deployment instructions](AGENTS.md) and run the maintained installer. Creating a new development environment and running every unit test are not required installation steps.

1. Download and extract the source ZIP, then double-click `scripts/install-workbuddy.cmd`. The installer prepares an isolated Python runtime, dependencies, and Chromium without downloading a speech model yet. No existing Python installation is required. Initial setup needs Internet access. Each stage prints its elapsed time; rerunning setup reuses healthy completed stages.
2. Follow the paths printed by the installer. Import `workbuddy-mcp.json` through WorkBuddy's custom connector interface, upload `creator-notes-skill.zip` through its Skill interface, and enable both. The default installer generates these files without changing the client's configuration.
3. Ask WorkBuddy: "Check Douyin creator ID <creator-name-or-id>. Give me the latest video, all four engagement counts, the full timestamped transcript, a corrected version, and an overview." For the first login, scan the displayed QR code with your own Douyin mobile account. Repeat login when the session expires.

No separate collection or speech API key is required. Correction and summarization use your own WorkBuddy account and model allowance. The first transcription downloads the selected speech model once if it is not cached; this can take several minutes and still requires Internet access. Deferring the model makes initial setup shorter, but does not remove the model download or local CPU cost. Later runs reuse the cache. Speech recognition does not establish what appears in unspoken captions, charts, or other visual content.

Installation defaults to `%LOCALAPPDATA%\CreatorNotes`. Account state, models, reports, and application source are stored separately. The installer supports `-InstallRoot` for a custom location. Model download is deferred by default; use `-PrepareModel` to prefetch the small model during setup. The older `-SkipModelDownload` flag remains compatible with the default behavior. Optional `-RegisterWorkBuddy` backs up and merges only this project's entry into the documented `.codebuddy` configuration; it stops if a conflicting MCP entry or Skill exists. An unchanged owned configuration is left untouched. This registration route still requires acceptance in the target client.

If installation or loading fails, double-click `scripts/diagnose.cmd` to check dependencies and the browser. Diagnostics do not print cookies, tokens, or the complete environment. Setup saves source fingerprints and completed package stages: unchanged healthy installs skip Python resolution, source copying, and package installation. The browser is downloaded only if the required full build is missing, and the duplicate headless shell is excluded. Rerunning preserves models and account data; an interrupted individual transfer may still need to restart. Stage durations, the final status, and model-prefetch status are recorded in `integration/last-install.json` under the installation directory. These records stay local and are excluded from release packages.

For periodic checks, use a WorkBuddy **local scheduled task**, select the connector and Skill, and specify the creator and frequency. The computer and client must be running with a valid login. Cloud tasks cannot directly access this local application. The project does not install a separate scheduler or background system service.

## Login and recovery

The program checks its speech-model cache before opening Douyin. It reuses a complete suitable cached model and reports both the requested and actual model; for example, an installed `small` model can satisfy a `fast` request without downloading a second `base` model. With no suitable cache, first-use model preparation still requires a download. Empty or incomplete model files are not reported as ready.

The first QR login opens a dedicated verification window. Douyin may require a human slider check; this cannot be guaranteed away. The helper waits in the same browser context while the user completes it, then saves only this project's dedicated login state. The Skill polls the result and resumes the saved task. Normal collection reuses that state, although later platform verification may still be required.

If transcription fails after media acquisition, `resume_check(check_id)` keeps the same task, original metric sampling time, and verified downloaded audio. Recovery does not repeat creator lookup or download valid saved audio. Changed or missing cached audio is rejected. A new CAPTCHA stops the task again rather than entering an automatic retry loop. During normal checks the Skill uses MCP tools instead of modifying source or improvising installation scripts.

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
