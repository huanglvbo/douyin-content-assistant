# Review request

Read every `get_transcript` page and copy its actual `source_sha256` into the request. `index` is the tool's zero-based segment index. `before` must match the complete original segment exactly. Omit unchanged segments. A segment cannot be both edited and listed as pending.

The `review` argument to `submit_review(check_id, review)` has this structure:

```json
{
  "source_sha256": "Use the actual source hash returned by the tool",
  "edits": [
    {
      "index": 2,
      "before": "Use the exact original segment text",
      "after": "An evidence-supported correction",
      "reason": "Explain the typo or recognition error",
      "evidence": "State the basis without inventing an audio check",
      "evidence_level": "context_inferred",
      "confidence": "high"
    }
  ],
  "pending_review": [
    {"index": 7, "issue": "Preserve the wording and explain the uncertainty"}
  ],
  "overview": {
    "summary": "A one-sentence description of the full spoken content",
    "sections": [
      {"start": 0.0, "end": 10.0, "title": "Actual section title", "summary": "Summarize the corresponding time range"}
    ],
    "limitations": ["Unreplayed corrections rely on context; not all visual content was analyzed"]
  }
}
```

Use empty arrays when there are no edits or pending items, but still submit an overview based on the complete transcript. Section times must be ordered and within the actual audio duration. Preserved segment times do not mean corrected words were independently realigned.

Allowed `evidence_level` values are `user_confirmed` (explicit user correction), `metadata_spelling` (proper-name spelling), `context_inferred` (contextual inference), `audio_checked` (actual replay), `caption_checked` (actual caption verification), and `script_normalization` (writing-form normalization). `confidence` is `high` or `medium`. Preserve low-confidence wording and mark it pending.

The tool validates hashes, indices, original text, and time ranges, then saves separate reviewed files. Check `get_report` for `review_status`, reviewed file paths, and `delivery_complete` before reporting completion. Successful export validation does not establish semantic accuracy.
