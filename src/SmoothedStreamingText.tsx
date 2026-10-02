import {useLayoutEffect,useRef} from 'react';
import type {StreamSession,TextStream} from './stream.js';
import {observeFirstVisible} from './visibility.js';
import {createReveal,graphemes} from './reveal.js';

/** Append each new grapheme in order; previously displayed text is never animated again. */
export function SmoothedStreamingText({stream,session,label='Smoothed streaming reply',className=''}: {
  stream:TextStream; /** Pass the session only if you want timing marks (first commit, first visible). */
  session?:StreamSession|null; label?:string; className?:string;
}) {
  const element = useRef<HTMLDivElement>(null);
  const rendered = useRef<{stream:TextStream|null;id:number;text:string}>({stream:null,id:0,text:''});
  useLayoutEffect(() => {
    const container = element.current!;
    const reveal = createReveal();
    let cancelProbe: (()=>void) | null = null;
    const update = () => {
      const state = stream.getSnapshot();
      if (stream !== rendered.current.stream || state.id !== rendered.current.id || !state.text.startsWith(rendered.current.text)) {
        reveal.settle(); cancelProbe?.(); cancelProbe = null;
        container.replaceChildren();
        rendered.current = {stream,id:state.id,text:''};
      }
      if (state.text !== rendered.current.text) {
        const addition = state.text.slice(rendered.current.text.length);
        const fragment = document.createDocumentFragment();
        const nodes = graphemes(addition).map(glyph=>{
          const span = document.createElement('span');
          span.className = 'rsm-append';
          span.textContent = glyph;
          fragment.append(span);
          return span;
        });
        // Append synchronously, even when motion is disabled or animation is unavailable.
        rendered.current.text = state.text;
        container.append(fragment);
        reveal.play(nodes,state.status === 'streaming');
      }
      // Let the final fragment finish its reveal. Stop and error reveal all text at once.
      if (state.status === 'interrupted' || state.status === 'error') reveal.settle();
      if (session?.id === state.id && state.text.trim()) {
        if (state.marks.firstCommitAt === null) session.committed();
        if (stream.getSnapshot().marks.firstVisibleAt === null && cancelProbe === null) cancelProbe = observeFirstVisible(container,session);
      }
    };
    const unsubscribe = stream.subscribe(update);
    update();
    return () => { unsubscribe(); reveal.dispose(); cancelProbe?.(); };
  },[stream,session]);
  return <div ref={element} className={`rsm-text rsm-smoothed ${className}`} role="region" aria-label={label}/>;
}
