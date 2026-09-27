# Stream Readable

A small TypeScript/React streaming-text component and latency lab maintained by [@Lemofly](https://github.com/Lemofly).

Publish the first readable fragment immediately. Coalesce later deltas into a browser frame. Measure where the wait happens instead of adding a typing animation.

**Initial code, tests, and documentation were generated with OpenAI Codex under the maintainer's direction.** This is an AI-assisted project, not a claim of manually written code. See [maintenance and provenance](docs/MAINTAINERS.md) and the commit history.

## What it improves

- Removes artificial sentence/typing waits in the display layer. An incomplete sentence can already be read.
- Reduces text publications during bursts while preserving delta order and every received character.
- Keeps one stable text node: old text does not remount or replay an entrance animation.
- Separates send preparation, time to first readable text, DOM commit, a visible-frame estimate, and the terminal event.
- Keeps received text on interruption/error, flushes queued deltas on termination, and ignores stale sessions after a restart.

**It cannot shorten model time to first text**, network latency, server queues, client preparation, or total model generation time. An existing immediate per-event renderer may already expose the first text just as quickly. Fewer store publications do not necessarily mean fewer React commits, faster CPU execution, or fewer painted frames.

This is plain text, not a Markdown renderer, sentence segmenter, provider SDK, or production chat application. It does not automatically scroll, virtualize history, reconnect, deduplicate retransmitted provider events, or cap response length. Memory grows with the response. The demo uses synthetic data only: no model calls, accounts, credentials, remote fonts, or external assets.

## Run locally

Node.js **24** is the tested version. Install dependencies from the committed lockfile:

```sh
git clone https://github.com/Lemofly/stream-readable.git
cd stream-readable
npm ci
npm run dev
```

Open **http://127.0.0.1:4318**. Select normal fragments, a 1,000-fragment burst, no sentence boundary, or a transport error. Run, stop, or restart the stream. All three panels receive the same HTTP response and arrival timestamps. Preparation delay is deliberately adjustable; it is an experiment control, not a library requirement.

The sentence-buffer panel is a comparison-only adapter. It waits for punctuation and flushes at a terminal event. It is intentionally not shipped as the recommended display policy or as a robust multilingual segmenter.

```sh
npm test                  # unit / React / transport tests
npm run build             # type check and demo production bundle
npm run build:lib          # ESM and declaration files in lib/
npm run benchmark         # deterministic prescribed-clock comparison
```

The mock endpoint is a **development-server-only** Vite middleware. A static `dist/` deployment or `vite preview` does not provide `/api/mock`. Production integrations must supply their own transport. This repository is public source; it is **not published on npm**.

## Reuse the library

Build `lib/` and import from `lib/index.js`, or integrate the small `src/` directory under its MIT license. React and React DOM 18+ are peer requirements; the lockfile uses the versions tested here. Import `src/style.css` separately. The source and API are independent of the demo transport.

```tsx
import { createTextStream, StreamingText } from './lib/index.js';
import './src/style.css';

const stream = createTextStream();
const session = stream.begin(performance.now()); // send-handler entry

// Immediately before your real fetch/provider request:
session.requestStarted();
// In your decoded delta callback, not after collecting a whole reply:
session.append('You can read');
session.append(' an unfinished sentence');
// At the provider's explicit end marker:
session.complete();

// Render with this same session in React:
<StreamingText stream={stream} session={session} />;
```

This snippet illustrates the API, not a provider integration. [The runnable demo](demo/main.tsx) owns the controller/session in React state, decodes the stream, aborts on stop, and handles restarts. [The example transport](demo/protocol.ts) is NDJSON, not SSE. Adapt your own parser to call `append(delta)` for **deltas**, not cumulative snapshots.

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

The first readable delta is published to the store without a frame timer. **React still schedules the DOM commit**; this library does not force synchronous paint or call `flushSync`. Later deltas use rAF with a 50 ms timer fallback for suspended frames. Browsers can throttle background timers, so 50 ms is not a hard delivery guarantee. Completion, interruption, and errors flush immediately without relying on rAF.

## Measurement contract

Use one monotonic `performance.now()` clock and one session per request. `durations(snapshot.marks)` returns `null` until a needed boundary exists; empty replies never invent a zero first-text latency.

| Metric | Boundary |
| --- | --- |
| `clickToRequestMs` | Send-handler entry → network-stack request start, or fetch dispatch fallback |
| `timeToFirstTextMs` | Request start → first decoded non-whitespace text |
| `firstTextToCommitMs` | First text → React layout-effect observation of the committed DOM |
| `firstTextToVisibleEstimateMs` | First text → post-commit frame sample with first-character viewport/clipping/opacity checks |
| `firstTextToEndMs` | First text → completion, interruption, or error; inspect `status` to distinguish them |

`requestStarted()` records API dispatch in `requestAt`. If available, pass the same-origin `PerformanceResourceTiming.requestStart` to `networkRequestStarted(at)`; `networkRequestAt` then refines the first two metrics. The demo does this after consuming the response. API invocation, browser network-stack start, and packets physically leaving the machine are different boundaries. ResourceTiming is not packet capture and does not expose exact wire time. Cross-origin detail can require Timing-Allow-Origin.

For comparison adapters that buffer input, call `textReceived(at)` at actual readable-text arrival **before** buffering, then append the released output. Otherwise you would incorrectly hide buffering time inside “time to first text.” See the sentence experiment in the demo.

The visible estimate uses two animation frames after commit and checks the first readable character's bounds, scroll-container clipping, document visibility, and ancestor opacity ≥95%. It does not observe exact physical display paint, occlusion by other windows/overlays, human eye movement, comprehension, or readability preference. Background/offscreen content may leave the visible timestamp `null` until it becomes visible. Sampling never delays content. Screenshots and browser checks validate behavior; they do not turn the estimate into an exact paint timestamp. See [requestAnimationFrame documentation](https://developer.mozilla.org/en-US/docs/Web/API/Window/requestAnimationFrame).

## Accessibility and motion

The reply is a labeled region. There is no per-token live announcement; applications should choose their own screen-reader policy, for example announcing completion. Real text has no fade, transform, or typing animation. The supplied styles honor `prefers-reduced-motion`; content remains immediate with either preference. Consumer CSS that hides/animates ancestors can still delay visibility and is outside this component's control. See [reduced-motion documentation](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion).

## Recorded evidence

Actual local results, environment, and limitations are in [verification](docs/verification.md), [browser measurements](docs/browser-results.json), and [deterministic results](docs/deterministic-results.json).

- 29 unit/React/transport tests passed on Node 24.20.0.
- Type check, demo build, and library build passed.
- Local Chrome browser harness: 8/8 checks passed with real loopback HTTP.
- In the deterministic 1,000-delta burst, frame batching published text twice versus 1,000 per-event publications; both retained all 1,000 characters. This is a scheduling/correctness result, **not a measured CPU speedup**.
- Same-stream browser runs show sentence buffering adding hundreds of milliseconds before visible text. The immediate per-event baseline was already fast. No human study established a readability winner.

Repeat browser checks without launching a separate browser process:

1. Start `npm run dev`.
2. Open **http://127.0.0.1:4318/browser-checks.html** in a foreground tab.
3. Click **Run browser checks**. The page reports assertions, timings, and environment as JSON.

A Playwright wrapper runs the same harness, checks reduced-motion emulation, and exercises the mobile demo:

```sh
npx playwright install chromium
npm run test:browser
# Optional: use an installed browser binary
# STREAM_BROWSER_PATH='/path/to/chrome' npm run test:browser
```

The agent's local shell could not launch Chrome under its sandbox. The recorded Mac browser results came from the foreground Chrome harness via the browser connector; they are not claimed as a successful local Playwright CLI run. GitHub CI runs the CLI checks independently.

## Maintenance

Issues and contributions are welcome with reproducible synthetic inputs. Preserve the timing definitions and distinguish experiments from measured product improvements. Please do not submit private chats, credentials, or proprietary application code. See [provenance and maintenance](docs/MAINTAINERS.md).

MIT licensed.
