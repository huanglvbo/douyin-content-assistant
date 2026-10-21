# Architecture and independent implementation

Creator name or ID -> host AI reads the Skill -> local stdio MCP -> dedicated browser verifies the account and newest post -> four engagement metrics -> project media downloader -> local faster-whisper -> immutable timestamped transcript -> host-AI review -> project validates revisions and records delivery.

MCP is the tool communication protocol. The Skill supplies workflow and evidence rules. Playwright provides browser capabilities, and Whisper provides speech recognition. This project's code manages downloads, tasks, originals, pagination, revision validation, and delivery history without another transcription application, HTTP backend, or its access token.

Runtime data defaults to `%LOCALAPPDATA%/CreatorNotes`: `session` stores dedicated Douyin login state, `models` stores speech models, `work` stores temporary media, and `reports` stores delivered files. Users can configure `CREATOR_NOTES_HOME`, `CREATOR_NOTES_MODELS`, and `CREATOR_NOTES_OUTPUT`. The installer generates the current user's actual paths for the client instead of embedding developer-machine paths.

The database stores tasks and successful delivery history. Machine transcription alone does not complete delivery. The video enters successful delivery history only after all four metrics are available and the review has been saved. Recollection can refresh metrics. Each check processes the latest verifiable post in the recent window; it does not automatically recover every publication between checks.

Speech recognition does not interpret all visual content. Correction uses the host's current model without another external LLM API. Text is processed under the host's and selected model's data-handling rules. Contextual inference cannot be marked as verified audio unless the segment was actually replayed or checked against captions.

Reference-project features were studied, so this project does not claim isolated clean-room development. The new transcriber uses public library interfaces and removes the shared-video-transcriber application dependency. Creator-collection code continues the integration layer added for this workflow. Third-party libraries, models, and browsers retain their own licenses; see the root third-party notices.
