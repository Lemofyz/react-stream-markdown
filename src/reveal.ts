const keyframes = [{opacity:0,transform:'translateX(-2px)'},{opacity:1,transform:'translateX(0)'}];
const segmenter = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter(undefined,{granularity:'grapheme'}) : null;

/** Split into user-perceived characters so emoji and combining marks animate as one unit. */
export function graphemes(text: string): string[] {
  return segmenter ? Array.from(segmenter.segment(text),part=>part.segment) : Array.from(text);
}

/**
 * Left-to-right fade for newly appended spans. Text is always in the DOM at full base opacity;
 * the animation only holds it transparent until its staggered start.
 */
export function createReveal(onDone?: (span: HTMLElement) => void) {
  const animations = new Map<Animation,HTMLElement>();
  const motion = typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : null;
  const settle = () => {
    for (const [animation,span] of animations) {
      animation.cancel(); // Underlying opacity is 1; no hidden text remains.
      span.removeAttribute('data-revealing');
    }
    animations.clear();
  };
  const onMotionChange = () => { if (motion?.matches) settle(); };
  const onVisibilityChange = () => { if (document.visibilityState !== 'visible') settle(); };
  motion?.addEventListener('change',onMotionChange);
  document.addEventListener('visibilitychange',onVisibilityChange);
  return {
    /** Returns false when the text should simply appear (reduced motion, hidden tab, or a large burst). */
    play(spans: HTMLElement[],streaming: boolean): boolean {
      // Avoid unbounded DOM animation work in a burst. All text remains present and readable.
      if (!streaming || motion?.matches || document.visibilityState !== 'visible' || spans.length > 96 || animations.size + spans.length > 160) return false;
      const step = Math.min(22, 220 / Math.max(1,spans.length - 1));
      spans.forEach((span,index)=>{
        if (typeof span.animate !== 'function') return;
        try {
          const animation = span.animate(keyframes,{duration:180,delay:index*step,easing:'ease-out',fill:'backwards'});
          animations.set(animation,span);
          span.dataset.revealing = 'true';
          let done = false;
          const forget = () => {
            if (done) return;
            done = true;
            animations.delete(animation);
            span.removeAttribute('data-revealing');
            onDone?.(span);
          };
          animation.addEventListener('finish',forget,{once:true});
          animation.addEventListener('cancel',forget,{once:true});
        } catch { /* No animation support: the underlying text is fully visible. */ }
      });
      return true;
    },
    settle,
    dispose() {
      motion?.removeEventListener('change',onMotionChange);
      document.removeEventListener('visibilitychange',onVisibilityChange);
      settle();
    },
  };
}
export type Reveal = ReturnType<typeof createReveal>;
