# Accessible public-history archives

Use this workflow only when the user asks for a creator's history or all accessible public works. The following `example-creator` label is fictional. Use actual IDs returned by tools at runtime; no account or video identifiers are supplied by this document.

Classify "archive all accessible public works", "all content", and equivalent full-content requests as including original videos. Plan a primary-folder export with `include_media=true`; the user does not need to request "originals" separately. If the user explicitly requests only an inventory or text/images, preserve that narrower scope instead.

## Inventory first

1. Call `start_creator_archive(creator, max_scrolls=2000, max_seconds=1800)` and retain `archive_id`. Normal official-profile navigation and scrolling are bounded; listing does not start transcription.
2. Poll `get_creator_archive(archive_id, offset=0, limit=50)`. After a terminal result, read every page until `next_offset` is null. Retain profile identity, all works, per-work `sampled_at`, coverage, statuses, and exported inventory paths.
3. Use `inventory_complete` and coverage, not a quiet page, to describe completeness. Accepted end evidence, pagination continuity or verified count reconciliation, and no count mismatch are required. `end_observed`, `all_history_scanned`, `pagination_complete`, `chain_status`, and `count_mismatch` explain that decision. Partial inventories never prove all history.

Private, deleted, restricted, and unavailable works are outside accessible public scope. Unknown interaction values remain unknown. Pinned works are not necessarily newest; retain the application's chronological inventory order. Metrics are sampled per work, not simultaneous live counters.

A blocked or interrupted inventory retains captured work. Complete any user-required dedicated login first. There is no `resume_creator_archive` tool: `start_creator_archive` is an explicit fresh enumeration, not an assumed continuation from an old cursor. Keep the earlier archive and describe its partial coverage. A repeated request during an active enumeration reuses that active job; do not loop fresh scans around CAPTCHA or limits. Never discard a partial inventory to hide failure.

## Process exact captured works

For a video, call `transcribe_archive_work(archive_id, video_id, profile="balanced")` with an ID from the persisted inventory. Retain its `check_id`; repeating submission reuses the linked task. Poll `get_report`, read every `get_transcript` page, and submit a complete host review using [review.md](review.md). Process sequentially and stop on a fresh login/CAPTCHA boundary. Recover per-video work with `resume_check`; never replace the requested work with the newest post.

For an image post, call `capture_archive_images(archive_id, video_id)`. Retain actual published images, screenshot, completeness, and partial errors. Actually inspect returned files before describing their visual content. Do not invent a speech transcript for an image post.

For native `no_speech`, retain that status: ASR non-detection does not prove silence. Do not create empty or fabricated raw/corrected transcript files. Also flag structurally valid transcripts with unresolved language, sparse-recognition, or coverage problems instead of calling them word-complete speech.

## Verified frame observations

Call `capture_archive_video_frames(archive_id, video_id, timestamps=None)` for a captured video with verified original media. It returns a `frame_set_id`, actual sample times, PNG paths, and hashes. The default is three samples near 20%, 50%, and 80% of full duration. At most 12 custom timestamps are accepted in one request. This tool captures evidence; it does not perform more ASR.

Actually view every frame used. Describe only visible content at its returned actual time. State sample limits: still frames do not verify the full soundtrack, unobserved movement, every short caption, or a character's intent. If inspected captions supplement sparse speech, label them as observed on-screen text with first/last sampled appearances rather than exact onset/offset or rewritten spoken words.

Submit the result through:

```text
submit_archive_video_overview(archive_id, video_id, frame_set_id, overview)

overview = {
  "summary": "Description supported by the frames actually inspected",
  "observations": [
    {"time_seconds": <actual returned frame time>, "description": "Visible evidence"}
  ],
  "limitations": ["Only the described samples were inspected; audio was not replayed."],
  "speech_quality": "unresolved",
  "quality_note": "Explain the actual unresolved language or coverage issue."
}
```

This is a schema illustration, not a request to run placeholder values. `speech_quality` and `quality_note` are optional; quality is `unresolved` or `not_assessed`. Use unresolved only for a supported speech-quality issue. The service controls `asr_status` (`speech` or `no_speech`). Never relabel it or provide arbitrary evidence paths. Caption translations and diagnostic probes do not overwrite native raw speech.

## Export and report the actual scope

Call `export_creator_archive(archive_id, include_media=true)` for an all-content/public-works archive or another request including original videos. The API default is `include_media=false`; do not rely on that default for full-content requests. Use media exclusion only when it matches an expressly narrower request. Each export creates a new application-owned snapshot; callers do not provide arbitrary destinations. Retain `export_id`, `export_paths` (`folder`, `index`, `manifest`, `csv`, `companion_zip`), `summary`, and `quality_caveats`.

Read the summary fields rather than inferring success from a ZIP:

- `total_works`, `videos`, and `image_posts` describe inventory.
- `raw_transcripts`, `reviewed_videos`, and pending wording describe speech artifacts.
- `no_speech_videos` and `visual_video_overviews` describe distinct non-transcript outcomes.
- `speech_quality_flagged_videos` retains unresolved language/coverage warnings.
- `inventory_complete`, `content_processing_complete`, `folder_delivery_complete`, and `delivery_complete` describe different artifact/coverage boundaries, not ASR accuracy.
- `original_media_files` and `media_excluded` state whether originals are available in that delivery.

With `include_media=true`, primary-folder completion requires every video's verified original MP4, including videos that also have speech transcripts; missing originals leave delivery partial. A native `no_speech` visual overview without its original MP4 is not a complete video-content delivery. The companion ZIP **always** excludes MP4s, has `delivery_scope=text_and_images_companion`, and cannot claim complete original-video delivery, even if its source folder is complete. Routine pending wording and structured speech-quality flags are separate counts.

Deliver the inventory/CSV, offline index, available per-work outputs, source scope, missing/failed counts, pending wording, and quality warnings. Distinguish metadata-only rows, raw-only speech, reviewed speech, actual images, and sampled-frame overviews. A completed export proves artifact availability under that contract, not word-perfect or word-complete recognition.
