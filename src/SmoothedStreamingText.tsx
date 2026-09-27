import {useLayoutEffect,useRef} from 'react';
import type {StreamSession,TextStream} from './stream.js';
import {observeFirstVisible} from './visibility.js';

/**
 * React owns the container; this subscription owns only its plain-text descendants.
 * Use createTextStream({batch:false}) for synchronous DOM appends on each received delta.
 * Each appended span is stable. Animation is decoration, never a gate for content.
 */
export function SmoothedStreamingText({stream,session,label='Smoothed streaming reply',className=''}: {
  stream:TextStream; session:StreamSession|null; label?:string; className?:string;
}) {
  const element = useRef<HTMLDivElement>(null);
  const rendered = useRef<{stream:TextStream|null;id:number;text:string}>({stream:null,id:0,text:''});
  useLayoutEffect(() => {
    const container = element.current!;
    const animations = new Map<Animation,HTMLSpanElement>();
    const motion = typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : null;
    let cancelProbe: (()=>void) | null = null;
    const settle = () => {
      for (const [animation,span] of animations) {
        animation.cancel(); // Underlying opacity is 1; no fill or inline .8 can remain.
        span.removeAttribute('data-clarifying');
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
        const firstReadable = rendered.current.text.trim() === '';
        const span = document.createElement('span');
        span.className = 'stream-readable-append';
        span.textContent = state.text.slice(rendered.current.text.length);
        // Commit before any animation setup. Re-entrant metric notifications cannot duplicate text.
        rendered.current.text = state.text;
        container.append(span);
        if (!firstReadable && state.status === 'streaming' && !motion?.matches && document.visibilityState === 'visible' && typeof span.animate === 'function') {
          try {
            const animation = span.animate([{opacity:0.8},{opacity:1}],{duration:100,easing:'ease-out',fill:'none'});
            animations.set(animation,span);
            span.dataset.clarifying = 'true';
            const forget = () => { animations.delete(animation); span.removeAttribute('data-clarifying'); };
            animation.addEventListener('finish',forget,{once:true});
            animation.addEventListener('cancel',forget,{once:true});
          } catch { /* Unsupported animation never blocks or hides the committed text. */ }
        }
      }
      if (state.status !== 'streaming') settle();
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
