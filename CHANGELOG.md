# Changelog

## 2026-09-27 — Left-to-right letter reveal

- New text enters the DOM immediately and reveals from left to right, grapheme by grapheme, with opacity 0 → 1 and a slight horizontal motion. Existing text never replays its animation.
- Stop and transport errors cancel active animations and make all received text fully visible. Restart isolates the new reply from stale callbacks. Normal completion lets the final reveal finish.
- `prefers-reduced-motion` disables decoration; enabling it during playback cancels active effects immediately. Unsupported animation APIs fall back to fully visible text.
- Burst protection skips animation for additions exceeding 96 graphemes or when an addition would exceed 160 active animations. Stagger delay is capped at 220 ms. This bounds animation work, not total DOM size or response memory.

This is a visual effect, not faster model generation or networking. Text is present in the DOM before animation, but the opacity-zero reveal can delay visual readability, including for the first fragment. Historical measurements in the earlier verification documents describe the older opacity 0.8 → 1 effect and are not results for this update.

Validation for this update: 42/42 unit tests passed; demo and library builds passed; the current foreground Chrome harness passed 16/16 checks, including HTTP bursts/errors and simulated live reduced-motion changes. Playwright CLI tests could not start because its browser executable was missing; no CLI or CI pass is claimed. These checks were rerun against commit `dada893`, not copied from prior releases.
