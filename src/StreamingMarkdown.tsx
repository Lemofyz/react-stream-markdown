import {useLayoutEffect,useRef} from 'react';
import type {StreamSession,TextStream} from './stream.js';
import {parseBlocks} from './markdown.js';
import {blockToVNode,commonPrefix,patchChildren,textOf,type PatchContext,type VNode} from './markdownDom.js';
import {observeFirstVisible} from './visibility.js';
import {createReveal} from './reveal.js';

interface Rendered {stream:TextStream|null;id:number;text:string;open:boolean;offset:number;live:Node[];vnodes:VNode[]}
const fresh = (stream: TextStream|null = null,id = 0): Rendered => ({stream,id,text:'',open:true,offset:0,live:[],vnodes:[]});

/**
 * Render a growing Markdown reply. Finished blocks are frozen and never touched again; only the
 * block still being written is re-parsed, so each update costs the size of that block, not the
 * whole reply. New characters fade in left to right (optional); visible text never replays.
 */
export function StreamingMarkdown({stream,session,label='Streaming reply',className='',animate=true}: {
  stream:TextStream; session:StreamSession|null; label?:string; className?:string;
  /** Fade in newly arrived characters. Reduced-motion users never see the effect. */
  animate?:boolean;
}) {
  const element = useRef<HTMLDivElement>(null);
  const rendered = useRef<Rendered>(fresh());
  const animateRef = useRef(animate);
  animateRef.current = animate;
  useLayoutEffect(() => {
    const container = element.current!;
    // Text wrappers with running reveals. When a wrapper's reveals end, its spans merge back into one text node.
    const running = new Map<HTMLElement,number>();
    const owner = new WeakMap<HTMLElement,HTMLElement>();
    const collapse = (wrapper: HTMLElement) => {
      if (running.get(wrapper)) return;
      running.delete(wrapper);
      if (wrapper.children.length) wrapper.textContent = wrapper.textContent;
    };
    const reveal = createReveal(span=>{
      const wrapper = owner.get(span);
      if (!wrapper) return;
      running.set(wrapper,(running.get(wrapper) ?? 1) - 1);
      collapse(wrapper);
    });
    let cancelProbe: (()=>void) | null = null;
    const update = () => {
      const state = stream.getSnapshot();
      const open = state.status === 'streaming';
      let r = rendered.current;
      if (stream !== r.stream || state.id !== r.id || !state.text.startsWith(r.text)) {
        reveal.settle(); running.clear(); cancelProbe?.(); cancelProbe = null;
        container.replaceChildren();
        r = rendered.current = fresh(stream,state.id);
      }
      if (state.text !== r.text || open !== r.open) {
        const source = state.text.slice(r.offset);
        const blocks = parseBlocks(source,open);
        const vnodes = blocks.map(b=>blockToVNode(b.block)[0]);
        const ctx: PatchContext = {offset:0,revealFrom:animateRef.current ? commonPrefix(textOf(r.vnodes),textOf(vnodes)) : Infinity,spans:[],touched:new Set()};
        r.live = patchChildren(container,r.live,r.vnodes,vnodes,ctx);
        r.vnodes = vnodes;
        r.text = state.text;
        r.open = open;
        for (const span of ctx.spans) owner.set(span,span.parentElement!);
        if (reveal.play(ctx.spans,open)) {
          for (const span of ctx.spans) if (span.dataset.revealing) running.set(owner.get(span)!,(running.get(owner.get(span)!) ?? 0) + 1);
        }
        ctx.touched.forEach(collapse);
        // Freeze every block followed by a block whose first line is complete: nothing later can change it.
        const lastBreak = source.lastIndexOf('\n');
        let frozen = 0;
        while (open && frozen + 1 < blocks.length && blocks[frozen + 1].start <= lastBreak) frozen++;
        if (frozen) {
          r.offset += blocks[frozen].start;
          r.live = r.live.slice(frozen);
          r.vnodes = r.vnodes.slice(frozen);
        }
      }
      // Let the final characters finish their reveal. Stop and error reveal all text at once.
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
  return <div ref={element} className={`stream-readable-md ${className}`} role="region" aria-label={label}/>;
}
