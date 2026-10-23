# Development validation record

Updated: October 23, 2026. Current version: 0.1.1 development preview.

## Isolated real-provider validation

An isolated run exercised real public collection, local timestamped speech recognition,
and host-AI editorial review. Private evidence records contain the original failures,
sampling receipts, source-preservation checks, and unresolved recognition issues.
Creator identities, source links, interaction snapshots, media, transcripts, browser
state, and login diagnostics are deliberately excluded from the public repository.

This establishes the tested isolated path only. It does not establish clean Windows
installation, target WorkBuddy loading, general creator coverage, complete speech
recognition, elimination of platform verification, or scheduled execution. Unknown
or uncertain wording remains explicit, and the user's separate WorkBuddy environment
was preserved.

## Local 0.1.1 recovery update, October 21, 2026

- The updated suite ran 127 tests and finished OK, with three Windows symbolic-link subcases skipped. The first combined run exposed a UTF-8 fixture read using the Windows default encoding; the fixture was corrected explicitly and the full suite passed on rerun. Original run logs remain local and are excluded from packages.
- A Windows PowerShell cached-install fixture ran with external networking blocked, no bootstrap executable, and no model files. It skipped completed package/browser stages and wrote successful stage timings. This establishes the unchanged-install path, not cold-machine installation speed.
- The actual local public model cache was inspected without loading weights or downloading anything. A complete cached small model was found, and a missing base request selected small explicitly. Cache presence is not a new real-model inference test.
- Offline recovery tests verify that a model failure retains the same check ID, metric sampling time, and audio; reconnect and concurrent resume do not repeat collection; modified checkpoints are rejected; stale login proof cannot resume a CAPTCHA job; and a new challenge stops again without a retry loop.
- Mock login tests retain one visible dedicated context from QR through human verification. They distinguish scan, phone confirmation, verification, and finalization and require session plus additional login proof. No real slider was solved or real browser/account used in these tests.
- Download tests verify empty-file rejection, disabling Xet before downloader imports, and at most one forced repair through the official Hugging Face endpoint. No live model download or mainland-network speed test was executed.
- The real MCP stdio fixture now lists eight tools, including resume_check, retains full transcript/review checks, and verifies the visible-login argument default.
- User-supplied WorkBuddy screenshots report manual model-download repair and a separate creator collection attempt. A login_required response and a synthetic 440 Hz tone returning no_speech are partial diagnostics, not complete real-video delivery acceptance. The independently modified WorkBuddy checkout was inspected without overwriting its source, session, model, or task data.

The recovery changes remain a development preview. The October 23 isolated run above adds real reviewed delivery and repaired live QR-extraction evidence. Clean Windows installation, target WorkBuddy loading, and scheduled execution remain separate acceptance items. The earlier 0.1.0 results below are historical evidence, not proof that these new paths were accepted in WorkBuddy.

## Historical 0.1.0 validation, October 21, 2026

### Executed in that historical pass

- The full unittest suite ran 80 tests in the project's new isolated Python 3.12.15 virtual environment and finished OK. Two symbolic-link subcases were skipped because the Windows account lacked permission to create symbolic links. `pip check` passed.
- A real MCP stdio subprocess initialized, listed seven tools, returned the logged-out state, paginated all 229 segments of a controlled transcript fixture, accepted a review, and returned the delivered report after shutdown and reconnection.
- Offline tests cover creator identity, pinned versus newest posts, missing metrics, CAPTCHA stopping conditions, public-candidate verification, login-status allowlisting, media destination checks, download integrity, ASR timelines, original-file protection, and installation configuration merging.
- Two defects were fixed with regression coverage: submitting a new fingerprint after modifying the original transcript, and reusing cached transcript content after it was changed.
- A background task with a missing dependency reaches an explicit failure state instead of remaining queued indefinitely.
- The independent recognition module processed real local speech. Source-specific durations, transcript quantities, and content remain only in private acceptance records. It used this project's source and public libraries rather than the previous application's recognition service.
- A bounded real-speech test also ran in the isolated development environment using previously downloaded public model weights. The source audio and output are excluded from release packages.
- Skill-format validation, PowerShell syntax parsing, and allowlisted source packaging passed.
- Diagnostics in the isolated development environment confirmed public dependencies and Chromium were available. No Douyin session was saved or migrated. The installer's clean-machine download flow remains a separate acceptance item.

These results establish source behavior, MCP protocol operation, and local recognition of real speech. They do not replace the user-environment acceptance below.

### Not executed in that historical pass

The following describes the October 21 baseline, not the current acceptance state. Its MCP service had seven tools; the current 0.1.1 service has eight. The October 23 real capture/transcription result and current outstanding work are documented above.

- Real Douyin media acquisition and complete creator delivery using this project's own dedicated login state; no cookies or account bindings were copied from the previous project.
- Running the complete installer on a clean Windows machine without Python preinstalled.
- Loading the Skill and all seven tools in WorkBuddy, scanning with the user's own mobile account, completing delivery, and running a local scheduled task.

## Project repository

The project is published as **Douyin Content Assistant** at [huanglvbo/douyin-content-assistant](https://github.com/huanglvbo/douyin-content-assistant). Publishing source and accepting the application in WorkBuddy are separate steps. The public repository and download packages remain labeled as a development preview.

See [release acceptance](acceptance.md) for the remaining checks. The source package contains code, tests, documentation, and licenses, excluding the real audio, transcripts, models, login state, and development environment used in validation.
