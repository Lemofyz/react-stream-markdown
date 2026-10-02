# Changelog

## 0.3.1 — 2026-10-02 — Renamed to react-stream-markdown

- The package, repository and demo are now `react-stream-markdown` (was `stream-readable`, which was easy to confuse with Node's `readable-stream`). The package had not been published under the old name.
- CSS classes use the short `rsm-` prefix: `rsm-markdown`, `rsm-text`, `rsm-smoothed`, `rsm-append`, `rsm-table`, `rsm-task`, `rsm-link-pending`.

## 0.3.0 — 2026-10-02 — Streaming Markdown

- New `StreamingMarkdown` component: renders replies as Markdown while they stream. Finished blocks are frozen; only the block being written is re-parsed and patched, so visible text is never rebuilt.
- Unfinished syntax renders optimistically while streaming (`**bold`, inline code, open code fences, table headers without a separator row).
- Optional fade-in applies only to newly arrived characters, shared with `SmoothedStreamingText`; animated spans merge back into plain text nodes when they finish.
- DOM is built without `innerHTML`; raw HTML renders as text, and only `http(s)`, `mailto` and relative links are linked. Images render as links.
- New static landing demo (`index.html`) comparing "wait for the full reply" with streaming, in English and Chinese. The dev-server latency lab moved to `lab.html`.
- Package is ready to publish to npm (`npm i stream-readable`). README rewritten; measurement details moved to `docs/measurement.md`.
- Added a GitHub Pages workflow for the demo.

## 2026-09-27 — Left-to-right letter reveal

- New text enters the DOM immediately and reveals from left to right, grapheme by grapheme, with opacity 0 → 1 and a slight horizontal motion. Existing text never replays its animation.
- Stop and transport errors cancel active animations and make all received text fully visible. Restart isolates the new reply from stale callbacks. Normal completion lets the final reveal finish.
- `prefers-reduced-motion` disables decoration; enabling it during playback cancels active effects immediately. Unsupported animation APIs fall back to fully visible text.
- Burst protection skips animation for additions exceeding 96 graphemes or when an addition would exceed 160 active animations. Stagger delay is capped at 220 ms. This bounds animation work, not total DOM size or response memory.

This is a visual effect, not faster model generation or networking. Text is present in the DOM before animation, but the opacity-zero reveal can delay visual readability, including for the first fragment. Historical measurements in the earlier verification documents describe the older opacity 0.8 → 1 effect and are not results for this update.

Validation for this update: 42/42 unit tests passed; demo and library builds passed; the current foreground Chrome harness passed 16/16 checks, including HTTP bursts/errors and simulated live reduced-motion changes. Playwright CLI tests could not start because its browser executable was missing; no CLI or CI pass is claimed. These checks were rerun against commit `dada893`, not copied from prior releases.
