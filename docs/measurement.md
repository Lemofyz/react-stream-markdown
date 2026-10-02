# Latency lab and measurement notes

The landing demo (`index.html`) shows the reading head start. This page covers the deeper tooling: the dev-server latency lab, the timing contract, and the plain-text renderers. Everything here is optional; `StreamingMarkdown` needs none of it.

## Latency lab

Run `npm run dev`. Open **http://127.0.0.1:4318/lab.html**. Select normal fragments, a 1,000-fragment burst, no sentence boundary, or a transport error. Run, stop, or restart the stream. Original / Letter reveal appear side by side and receive the same HTTP response and timestamps. Per-event and sentence-buffer experiments remain under Additional timing experiments. Preparation delay is deliberately adjustable; it is an experiment control, not a library requirement.

For a reproducible **live before/after comparison**, select **Playback → Slow · 4× arrival intervals**, keep Normal fragments, and click Run stream. Both replies are identical; the server stretches intervals equally, preserving the synthetic 200 ms first-text schedule. Slow mode takes about 3.8 seconds from request dispatch to completion. Watch the second and later fragments, then try Stop and Restart during playback.

You can also open **http://127.0.0.1:4318/lab.html?playback=slow&scenario=normal&autoplay=1** to start the same comparison automatically. The Shared fragment arrival timestamps disclosure records the single receive timeline used by both panels. Repeat with a burst, no sentence boundary, or a transport error. This is a moving comparison, not just a final screenshot; no model or production speed claim is made.

The sentence-buffer panel is a comparison-only adapter. It waits for punctuation and flushes at a terminal event. It is intentionally not shipped as the recommended display policy or as a robust multilingual segmenter.

The mock endpoint is a **development-server-only** Vite middleware, so the lab is not part of the static demo build.

```sh
npm run benchmark         # deterministic prescribed-clock comparison
```

## Plain-text renderers

`StreamingText` keeps one stable text node and batches updates per frame (the first readable delta is published immediately). `SmoothedStreamingText` is the plain-text letter reveal.

For letter reveal, mount `SmoothedStreamingText` with a controller created using `createTextStream({batch:false})`. The exported component name remains unchanged for compatibility. Its DOM subscription appends each delta synchronously before `append` returns, then animates **only newly appended graphemes**, including the first fragment. React owns the container; the component owns its escaped, plain-text descendants. It does not call `flushSync` or force physical paint. A browser can still coalesce rendering opportunities.

```tsx
import { SmoothedStreamingText, createTextStream } from './lib/index.js';
const smoothed = createTextStream({batch:false});
const smoothedSession = smoothed.begin();
// Mount before consuming your transport, as in demo/main.tsx:
<SmoothedStreamingText stream={smoothed} session={smoothedSession} />;
// In the actual request/delta callbacks:
smoothedSession.requestStarted();
smoothedSession.append('Letters fade in from zero opacity');
smoothedSession.append(' as each new fragment arrives');
```

