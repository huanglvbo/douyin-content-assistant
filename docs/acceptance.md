# Preview release acceptance

This is a checklist for execution, not completed test evidence. The [isolated real run](validation.md) does not establish a fresh Windows or WorkBuddy installation. Keep source fixtures, real runtime evidence, target-client acceptance, and release packaging distinct.

## Installation and loading

- On Windows x64 without Python preinstalled, extract into a path with spaces and non-ASCII characters. Run the maintained installer. Verify bootstrap integrity, private supported runtime, dependencies, and required Chromium without a duplicate headless-shell download.
- Confirm model download is deferred by default and `-PrepareModel` explicitly prefetches it. First transcription must report preparation of a missing model rather than claim readiness.
- Interrupt setup and rerun. Verify healthy-stage reuse, stale-receipt invalidation after source changes, clear concurrent-setup handling, and retained timings/failure status.
- Import generated MCP configuration and Skill into the target WorkBuddy version. Reconnect and discover the actual registered latest-check, recovery, history, frame-observation, and export tools. A stdio fixture does not establish client support.
- Before accepting automatic registration, verify the client reads the owned configuration and Skill locations. Preserve unrelated entries, login state, models, reports, and independent checkout edits.

## Human login and recovery

- Use the tester's own account. Do not copy developer browser profiles, cookies, QR artifacts, identity data, or reports.
- Check QR display/expiry, phone confirmation, human slider handling, and finalization in the same dedicated context. No criterion requires avoiding or solving CAPTCHA automatically.
- After login, resume the saved check without repeating the original user request. A new challenge must stop again, preserve IDs and diagnostics, and avoid a retry loop.
- Fail transcription after media acquisition, resolve the cause, and resume the same check. Verify original metric sampling time and healthy saved media are reused. Modified, missing, linked, or out-of-bounds checkpoints must fail.

## Latest-post delivery

- Supply only an unbound creator name or public ID. Verify exact identity, latest ordering despite pinned posts, link, four integer metrics or explicit unknowns, and sampling time.
- Read every transcript page. Submit a host review with actual source hash, exact full original segments, disjoint edits/pending items, supported evidence labels, and ordered overview ranges.
- Rehash originals and preserve segment boundaries. Check corrected edition, change log, uncertainty list, and overview. Existing files do not prove speech accuracy or factual claims.
- Repeat a delivered work to check refreshed metrics and unchanged-work handling. Missing metrics, unresolved identity, CAPTCHA, and incomplete content remain explicit.

## Accessible public history

- Enumerate a creator with multiple listing pages and image posts. Read every inventory page. Verify creator binding, uniqueness, order, per-work sampling times, terminal-page evidence, pagination continuity, and advertised-count checks.
- Exercise an empty final page, duplicate prefetches, a missing cursor link, count mismatch, scroll stall, time limit, interruption, expiry, and CAPTCHA. Preserve partial work without an all-history claim.
- Submit exact inventory videos sequentially. Repeat submission reuses the linked task; resume must not select the current newest work or create duplicates.
- Capture actual published image assets and a screenshot. Preserve partial failures and exact identity checks. Do not invent speech for an image row.

## Speech and visual evidence

- Exercise invalid timing and its one bounded full-input retry. Verify input is not cropped, failed segments are not discarded to force success, recovery facts remain, and persistent timing failure stays explicit.
- Use actual speech and native `no_speech`. Keep raw transcription, reviewed speech, and visual-only outcomes separate. A tone or music clip is not a speech-accuracy test.
- Capture default and bounded custom frames. Actually inspect every frame used, then submit observations tied to its returned frame-set receipt and actual timestamps. Reject fabricated times, uninspected evidence, and arbitrary input paths.
- Verify `no_speech` creates no placeholder transcripts or silence claim. Describe limits on audio, intermediate motion, and short captions when relevant.
- For unreliable language or sparse speech, preserve raw words, mark structured quality unresolved, and keep observed caption sequences separate. Successful export must not convert uncertainty into verified speech.

## Export and packaging

- Export new snapshots with default media exclusion and `include_media=true`. Verify unique IDs and application-owned destinations, not arbitrary caller paths.
- Forward-test natural-language scope: "archive all accessible public works" and "all content" must select `include_media=true` without requiring the user to say "originals" separately. An expressly inventory-only or text/images-only request must stay narrower. Every companion ZIP must retain its media-exclusion caveat.
- Open the primary index offline. Check search, filters, sorting, all local links, CSV text-safe long IDs, manifest, and hashes. Inspect coverage, counts/times, raw/reviewed files, images, visual overviews, pending wording, and quality warnings.
- Verify included original MP4 files against receipts. A native `no_speech` visual overview without its original MP4 must not count as complete video content.
- Open the companion ZIP separately. It always excludes MP4s and states `delivery_scope=text_and_images_companion`, media exclusion, and incomplete original-video delivery even if its source folder is complete.
- Keep source/Skill releases separate from user-generated archives. Extract final release ZIPs and verify allowlist, manifest, and checksums. Exclude real collected identities/IDs/titles, transcripts, media, credentials, QR artifacts, databases, logs, models, environments, personal paths, and private configuration. Examples and fixtures must be synthetic.

## Scheduled execution

- Reconnect the target client and retrieve durable reports. Configure a host-local task with connector, Skill, account/model allowance, and output access.
- Verify operation with computer/client running and honest failure when login expires or human action is required. Cloud tasks are not direct access to the local runtime.

Record what actually ran, its version/result, and remaining limits. Unexecuted checks remain unaccepted; the corresponding release remains preview.
