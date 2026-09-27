import {useLayoutEffect, useRef, useSyncExternalStore} from 'react';
import type {StreamSession, TextStream} from './stream.js';

export function useStreamingText(stream: TextStream) {
  return useSyncExternalStore(stream.subscribe,stream.getSnapshot,stream.getSnapshot);
}

/** Check the first readable character, including scroll-container clipping and ancestor visibility. */
function firstCharacterVisible(element: HTMLElement): boolean {
  if (document.visibilityState !== 'visible') return false;
  const node = element.firstChild;
  const offset = element.textContent?.search(/\S/) ?? -1;
  if (!node || offset < 0 || node.nodeType !== Node.TEXT_NODE) return false;
  const range = document.createRange();
  range.setStart(node,offset); range.setEnd(node,offset+1);
  const box = range.getBoundingClientRect();
  if (box.width <= 0 || box.height <= 0 || box.bottom <= 0 || box.top >= innerHeight || box.right <= 0 || box.left >= innerWidth) return false;
  let opacity = 1;
  for (let parent: HTMLElement | null = element; parent; parent = parent.parentElement) {
    const style = getComputedStyle(parent);
    opacity *= Number(style.opacity);
    if (style.visibility !== 'visible' || style.display === 'none' || opacity < 0.95) return false;
    if (/(auto|scroll|hidden|clip)/.test(style.overflowX + style.overflowY)) {
      const clip = parent.getBoundingClientRect();
      if (box.bottom <= clip.top || box.top >= clip.bottom || box.right <= clip.left || box.left >= clip.right) return false;
    }
  }
  return true;
}

/** Plain text by design: one stable text node, escaped by React, no per-token remounts. */
export function StreamingText({stream,session,label = 'Streaming reply',className = ''}: {
  stream: TextStream; session: StreamSession | null; label?: string; className?: string;
}) {
  const state = useStreamingText(stream);
  const element = useRef<HTMLDivElement>(null);
  const hasText = /\S/.test(state.text);
  useLayoutEffect(() => {
    if (!session || session.id !== state.id || !hasText || !element.current) return;
    session.committed();
    if (stream.getSnapshot().marks.firstVisibleAt !== null) return;
    let frame = 0;
    let cancelled = false;
    // The second rAF samples after an intervening rendering opportunity, not exact physical paint.
    const sample = () => {
      if (cancelled) return;
      if (element.current && firstCharacterVisible(element.current)) session.visible();
      else frame = requestAnimationFrame(sample);
    };
    frame = requestAnimationFrame(() => { frame = requestAnimationFrame(sample); });
    return () => { cancelled = true; cancelAnimationFrame(frame); };
  },[state.id,hasText,session,stream]);
  return <div ref={element} className={`stream-readable ${className}`} role="region" aria-label={label}>{state.text}</div>;
}