Base opacity is always 1. Web Animations holds incoming letters at zero opacity until their staggered reveal begins; after completion there is no persistent fill. Normal stream completion allows the last letters to finish fading. Stop, error, restart, unmount, backgrounding, and a reduced-motion preference change cancel active animations and leave all received text opaque. Unsupported animation APIs simply show fully opaque text. There is no sentence buffer or old-text animation. See [Element.animate](https://developer.mozilla.org/en-US/docs/Web/API/Element/animate).

**Tradeoff:** Letter reveal uses an unbatched subscription and one span per grapheme. It performs more DOM and animation work than Original and delays full readability by a few hundred milliseconds. To bound animation work, a delta over 96 graphemes or a burst exceeding 160 active animations displays the new text immediately. With a frame-batched controller it can only render what the controller publishes, so use `batch:false` to meet the immediate-per-delta contract. Memory/DOM size grows with response length.

Other lifecycle operations:

```ts
session.interrupt();          // flush pending deltas, retain partial reply
session.fail(new Error('Connection lost')); // same retention, error status
const newer = stream.begin(); // cancels queued work; old sessions become inert
newer.requestStarted();
// AbortController.abort() is separate: this accumulator does not own the network.
stream.dispose();            // final owner teardown; controller cannot be reused
```

`useStreamingText(stream)` exposes an immutable snapshot through `useSyncExternalStore`. A controller is owned by its caller; unmounting `StreamingText` cancels its visibility probe but does not dispose a shared controller. Do not dispose a reusable controller during React StrictMode's effect replay. Stop the transport on owner cleanup; dispose only when its lifetime is definitively over.

In the Original renderer, the first readable delta is published to the store without a frame timer. **React still schedules the DOM commit**; this library does not force synchronous paint or call `flushSync`. Later deltas use rAF with a 50 ms timer fallback for suspended frames. Browsers can throttle background timers, so 50 ms is not a hard delivery guarantee. Completion, interruption, and errors flush immediately without relying on rAF.

## Measurement contract

Use one monotonic `performance.now()` clock and one session per request. `durations(snapshot.marks)` returns `null` until a needed boundary exists; empty replies never invent a zero first-text latency.

| Metric | Boundary |
| --- | --- |
| `clickToRequestMs` | Send-handler entry → network-stack request start, or fetch dispatch fallback |
| `timeToFirstTextMs` | Request start → first decoded non-whitespace text |
| `firstTextToCommitMs` | First text → observation of committed DOM (Original layout effect; Smoothed synchronous DOM append) |
| `firstTextToVisibleEstimateMs` | First text → post-commit frame sample with first-character viewport/clipping/opacity checks |
| `firstTextToEndMs` | First text → completion, interruption, or error; inspect `status` to distinguish them |

`requestStarted()` records API dispatch in `requestAt`. If available, pass the same-origin `PerformanceResourceTiming.requestStart` to `networkRequestStarted(at)`; `networkRequestAt` then refines the first two metrics. The demo does this after consuming the response. API invocation, browser network-stack start, and packets physically leaving the machine are different boundaries. ResourceTiming is not packet capture and does not expose exact wire time. Cross-origin detail can require Timing-Allow-Origin.

For comparison adapters that buffer input, call `textReceived(at)` at actual readable-text arrival **before** buffering, then append the released output. Otherwise you would incorrectly hide buffering time inside “time to first text.” See the sentence experiment in the demo.

The timing definitions and subtraction formulas are unchanged. Both renderers use the same first-readable-character probe, including text inside spans. Letter reveal animates the first fragment too, so its first-visible estimate may be later than Original even when both receive the same first text at the same instant.

The visible estimate uses two animation frames after commit and checks the first readable character's bounds, scroll-container clipping, document visibility, and ancestor opacity ≥95%. It does not observe exact physical display paint, occlusion by other windows/overlays, human eye movement, comprehension, or readability preference. Background/offscreen content may leave the visible timestamp `null` until it becomes visible. Sampling never delays content. Screenshots and browser checks validate behavior; they do not turn the estimate into an exact paint timestamp. See [requestAnimationFrame documentation](https://developer.mozilla.org/en-US/docs/Web/API/Window/requestAnimationFrame).

## Accessibility and motion

The reply is a labeled region. There is no per-token live announcement; applications should choose their own screen-reader policy, for example announcing completion. Original has no text animation. Letter reveal briefly hides each new grapheme, including the first one, and moves it 2 px to the right while fading in. It honors `prefers-reduced-motion` on initial mount and live preference changes; reduced motion disables/cancels decoration without replay. Consumer CSS that hides/animates ancestors can still delay visibility and is outside this component's control. See [reduced-motion documentation](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion).

## Recorded evidence

The [previous smoothing verification](docs/smoothing-verification.md) and [measurements](docs/smoothing-results.json) describe the earlier 0.8→1 design and do not measure the current letter reveal. Initial-release local results, environment, and limitations are retained in [verification](docs/verification.md), [browser measurements](docs/browser-results.json), and [deterministic results](docs/deterministic-results.json).

- Earlier release: 45 unit/React/transport/smoothing tests passed on Node 24.20.0; type check, demo build, and library build passed.
- Earlier release: local Chrome browser harness 16/16 checks passed for the previous animation. Run the checks below to verify the current letter reveal on your machine.
- In the deterministic 1,000-delta burst, frame batching published text twice versus 1,000 per-event publications; both retained all 1,000 characters. This is a scheduling/correctness result, **not a measured CPU speedup**.
- Same-stream browser runs show sentence buffering adding hundreds of milliseconds before visible text. The immediate per-event baseline was already fast. No human study established a readability winner.

Repeat browser checks without launching a separate browser process:

1. Start `npm run dev`.
2. Open **http://127.0.0.1:4318/browser-checks.html** in a foreground tab.
3. Click **Run browser checks**. The page reports assertions, timings, and environment as JSON.

A Playwright wrapper runs the same harness, checks reduced-motion emulation, and exercises the mobile and slow comparison:

```sh
npx playwright install chromium
npm run test:browser
# Optional: use an installed browser binary
# STREAM_BROWSER_PATH='/path/to/chrome' npm run test:browser
```

The agent's local shell could not launch Chrome under its sandbox. The recorded Mac browser results came from the foreground Chrome harness via the browser connector; they are not claimed as a successful local Playwright CLI run. An inactive [CI configuration example](.github/ci-example.yml) runs the CLI checks when moved to `.github/workflows/ci.yml`. It is not enabled: the publishing credential lacks GitHub workflow scope. No CI success is claimed.
