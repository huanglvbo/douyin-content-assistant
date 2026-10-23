# Douyin Content Assistant

Local MCP + Skill for Douyin creator research. Internal Python package: Creator Notes.

Give the host AI a creator name, public ID, or profile URL. A latest-post check returns its verified link, like/comment/favorite/share counts, timestamped machine transcript, separate reviewed edition, change log, and overview. An explicitly requested history archive inventories all accessible public works and processes each captured video or image post with explicit coverage and quality limits.

**v0.2.0 preview.** One isolated real public-history run has been exercised. A fresh Windows installation, target WorkBuddy loading, and scheduled execution have **not** been revalidated in this release. This is not a verified one-click release, and completed exports do not guarantee word-perfect transcription.

The project maintains its own MCP service, browser collection, tasks, media checkpoints, transcription orchestration, and exports. It uses public libraries including Playwright and faster-whisper, together with Whisper models. It does not require the shared-video-transcriber application. The host AI reviews content without another paid model API. See [third-party notices](THIRD_PARTY_NOTICES.md) and [architecture](docs/architecture.md).

## Choose the result you need

| Request | Result | Completion boundary |
| --- | --- | --- |
| Check the latest post | One verified latest work and its available content | Raw transcription and host-AI review are separate stages |
| Archive all public works / all content | Inventory, per-work processing, original videos, browsable local folder | Use `include_media=true`; a complete listing alone is not complete content delivery |
| Include original videos | Primary local export folder with available verified MP4 files | Use `export_creator_archive(..., include_media=true)` |
| Share the companion ZIP | Text, images, sampled-frame overviews, CSV, manifest | The ZIP always excludes original MP4 files |

Image posts retain actual published images and a page capture, with no invented speech transcript. A video returning `no_speech` can have a sampled-frame visual overview and verified original media. This means no deliverable speech was recognized, not that audio was proven silent. Unresolved language, sparse speech, and uncertain wording stay visible.

Requests such as "archive all accessible public works" or "all content" include original video delivery in the primary local folder. The Skill uses `include_media=true` for those requests. A request expressly limited to inventory or text/images keeps that narrower scope. The companion ZIP excludes MP4 files in every case.

## Quick start on Windows

For deployment by an AI assistant, follow [AGENTS.md](AGENTS.md). Use the maintained installer rather than creating a development environment as an installation step.

1. Download and extract the source ZIP, then double-click `scripts/install-workbuddy.cmd`. Setup prepares a private Python runtime, dependencies, and Chromium. No existing Python installation is required. Internet is needed for uncached components; speech-model download is deferred by default.
2. Import the generated `workbuddy-mcp.json` through WorkBuddy's custom connector interface, upload `creator-notes-skill.zip` through its Skill interface, and enable both. Default setup generates these files without editing client configuration.
3. Ask for a latest check or history archive. Example: "Archive all accessible public works for example-creator. Include engagement counts, reviewed timestamped transcripts where speech is recognized, actual image posts, visual overviews where needed, and original videos in the local export folder." **`example-creator` is a fictional placeholder; replace it with your chosen account.**

Use your own Douyin mobile account for the dedicated QR login. Douyin may also require a human slider in the same window. No separate collection or speech API key is required. Review uses the host account and model allowance. First transcription still downloads a selected speech model if no suitable complete cache exists, and local CPU inference takes time.

Installation defaults to `%LOCALAPPDATA%\CreatorNotes`, with separate source, session, model, work-media, and report locations. The installer supports `-InstallRoot` and optional `-PrepareModel`. `-RegisterWorkBuddy` backs up and merges only owned connector/Skill entries and stops on conflicts; that target-client route still needs fresh acceptance. Healthy completed setup stages are reused. Stage timings and status stay in the local `integration/last-install.json` receipt.

If setup fails, run `scripts/diagnose.cmd` and retain failed-stage diagnostics. Do not copy another user's login state, replace unrelated client configuration, or silently switch download sources. See [release acceptance](docs/acceptance.md).

## Login, recovery, and review

The application checks its model cache before opening Douyin and reports requested and selected models. A complete suitable cache can be reused; empty or incomplete files are not ready models.

The login helper retains a dedicated browser context through QR scan, phone confirmation, human verification, and finalization. After login, the Skill continues the saved check with `resume_check(check_id)`. A later CAPTCHA stops again rather than entering an automatic retry loop. The application does not solve sliders or promise they can be avoided.

After a recoverable transcription failure, a valid checkpoint preserves the same check ID, sampled metrics, and downloaded media. Resume verifies task identity, file bounds, byte count, and SHA-256; it does not recollect metrics or redownload healthy saved media. Invalid ASR timelines can trigger one bounded retry over the complete input with shorter chunks. Failed attempts remain auditable, and persistent failures remain explicit.

Machine originals are immutable. The host reads every transcript page, makes supported corrections in a separate edition, and retains uncertain wording for review. Titles support spelling, not proof of speech. Caption observations and visual chapters remain separate from spoken words. An exported reviewed file does not prove every word, language, claim, or visual action was verified.

For history requests, read the [archive workflow](skills/creator-notes/references/archive.md). Listing coverage, per-work sampling times, retained failures, original media, review readiness, and export scope are reported separately.

## Other clients and development

The service uses local stdio MCP; no public server is required. Setup also generates a `codex-mcp.toml` fragment. Merge only its owned entry into existing client configuration, reconnect MCP, and install `skills/creator-notes` in the host. Use paths generated for the current user.

For development only:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m creator_notes.cli doctor
.\.venv\Scripts\python.exe -m creator_notes.server
```

Run appropriate tests and build source packages:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests
python scripts/build_release.py
```

Source releases use an allowlist and hash manifest. They exclude collected metadata, transcripts, media, reports, models, browsers, databases, QR artifacts, credentials, runtime logs, and personal client configuration. User-generated content archives are separate local outputs, not part of the open-source release.

For periodic checks, configure a **local scheduled task in the host** with connector and Skill enabled. The computer, client, login, and model allowance must remain available. The project creates no separate scheduler or system service; cloud tasks cannot directly access this local runtime.

## Validation and scope

The anonymized isolated run inventoried **126 accessible public works: 124 videos and two image posts**. It produced **105 machine transcripts**, **19 visual overviews for videos with no deliverable recognized speech**, and **three explicitly unresolved speech-quality cases**. These are results from one public creator, not a general accuracy benchmark. See [validation](docs/validation.md) and [unexecuted acceptance checks](docs/acceptance.md).

Scope is accessible public works returned by the verified official profile. Private, deleted, restricted, or unavailable works remain outside that claim. A scroll stall, time limit, missing page, or advertised-count mismatch leaves coverage partial. Engagement values are per-work snapshots, not live counters; unavailable metrics remain unknown rather than zero.

Repository: [huanglvbo/douyin-content-assistant](https://github.com/huanglvbo/douyin-content-assistant). Licensed under [MIT](LICENSE).
