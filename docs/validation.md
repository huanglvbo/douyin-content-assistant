# Validation record

Updated October 23, 2026. Release scope: **v0.2.0 preview**.

This public record contains anonymized outcomes only. It omits the tested creator's name, account/profile/video identifiers, titles, exact interaction values, transcripts, media, login artifacts, personal paths, and internal task IDs. Runnable examples and fixtures are synthetic.

## Isolated real public-history run

The project was exercised against **a public creator** using its own dedicated login and isolated runtime. The user's independently modified WorkBuddy installation was not reinstalled or overwritten.

| Recorded outcome | Count |
| --- | ---: |
| Accessible public works inventoried | 126 |
| Video works | 124 |
| Image posts | 2 |
| Machine transcripts available | 105 |
| Videos with no deliverable recognized speech and a visual overview | 19 |
| Explicitly unresolved speech-quality cases among transcript-bearing works | 3 |

The inventory reached an accepted listing end with pagination coverage and count checks. This establishes accessible public history returned for that account at that time, not historical private, deleted, restricted, or unavailable posts. Interaction values retain per-work sampling times and are not live counters.

The run acquired actual media and image-post content, processed recognized speech locally, and prepared separate host-AI reviews and overviews. Originals and segment timestamps were preserved. Corrections retained uncertain wording and evidence labels. Reviewed artifacts and exports were checked for source consistency, relative-link integrity, and missing content.

Videos returning `no_speech` retained that classification. Visual overviews came from actually inspected, source-verified frame samples with explicit limits. No placeholder raw/corrected speech files were created, and ASR non-detection did not establish complete silence.

Three transcript-bearing cases retained unresolved language, sparse-recognition, or speech-coverage issues. Inspected caption sequences were labeled visual evidence instead of merged into native speech. A separate diagnostic probe did not overwrite or become verified original speech. Available artifacts therefore do not imply word-complete recognition for those cases.

## Fixes exercised and evidence limits

- An official login-page redirect exposed a QR-extraction allowlist defect. The repair was separately checked against the signed-out official page. QR extraction, user-confirmed login, and a fresh WorkBuddy login are different acceptance events.
- A real run exposed invalid ASR timing. The recognizer now permits one full-input retry with shorter chunks, retaining timing-failure/recovery facts. It does not crop input or discard failed segments to manufacture success. Persistent timing errors remain failures.
- Durable checkpoints retain the exact selected work, creator binding, original sampling time, media size, and hash. Resume and cached-original reuse were exercised separately from inventory and export.
- Image delivery, structured speech-quality warnings, visual overviews, and primary-folder versus companion-ZIP scope remain separate. The ZIP excludes MP4s and does not inherit a complete original-video claim from its source folder.
- Automated validation covers identity/pagination, task admission and recovery, originals, timing failures, frame evidence, review validation, export links/checksums, and private-data exclusions. Fixture passes do not establish a new live account acceptance.

This is one isolated run, not a benchmark across creators, languages, hardware, networks, or every media type. Caption checks do not verify a speaker's technical, medical, business, or historical claims. Contextual corrections remain inferential unless their evidence was actually checked.

## Final source and protocol checks

- The full automated suite ran **247 tests** and finished **OK, with four skipped**. Skipped checks are not counted as executed acceptance.
- A native MCP stdio fixture used a **generated MP4** to verify frame capture, host visual-overview submission, export, service restart, and durable re-export. It exercised the actual tool/protocol path with controlled generated media; it did not perform a real login, network collection, model download, or real-model inference.
- The public-source gate checked **57 selected files** with **zero errors**. This is a selected-source check, not verification of a built release archive.
- The maintained release builder produced source and Skill ZIPs. Package verification passed ZIP integrity, all 57 source-manifest hashes, both SHA-256 sums, Skill/source consistency, and protected-value checks with zero privacy errors. This verifies package contents, not a fresh WorkBuddy installation.

## Earlier preview evidence

Earlier v0.1.x work exercised a real latest-post check, local recognition, semantic review, immutable originals, MCP pagination/reconnection, model-cache handling, and recoverable login/model failures. Synthetic-tone tests established honest `no_speech` handling, not real-speech accuracy. Cached-install fixtures established stage reuse, not cold-install speed or target-client acceptance.

Those results remain bounded to their executed environment and version. Detailed private artifacts are not included in public docs, release notes, fixtures, or source packages.

## Not revalidated in this release

- Fresh Windows x64 installation without Python preinstalled, including cold downloads and target-network performance.
- Import or automatic registration of the connector and Skill in a fresh target WorkBuddy installation.
- That client's own QR login, human slider if requested, complete creator delivery, and local scheduled execution.
- General transcription accuracy, every language or lyric, word-level realignment, or removal of platform verification.

These remain separate [release acceptance checks](acceptance.md). Source and packages remain **preview**.
