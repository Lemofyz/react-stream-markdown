import type {MathRenderer} from './markdownDom.js';

/** The part of KaTeX's API this adapter uses, so the package does not depend on KaTeX. */
export interface KatexLike {render(tex: string,element: HTMLElement,options?: Record<string,unknown>): void}

/**
 * Typeset math with KaTeX: `<StreamingMarkdown math={katexMath(katex)} />`. Create it once, outside
 * the component. KaTeX builds DOM nodes (no innerHTML); `trust` stays off so model output cannot
 * use `\href`, `\htmlClass` and similar. Invalid TeX is shown inline in KaTeX's error color.
 */
export function katexMath(katex: KatexLike,options: Record<string,unknown> = {}): MathRenderer {
  return (tex,target,{displayMode}) => katex.render(tex,target,{throwOnError:false,trust:false,maxSize:20,maxExpand:500,...options,displayMode});
}
