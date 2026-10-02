# Maintenance

Maintainer: [@Lemofyz](https://github.com/Lemofyz).

Please open reproducible issues with synthetic inputs, expected behavior, actual behavior, and runtime versions. Do not attach private chats, access tokens, or application credentials.

Original (`StreamingText`) keeps its frame-batched behavior; `SmoothedStreamingText` and `StreamingMarkdown` add an optional per-addition fade-in. Keep this distinction visible in performance claims.

Before changing scheduling behavior, run unit tests, the library build, and the local browser harness. Preserve the first-fragment behavior, delta order, terminal flushing, stale-session isolation, and measurement caveats. Add a test for an actual new failure rather than snapshotting every implementation detail.
