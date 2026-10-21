# Third-party dependencies and provenance

Douyin Content Assistant maintains its own project code, tool interfaces, and delivery workflow. Earlier functional research and trials included [shared-video-transcriber](https://github.com/ZY-ZhichaoYu/shared-video-transcriber). This project replaces that application-level dependency: its source package does not include the application, and its runtime does not import or start it. Some creator-collection components continue the integration code written separately for this workflow. This is not a claim of never having studied the reference project, or of developing the underlying speech model, browser, or MCP protocol.

The table lists major direct and runtime dependencies. Their metadata in the development environment and the corresponding official projects were checked on October 21, 2026. Actual installed versions are determined by `pyproject.toml` constraints and dependency resolution at installation time. This table is neither a complete lockfile nor a complete dependency inventory.

| Component | Checked version | License and official source |
| --- | --- | --- |
| MCP Python SDK | 1.27.2 | [MIT](https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE) |
| Playwright Python | 1.60.0 | [Apache-2.0](https://github.com/microsoft/playwright-python/blob/main/LICENSE) |
| faster-whisper | 1.2.1 | [MIT](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE) |
| CTranslate2 | 4.8.0 | [MIT](https://github.com/OpenNMT/CTranslate2/blob/master/LICENSE) |
| PyAV | 17.1.0 | [BSD-3-Clause](https://github.com/PyAV-Org/PyAV/blob/main/LICENSE.txt); its FFmpeg libraries have separate licenses determined by the actual build |
| HTTPX | 0.28.1 | [BSD-3-Clause](https://github.com/encode/httpx/blob/master/LICENSE.md) |
| Hugging Face Hub | 2.2.0 | [Apache-2.0](https://github.com/huggingface/huggingface_hub/blob/main/LICENSE) |
| Whisper models and original code | Downloaded per model | [MIT](https://github.com/openai/whisper#license); converted model: [Systran faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small) |
| uv bootstrap installer | Pinned to 0.13.4 | [MIT or Apache-2.0](https://github.com/astral-sh/uv#license); the Windows x64 ZIP SHA-256 is pinned in the installer, based on the [official release assets](https://github.com/astral-sh/uv/releases/tag/0.13.4) |
| Python runtime | Python 3.12 resolved during installation | [Python license](https://docs.python.org/3/license.html); the uv-managed Python build carries its component licenses |
| Chromium | Installed with Playwright | [Chromium and third-party licenses](https://chromium.googlesource.com/chromium/src/+/main/LICENSE); downloaded artifacts include further component notices |

The source ZIP does not bundle these third-party binaries, models, or virtual environments. The installer obtains them through their distribution channels, and their own licenses continue to apply. A future offline installer bundling a browser, FFmpeg, models, or a runtime must also include the relevant copyright notices, licenses, and any applicable source-offer obligations. This project's MIT license alone does not cover those components.

Use `pip list`, installed `.dist-info` metadata, and the actual distribution artifacts to verify the full dependency versions and licenses. Do not include environment dumps, account configuration, cookies, or access tokens in a public inventory.
