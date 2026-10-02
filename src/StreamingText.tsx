import {useLayoutEffect, useRef, useSyncExternalStore} from 'react';
import type {StreamSession, TextStream} from './stream.js';
import {observeFirstVisible} from './visibility.js';

export function useStreamingText(stream: TextStream) {
  return useSyncExternalStore(stream.subscribe,stream.getSnapshot,stream.getSnapshot);
}

/** Plain text by design: one stable text node, escaped by React, no per-token remounts. */
export function StreamingText({stream,session,label = 'Streaming reply',className = ''}: {
  stream: TextStream; /** Pass the session only if you want timing marks (first commit, first visible). */
  session?: StreamSession | null; label?: string; className?: string;
}) {
  const state = useStreamingText(stream);
  const element = useRef<HTMLDivElement>(null);
  const hasText = /\S/.test(state.text);
  useLayoutEffect(() => {
    if (!session || session.id !== state.id || !hasText || !element.current) return;
    session.committed();
    if (stream.getSnapshot().marks.firstVisibleAt !== null) return;
    return observeFirstVisible(element.current,session);
  },[state.id,hasText,session,stream]);
  return <div ref={element} className={`rsm-text ${className}`} role="region" aria-label={label}>{state.text}</div>;
}
