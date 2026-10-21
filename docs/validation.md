# Development validation record

Date: October 21, 2026. Version: 0.1.0 development preview.

## Executed

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

## Not executed

- Real Douyin media acquisition and complete creator delivery using this project's own dedicated login state; no cookies or account bindings were copied from the previous project.
- Running the complete installer on a clean Windows machine without Python preinstalled.
- Loading the Skill and all seven tools in WorkBuddy, scanning with the user's own mobile account, completing delivery, and running a local scheduled task.

## Project repository

The project is published as **Douyin Content Assistant** at [huanglvbo/douyin-content-assistant](https://github.com/huanglvbo/douyin-content-assistant). Publishing source and accepting the application in WorkBuddy are separate steps. The public repository and download packages remain labeled as a development preview.

See [release acceptance](acceptance.md) for the remaining checks. The source package contains code, tests, documentation, and licenses, excluding the real audio, transcripts, models, login state, and development environment used in validation.
