import type {Block,Inline} from './markdown.js';
import {graphemes} from './reveal.js';

/** Minimal virtual node. Strings are text; everything is built with DOM APIs, never innerHTML. */
export type VNode = string | {tag:string;attrs?:Record<string,string>;children:VNode[]};

const el = (tag: string,children: VNode[],attrs?: Record<string,string>): VNode => attrs ? {tag,attrs,children} : {tag,children};

function inline(node: Inline): VNode {
  switch (node.type) {
    case 'text': return node.value;
    case 'br': return el('br',[]);
    case 'code': return el('code',[node.value]);
    case 'strong': case 'em': case 'del': return el(node.type,node.children.map(inline));
    case 'link': return node.href === null
      ? el('span',node.children.map(inline),{class:'rsm-link-pending'})
      : el('a',node.children.map(inline),{href:node.href,target:'_blank',rel:'noopener noreferrer nofollow'});
  }
}
export function blockToVNode(block: Block,tight = false): VNode[] {
  switch (block.type) {
    case 'paragraph': return tight ? block.children.map(inline) : [el('p',block.children.map(inline))];
    case 'heading': return [el(`h${block.level}`,block.children.map(inline))];
    case 'code': return [el('pre',[el('code',[block.value],block.lang ? {class:`language-${block.lang}`,'data-lang':block.lang} : undefined)])];
    case 'hr': return [el('hr',[])];
    case 'blockquote': return [el('blockquote',block.children.flatMap(b=>blockToVNode(b)))];
    case 'list': return [el(block.ordered ? 'ol' : 'ul',block.items.map(item=>{
      const children = item.children.flatMap(b=>blockToVNode(b,block.tight));
      if (item.checked === null) return el('li',children);
      const box = el('input',[],item.checked ? {type:'checkbox',disabled:'',checked:''} : {type:'checkbox',disabled:''});
      return el('li',[box,...children],{class:'rsm-task'});
    }),block.ordered && block.start !== 1 ? {start:String(block.start)} : undefined)];
    case 'table': {
      const cell = (tag: string,content: Inline[],c: number) => el(tag,content.map(inline),block.align[c] ? {'data-align':block.align[c]!} : undefined);
      const head = el('thead',[el('tr',block.head.map((c,i)=>cell('th',c,i)))]);
      const body = block.rows.length ? [el('tbody',block.rows.map(row=>el('tr',row.map((c,i)=>cell('td',c,i)))))] : [];
      return [el('div',[el('table',[head,...body])],{class:'rsm-table'})];
    }
  }
}

export const textOf = (nodes: VNode[]): string => nodes.map(n=>typeof n === 'string' ? n : textOf(n.children)).join('');
export function commonPrefix(a: string,b: string): number {
  const max = Math.min(a.length,b.length);
  let i = 0;
  while (i < max && a.charCodeAt(i) === b.charCodeAt(i)) i++;
  return i;
}

/** Shared state for one patch pass: text offset in document order, and which text is new. */
export interface PatchContext {
  offset: number;
  /** Text before this offset was already on screen and must not animate again. */
  revealFrom: number;
  spans: HTMLElement[];
  touched: Set<HTMLElement>;
}

function appendText(wrapper: HTMLElement,text: string,at: number,ctx: PatchContext) {
  let settled = '';
  let position = at;
  for (const glyph of graphemes(text)) {
    if (position < ctx.revealFrom) settled += glyph;
    else {
      if (settled) { wrapper.append(settled); settled = ''; }
      const span = document.createElement('span');
      span.className = 'rsm-append';
      span.textContent = glyph;
      wrapper.append(span);
      ctx.spans.push(span);
      ctx.touched.add(wrapper);
    }
    position += glyph.length;
  }
  if (settled) wrapper.append(settled);
}
function build(node: VNode,ctx: PatchContext): Node {
  if (typeof node === 'string') {
    const wrapper = document.createElement('span');
    appendText(wrapper,node,ctx.offset,ctx);
    ctx.offset += node.length;
    return wrapper;
  }
  const element = document.createElement(node.tag);
  for (const [name,value] of Object.entries(node.attrs ?? {})) element.setAttribute(name,value);
  for (const child of node.children) element.append(build(child,ctx));
  return element;
}
function syncAttrs(element: Element,before: Record<string,string> = {},after: Record<string,string> = {}) {
  for (const name of Object.keys(before)) if (!(name in after)) element.removeAttribute(name);
  for (const [name,value] of Object.entries(after)) if (before[name] !== value) element.setAttribute(name,value);
}
function patchNode(live: Node,before: VNode,after: VNode,ctx: PatchContext): Node {
  if (typeof after === 'string' && typeof before === 'string') {
    const wrapper = live as HTMLElement;
    if (after.startsWith(before)) appendText(wrapper,after.slice(before.length),ctx.offset + before.length,ctx);
    else { wrapper.replaceChildren(); appendText(wrapper,after,ctx.offset,ctx); }
    ctx.offset += after.length;
    return live;
  }
  if (typeof after !== 'string' && typeof before !== 'string' && before.tag === after.tag) {
    syncAttrs(live as Element,before.attrs,after.attrs);
    patchChildren(live,Array.from(live.childNodes),before.children,after.children,ctx);
    return live;
  }
  const replacement = build(after,ctx);
  live.parentNode?.replaceChild(replacement,live);
  return replacement;
}
/**
 * Bring `live` (rendered from `before`) in line with `after`, reusing nodes where the structure
 * matches so text that is already visible, and its running animations, stay in place.
 */
export function patchChildren(parent: Node,live: Node[],before: VNode[],after: VNode[],ctx: PatchContext): Node[] {
  const result: Node[] = [];
  after.forEach((node,index)=>{
    if (index < before.length) result.push(patchNode(live[index],before[index],node,ctx));
    else { const fresh = build(node,ctx); parent.appendChild(fresh); result.push(fresh); }
  });
  for (let index = after.length; index < live.length; index++) live[index].parentNode?.removeChild(live[index]);
  return result;
}
