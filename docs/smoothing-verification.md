# Original / Smoothed verification — 0.2.0

2026-09-27, Mac, Node 24.20.0, Chrome 152.0.0.0, foreground viewport 1598×793. Initial-release evidence remains in [verification.md](verification.md).

## Implemented behavior

- **Original** retains the previous left-column renderer, first-text publication and subsequent frame batching. Its existing text node never replays an animation.
- **Smoothed** uses `createTextStream({batch:false})` and a synchronous DOM subscription. Each received delta is in the DOM before `append` returns, as escaped plain text in a stable span. There is no frame timer or sentence gate on the smoothed path.
- The first readable fragment is fully opaque and unanimated. Later newly appended spans have a 100 ms `opacity: 0.8 → 1` Web Animations API effect. Their underlying style is opacity 1; the effect has no persistent fill. Existing spans are never reanimated or replaced during append.
- Terminal events, restart, unmount, backgrounding, and a live reduced-motion preference change cancel active effects. Cancellation reveals underlying opacity 1 immediately. Unsupported animation APIs also fall back to full opacity.
- Both panels read **one** HTTP response with the same text and `receivedAt` values. The accumulator, network decoder, metric subtraction formulas, and first-visible threshold/frame definition are unchanged. The visibility probe now traverses text inside spans as well as a plain text node.
- Slow playback stretches the mock's post-first-text schedule by 4× equally for both panels. Its first-text schedule remains 200 ms. This is a simulator control, not a production/model optimization.

## Reproducible moving comparison

```sh
npm ci
npm run dev
```

Open **http://127.0.0.1:4318/?playback=slow&scenario=normal&autoplay=1**. Watch Original / Smoothed side by side as the same reply arrives for roughly 3.8 seconds. First fragments should look identical; watch only the newly appended text for the subtle 100 ms effect. Click Run stream to repeat, or Stop / Restart during playback. The Playback control also offers normal speed. Repeat with a burst or transport error. Shared fragment arrival timestamps expose the single receive timeline.

This live demo is the reproducible before/after artifact; a final screenshot alone cannot demonstrate the behavior. Static production output does not include the development-only mock endpoint.

## Local automated results

```text
Test Files  4 passed (4)
Tests       45 passed (45)
TypeScript and Vite production build: passed
Library ESM/declaration build: passed
Original + Smoothed ESM exports: PASS
```

The 16 new smoothing unit cases cover synchronous DOM insertion, 1,000 rapid ordered appends, stable nodes and finished effects, leading whitespace, completion/interruption/error cleanup, stale sessions, initial/live reduced motion, unsupported APIs, unmount, StrictMode, HTML escaping, and replacement of a controller with a shared text prefix.

The foreground `/browser-checks.html` harness passed **16/16 checks**: the 8 original checks plus 8 smoothing checks. Its measured result is stored with the browser observations in [smoothing-results.json](smoothing-results.json). It uses native browser animation APIs and actual loopback HTTP for bursts and errors. A simulated MediaQueryList additionally tests the actual live preference-change listener without changing the user's OS settings. The Playwright wrapper separately specifies real reduced-motion emulation and slow/mobile checks; local CLI process launch remains restricted and no successful CLI or remote CI execution is claimed. The repository's CI example remains inactive.

Native animation observations: the new span was readable at opacity 0.849431 during its 100 ms effect; the old first span stayed at 1, and the new span settled at 1. The HTTP burst retained 1,001 fragments / 2,021 characters with every decoded delta synchronously present in order; maximum observed append call was 3.9 ms in this run. The HTTP error retained all 73 received characters with every span fully opaque. These are local observations, not throughput guarantees.

## Three same-stream slow trials

| Metric (ms) | Original | Smoothed |
| --- | ---: | ---: |
| Send entry → network request start | 83.5–85.6 | exactly the same request |
| Network request start → first readable text | 202.5–202.8 | exactly the same arrival |
| First text → DOM commit observation | 2.1–10.5 | 0.1–0.4 |
| First text → first-visible frame estimate | 7.2–27.0 | 6.2–25.5 |
| First text → completion | 3599.5–3600.0 | 3599.6–3600.0 |

The synchronous adapter observes DOM insertion earlier than Original's React layout effect. This is not model acceleration, a precise physical paint measurement, or a statistically powered speedup claim. First text is never subject to the 100 ms clarification. Three trials on one machine do not establish general readability preference or cross-browser performance.

Smoothed creates one span per published delta and bypasses frame batching. During bursts it does more DOM/animation work than Original; it is a visual option with an explicit cost, not a CPU optimization. Browsers can coalesce physical paints even though each delta enters the DOM synchronously.
