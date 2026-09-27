# Verification record

2026-09-27, Mac, Node 24.20.0, foreground Chrome 152.0.0.0, 1598×793 viewport, system reduced-motion preference false. Dependencies are pinned by `package-lock.json`.

## Local commands

```text
npm test
Test Files  3 passed (3)
Tests       29 passed (29)

npm run build
TypeScript: passed
Vite production build: passed (20 modules)

npm run build:lib
TypeScript ESM/declarations: passed

npm run benchmark
1,000 deltas → 2 batched text publications or 1,000 per-event publications
Both outputs: 1,000 characters
Prescribed-clock first-release extra wait: 360 ms with punctuation,
720 ms without punctuation; 0 ms for both immediate policies
```

The deterministic benchmark intentionally uses a fake clock/scheduler. It establishes buffering delay and lossless batching, not measured hardware performance. Commit/visible marks remain null in that Node-only experiment.

## Actual browser execution

The foreground local `/browser-checks.html` harness passed **8/8** checks using real loopback HTTP. It verified incomplete-sentence display before completion, burst correctness, abort before first text, interruption retention, restart isolation, transport failure retention, literal HTML escaping, and fully opaque unanimated content. The 1,001-fragment HTTP test published text **3** times; this count can vary with network packet/read/frame grouping. All characters survived.

The browser connector ran the harness because spawning Chrome from the agent's shell was restricted. This is not described as a successful local Playwright CLI execution. `npm run test:browser` and GitHub CI wrap the same harness, emulate reduced motion, and check a mobile viewport. Their outcomes must be checked separately.

Three completed normal demo runs shared each HTTP response between all policies. Measured ranges (rounded to 0.1 ms):

| Boundary | Frame batching | Per event | Sentence experiment |
| --- | ---: | ---: | ---: |
| Click → network request start | 84.1–86.0 ms | same request | same request |
| Request start → first readable text | 201.9–203.2 ms | same arrivals | same arrivals |
| First text → DOM commit | 3.0–4.8 ms | 3.0–4.8 ms | 361.1–364.9 ms |
| First text → visible estimate | 7.9–15.9 ms | 10.2–18.0 ms | 370.6–374.4 ms |
| First text → completion | 899.5–900.6 ms | same stream | same stream |

One additional no-punctuation browser trial measured first-text-to-visible estimates of **8.4 ms** (frame), **10.5 ms** (event), and **918.3 ms** (sentence). The sentence adapter waited until the terminal event.

Preparation is configured to 80 ms, the mock first-text delay to 200 ms. These are synthetic values plus local overhead, not model timings. A sentence buffer visibly delayed the first text here. The immediate baseline already had comparable first-text speed. There is no statistically powered performance comparison, CPU profile, cross-browser guarantee, or human-readability experiment.

See [machine-readable browser evidence](browser-results.json) and [deterministic results](deterministic-results.json). The reply itself has no animation, so content is immediate under either motion preference; a real reduced-motion browser run is part of the CI wrapper.

## Repeat

Run `npm ci`, `npm test`, `npm run build`, `npm run build:lib`, `npm run benchmark`. Then run `npm run dev` and use the demo and `/browser-checks.html` in a foreground browser. For the Playwright wrapper, install its Chromium binary and run `npm run test:browser`. Test output changes with the machine; do not expect exact wall-clock values from the recorded table.
