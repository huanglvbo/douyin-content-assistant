# Architecture

The host AI reads the Skill and calls local stdio MCP. The service owns identity verification, normal browser collection, durable tasks, media acquisition, local speech recognition, original-file protection, and exports. The host supplies semantic review and observations of frames it actually inspects.

## Latest checks and public history

`check_creator` checks the newest verifiable work in the recent collection window. It does not automatically recover every publication between checks.

`start_creator_archive` enumerates the verified creator's accessible public history through normal official-profile navigation and scrolling. Works are deduplicated, creator-bound, ordered, and checkpointed. Metrics retain per-work sampling times. Listing alone does not transcribe videos.

An accepted terminal page is required for complete coverage. Pagination continuity or a verified advertised-count match must support the path to that end. Stalls, missing cursor links, mismatches, challenges, interruptions, and limits leave retained listings partial. Authenticated listing APIs are not directly replayed to bypass the normal browser flow.

`transcribe_archive_work` selects an exact inventory video, bound to its archive, creator, video, and metric snapshot. Repeat submission returns the linked check rather than substituting the current newest video. Image posts use a separate capture path.

## Durable media and ASR

Model readiness is checked before collection. Requested and selected models are explicit; complete cached weights can load locally. Incomplete caches are rejected. A stronger explicit request is not silently downgraded.

A durable checkpoint binds a verified original file to its task, creator, video, byte count, and SHA-256. `resume_check` reuses only a valid checkpoint. Modified, missing, linked, or out-of-bounds files cannot become trusted recovery media. Resume does not refresh the metric sampling time or repeat a healthy download.

Recognition processes the complete input. An invalid timeline can cause one complete-input retry with shorter chunks. It does not repair a bad pass by deleting segments, cropping the source, or treating an unrecognized tail as proven silence. Timing failures and recovery metadata remain separate from a later successful transcript. Persistent failures remain explicit.

`no_speech` means no deliverable speech was recognized. Language detection, sparse text, lyrics, effects, and noisy audio can remain unreliable even with structurally valid timestamps. Segment timing is not independently verified word alignment.

## Originals, review, and visual evidence

Machine files and hash receipts remain immutable. The host reads every transcript page and submits a separate review with the actual source hash, exact original segments, supported replacements, unresolved wording, and overview. Structural validation does not establish acoustic or factual accuracy.

Audio evidence requires actual replay. Caption evidence requires inspecting the relevant displayed text. Titles and contextual guesses do not qualify as either. Observed captions, figures, and visual chapters remain distinct from spoken transcript. Unknown wording is preserved instead of replaced with a fluent invention.

The maintained frame tool captures PNG samples from a verified original MP4. Default samples are near 20%, 50%, and 80% of full duration; custom sampling is bounded. The host actually views returned frames before submitting observations tied to `frame_set_id` and actual timestamps. The application validates that evidence receipt. A sampled-frame overview does not verify the whole soundtrack or every intermediate action.

Image-post capture verifies the exact author and post, retaining published images plus a page screenshot. Partial asset failures remain explicit. Image posts do not receive fabricated speech transcripts.

## Completion and export scopes

| State or field | Meaning |
| --- | --- |
| `inventory_complete` | Accessible public listing has accepted end and coverage evidence |
| Per-check `complete` | Raw processing reached its stated result; not automatically reviewed delivery |
| Per-check `delivery_complete` | Required metrics and review artifacts are available for that check |
| `content_processing_complete` | Inventoried works have the required recorded processing artifacts under the export contract |
| `folder_delivery_complete` | Primary folder has required content, including original media where required |
| `speech_quality_flagged_videos` | Speech language or coverage remains explicitly unresolved despite available artifacts |
| `delivery_scope=text_and_images_companion` | Portable ZIP excludes original MP4 files and cannot claim complete original-video delivery |

`export_creator_archive(archive_id, include_media=false)` creates a new snapshot under the application archive-reports directory; callers cannot provide arbitrary output paths. It returns export ID, folder/index/manifest/CSV/companion paths, summary fields, and quality caveats.

The API's default media exclusion is not the Skill's interpretation of an all-content request. Requests such as "archive all accessible public works" or "all content" include originals, so the host explicitly passes `include_media=true`. An expressly inventory-only or text/images-only request retains that narrower scope.

With `include_media=true`, primary-folder completion requires every video's available hash-verified original MP4, including speech-bearing videos; a missing original leaves delivery partial. The ZIP always excludes MP4s, even when its source folder includes them. A `no_speech` visual overview without the original video remains partial video-content delivery. Neither folder completion nor review-file existence establishes complete or word-perfect speech.

The offline index, CSV, relative links, manifest, and checksums distinguish missing content, image posts, recognized speech, visual overviews, pending wording, and structured quality warnings. Source report/review JSON, session state, signed CDN URLs, models, and personal paths do not belong in portable exports. User-generated archives are also excluded from public source releases.

## Authentication and host boundaries

The dedicated login window remains available through scan, phone confirmation, human slider, and finalization. The host polls those states and resumes the saved check. CAPTCHA is never solved by the program; new collection challenges stop again.

Private runtime data defaults to `%LOCALAPPDATA%/CreatorNotes`, with separate session, model, work, and reports locations. `CREATOR_NOTES_HOME`, `CREATOR_NOTES_MODELS`, and `CREATOR_NOTES_OUTPUT` support user-controlled locations. Setup generates current-user paths and preserves unrelated configuration.

Scheduling belongs to the host's local tasks while the computer, client, login, and model allowance remain available. The project installs no separate scheduler or system service.

## Implementation and dependencies

This independently maintained application uses public Playwright, faster-whisper, and model interfaces. It does not import or start shared-video-transcriber or require its token. Reference-project features were studied, so this is not a claim of isolated clean-room development. Dependencies retain their own licenses; see [third-party notices](../THIRD_PARTY_NOTICES.md).
