# Instructions for deployment assistants

Keep all project source, documentation, and messages in English.

## Windows WorkBuddy installation

Use the maintained installer as the normal installation entry point:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-workbuddy.ps1 -RegisterWorkBuddy
```

Run it from the checked-out project directory. It prepares the private supported runtime, reuses completed stages, and safely merges only the owned connector and Skill. Preserve unrelated configuration and all user login state, models, media, and reports. Reconnect or enable the connector and Skill in WorkBuddy when setup finishes.

Do not create an alternative virtual environment, run the entire development test suite, prefetch multiple speech models, or edit application source as routine installation steps. Do not copy another user's credentials or browser profile. A failed setup is not permission to change global settings or silently select a third-party mirror. Report the failed stage and safe diagnostics, retaining the setup cache for retry. First-use model preparation still needs a download if no suitable complete cache exists.

## Routine creator requests

Read `skills/creator-notes/SKILL.md` and use the registered MCP tools. Default to the balanced profile; let the application select and report a suitable installed model. Do not rewrite code or switch to ad hoc browser/shell automation to perform a creator check.

Retain the check ID. After an interrupted login, CAPTCHA, or recoverable transcription failure, follow the Skill and `resume_check` contract. A human completes any slider in the dedicated window. The application must not solve it or enter a retry loop. Reuse valid saved audio after a model failure instead of restarting collection.

## Evidence and completion

Keep installation readiness, protocol checks, fixture tests, model loading, real speech transcription, and complete real creator delivery distinct. A login-required response is not successful collection; a synthetic tone returning no speech is not successful real-video transcription. Declare complete delivery only after all requested metrics, full timestamped speech, reviewed corrections, and overview are available. Report known remaining acceptance items honestly.
