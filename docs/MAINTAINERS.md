# Maintenance and provenance

Maintainer: [@Lemofly](https://github.com/Lemofly).

The initial implementation, demo, tests, and documentation were generated with OpenAI Codex under the maintainer's direction. This repository does not claim that the maintainer manually wrote the generated code. Commits label the AI-assisted work and keep the implementation, verification, and documentation separate for review and learning.

Maintenance is a continuing responsibility, not an automated guarantee. Please open reproducible issues with synthetic inputs, expected behavior, actual behavior, and runtime versions. Do not attach private chats, access tokens, or application credentials.

Subsequent smoothing implementation, tests, and documentation are also AI-assisted. Original retains its frame-batched behavior; Smoothed is an optional unbatched view with per-addition decoration. Keep this distinction visible in performance claims.

Before changing scheduling behavior, run unit tests, the library build, and the local browser harness. Preserve the first-fragment behavior, delta order, terminal flushing, stale-session isolation, and measurement caveats. Add a test for an actual new failure rather than snapshotting every implementation detail.
