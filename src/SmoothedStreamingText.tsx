import {useLayoutEffect,useRef} from 'react';
import type {StreamSession,TextStream} from './stream.js';
import {observeFirstVisible} from './visibility.js';

/** Append each new grapheme in order; previously displayed text is never animated again. */
export function SmoothedStreamingText({stream,session,label='Smoothed streaming reply',className=''}: {
  stream:TextStream; session:StreamSession|null; label?:string; className?:string;
}) {
  const element = useRef<HTMLDivElement>(null);
  const rendered = useRef<{stream:TextStream|null;id:number;text:string}>({stream:null,id:0,text:''});
  useLayoutEffect(() => {
    const container = element.current!;
    const animations = new Map<Animation,HTMLSpanElement>();
    const segmenter = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter(undefined,{granularity:'grapheme'}) : null;
    const motion = typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : null;
    let cancelProbe: (()=>void) | null = null;
    const settle = () => {
      for (const [animation,span] of animations) {
        animation.cancel(); // Underlying opacity is 1; no hidden text remains.
        span.removeAttribute('data-revealing');
      }
      animations.clear();
    };
    const onMotionChange = () => { if (motion?.matches) settle(); };
    const onVisibilityChange = () => { if (document.visibilityState !== 'visible') settle(); };
    const update = () => {
      const state = stream.getSnapshot();
      if (stream !== rendered.current.stream || state.id !== rendered.current.id || !state.text.startsWith(rendered.current.text)) {
        settle(); cancelProbe?.(); cancelProbe = null;
        container.replaceChildren();
        rendered.current = {stream,id:state.id,text:''};
      }
      if (state.text !== rendered.current.text) {
        const addition = state.text.slice(rendered.current.text.length);
        const graphemes = segmenter ? Array.from(segmenter.segment(addition),part=>part.segment) : Array.from(addition);
        const fragment = document.createDocumentFragment();
        const nodes = graphemes.map(glyph=>{
          const span = document.createElement('span');
          span.className = 'stream-readable-append';
          span.textContent = glyph;
          fragment.append(span);
          return span;
        });
        // Append synchronously, even when motion is disabled or animation is unavailable.
        rendered.current.text = state.text;
        container.append(fragment);
        // Avoid unbounded DOM animation work in a burst. All text remains present and readable.
        if (state.status === 'streaming' && !motion?.matches && document.visibilityState === 'visible' && nodes.length <= 96 && animations.size + nodes.length <= 160) {
          const step = Math.min(22, 220 / Math.max(1,nodes.length - 1));
          nodes.forEach((span,index)=>{
            if (typeof span.animate !== 'function') return;
            try {
              const animation = span.animate([{opacity:0,transform:'translateX(-2px)'},{opacity:1,transform:'translateX(0)'}],
                {duration:180,delay:index*step,easing:'ease-out',fill:'backwards'});
              animations.set(animation,span);
              span.dataset.revealing = 'true';
              const forget = () => { animations.delete(animation); span.removeAttribute('data-revealing'); };
              animation.addEventListener('finish',forget,{once:true});
              animation.addEventListener('cancel',forget,{once:true});
            } catch { /* No animation support: the underlying text is fully visible. */ }
          });
        }
      }
      // Let the final fragment finish its reveal. Stop and error reveal all text at once.
      if (state.status === 'interrupted' || state.status === 'error') settle();
      if (session?.id === state.id && state.text.trim()) {
        if (state.marks.firstCommitAt === null) session.committed();
        if (stream.getSnapshot().marks.firstVisibleAt === null && cancelProbe === null) cancelProbe = observeFirstVisible(container,session);
      }
    };
    const unsubscribe = stream.subscribe(update);
    motion?.addEventListener('change',onMotionChange);
    document.addEventListener('visibilitychange',onVisibilityChange);
    update();
    return () => {
      unsubscribe(); settle(); cancelProbe?.();
      motion?.removeEventListener('change',onMotionChange);
      document.removeEventListener('visibilitychange',onVisibilityChange);
    };
  },[stream,session]);
  return <div ref={element} className={`stream-readable stream-readable-smoothed ${className}`} role="region" aria-label={label}/>;
}
