/**
 * Small, dependency-free Markdown parser for streamed model output.
 *
 * It covers what chat replies actually use (CommonMark basics plus GFM tables, task lists and
 * strikethrough) and is tolerant of half-received input: when `open` is true the text may still
 * grow, so unclosed `**bold`, `` `code `` and code fences render optimistically instead of
 * flashing raw markers. It never produces HTML strings; renderers build DOM nodes directly.
 */
export type Inline =
  | {type:'text';value:string}
  | {type:'code';value:string}
  | {type:'strong'|'em'|'del';children:Inline[]}
  | {type:'link';href:string|null;children:Inline[]}
  | {type:'br'};
export type Align = 'left'|'center'|'right'|null;
export interface ListItem {checked:boolean|null;children:Block[]}
export type Block =
  | {type:'paragraph';children:Inline[]}
  | {type:'heading';level:number;children:Inline[]}
  | {type:'code';lang:string;value:string}
  | {type:'hr'}
  | {type:'blockquote';children:Block[]}
  | {type:'list';ordered:boolean;start:number;tight:boolean;items:ListItem[]}
  | {type:'table';align:Align[];head:Inline[][];rows:Inline[][][]};
/** A top-level block and its source range, so a renderer can freeze finished blocks. */
export interface SourceBlock {block:Block;start:number;end:number}

interface Line {text:string;start:number;end:number}
const splitLines = (src: string): Line[] => {
  const lines: Line[] = [];
  let start = 0;
  while (start <= src.length) {
    const nl = src.indexOf('\n',start);
    const stop = nl < 0 ? src.length : nl;
    lines.push({text:src.slice(start,stop).replace(/\r$/,''),start,end:nl < 0 ? stop : nl + 1});
    if (nl < 0) break;
    start = nl + 1;
  }
  return lines;
};
const blank = (line: string) => !/\S/.test(line);
const fenceOpen = /^( {0,3})(`{3,}|~{3,})[ \t]*([^\s`]*)[^`]*$/;
const headingLine = /^ {0,3}(#{1,6})(?:[ \t]+(.*))?$/;
const hrLine = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const quoteLine = /^ {0,3}> ?/;
const itemLine = /^( {0,3})([-+*]|\d{1,9}[.)])(?:([ \t]+)(.*))?$/;
const tableSeparator = /^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$/;

function listMarker(line: string) {
  const m = itemLine.exec(line);
  if (!m) return null;
  const marker = m[2];
  const ordered = /\d/.test(marker[0]);
  const spacing = m[3] ?? '';
  const content = m[4] ?? '';
  const indent = m[1].length + marker.length + (spacing.length > 4 || !content ? 1 : spacing.length);
  return {ordered,start:ordered ? parseInt(marker,10) : 1,kind:ordered ? marker.at(-1)! : marker,indent,content,empty:!content.trim()};
}
/** Lines that end a paragraph. Lists may interrupt only when non-empty (ordered: starting at 1). */
function interrupts(line: string) {
  if (fenceOpen.test(line) || headingLine.test(line) || hrLine.test(line) || quoteLine.test(line)) return true;
  const item = listMarker(line);
  return !!item && !item.empty && (!item.ordered || item.start === 1);
}
function splitRow(line: string): string[] {
  let row = line.trim();
  if (row.startsWith('|')) row = row.slice(1);
  if (row.endsWith('|') && !row.endsWith('\\|')) row = row.slice(0,-1);
  const cells: string[] = [];
  let cell = '';
  for (let i = 0; i < row.length; i++) {
    if (row[i] === '\\' && row[i+1] === '|') { cell += '|'; i++; }
    else if (row[i] === '|') { cells.push(cell.trim()); cell = ''; }
    else cell += row[i];
  }
  cells.push(cell.trim());
  return cells;
}

export function parseBlocks(src: string,open = false): SourceBlock[] {
  const lines = splitLines(src);
  const out: SourceBlock[] = [];
  let i = 0;
  // A block is "open" when it may still grow: the last block of a still-streaming document.
  const lastContent = (() => { let k = lines.length - 1; while (k >= 0 && blank(lines[k].text)) k--; return k; })();
  while (i < lines.length) {
    const line = lines[i].text;
    if (blank(line)) { i++; continue; }
    const first = i;
    let block: Block;
    const fence = fenceOpen.exec(line);
    const heading = headingLine.exec(line);
    const item = listMarker(line);
    if (fence) {
      const indent = fence[1].length;
      const marker = fence[2];
      const close = new RegExp(`^ {0,3}${marker[0] === '`' ? '`' : '~'}{${marker.length},}[ \\t]*$`);
      const body: string[] = [];
      let closed = false;
      for (i++; i < lines.length; i++) {
        if (close.test(lines[i].text)) { closed = true; i++; break; }
        body.push(lines[i].text.replace(new RegExp(`^ {0,${indent}}`),''));
      }
      if (!closed && open) {
        // Hide a closing fence that is still arriving, and the empty line after a trailing newline.
        const tail = body.at(-1);
        if (tail !== undefined && (tail === '' || new RegExp(`^ {0,3}\\${marker[0]}{1,${marker.length}}$`).test(tail))) body.pop();
      }
      block = {type:'code',lang:/^[\w+#.-]*$/.test(fence[3]) ? fence[3] : '',value:body.join('\n')};
    } else if (heading) {
      const text = (heading[2] ?? '').replace(/(?:^|[ \t]+)#+[ \t]*$/,'').trim();
      block = {type:'heading',level:heading[1].length,children:parseInline(text,open && i === lastContent)};
      i++;
    } else if (hrLine.test(line)) {
      block = {type:'hr'};
      i++;
    } else if (quoteLine.test(line)) {
      const body: string[] = [];
      while (i < lines.length && quoteLine.test(lines[i].text)) body.push(lines[i++].text.replace(quoteLine,''));
      block = {type:'blockquote',children:parseBlocks(body.join('\n'),open && i > lastContent).map(b=>b.block)};
    } else if (item) {
      const items: {checked:boolean|null;lines:string[]}[] = [];
      let tight = true;
      let current = item;
      let body = [current.content];
      let lastLine = i;
      const push = () => {
        let checked: boolean|null = null;
        const task = /^\[([ xX])\](?:[ \t]+|$)/.exec(body[0]);
        if (task) { checked = task[1] !== ' '; body[0] = body[0].slice(task[0].length); }
        items.push({checked,lines:body});
      };
      for (i++; i < lines.length; i++) {
        const text = lines[i].text;
        if (blank(text)) { body.push(''); continue; }
        const indent = text.length - text.trimStart().length;
        const sibling = listMarker(text);
        const sawBlank = body.at(-1) === '';
        if (indent >= current.indent) {
          if (sawBlank) tight = false;
          body.push(text.slice(current.indent));
        } else if (hrLine.test(text)) {
          break;
        } else if (sibling && sibling.ordered === current.ordered && sibling.kind === current.kind) {
          if (sawBlank) tight = false;
          while (body.at(-1) === '') body.pop();
          push();
          current = sibling;
          body = [sibling.content];
        } else if (!sawBlank && !interrupts(text) && !sibling) {
          body.push(text.trim()); // Lazy paragraph continuation.
        } else break;
        lastLine = i;
      }
      while (body.at(-1) === '') body.pop();
      push();
      i = lastLine + 1;
      const isOpen = open && lastLine >= lastContent;
      block = {type:'list',ordered:item.ordered,start:item.start,tight,items:items.map((entry,index)=>({
        checked:entry.checked,
        children:parseBlocks(entry.lines.join('\n'),isOpen && index === items.length - 1).map(b=>b.block),
      }))};
    } else if (line.includes('|') && i + 1 < lines.length && tableSeparator.test(lines[i+1].text)
      && (splitRow(lines[i+1].text).length === splitRow(line).length || (open && i + 1 === lines.length - 1))) {
      const head = splitRow(line);
      const align: Align[] = head.map((_,c)=>{
        const spec = (splitRow(lines[i+1].text)[c] ?? '').trim();
        return spec.startsWith(':') && spec.endsWith(':') ? 'center' : spec.endsWith(':') ? 'right' : spec.startsWith(':') ? 'left' : null;
      });
      const rows: Inline[][][] = [];
      for (i += 2; i < lines.length && !blank(lines[i].text) && lines[i].text.includes('|') && !interrupts(lines[i].text); i++) {
        const cells = splitRow(lines[i].text);
        const rowOpen = open && i === lastContent;
        rows.push(head.map((_,c)=>parseInline(cells[c] ?? '',rowOpen && c === cells.length - 1)));
      }
      block = {type:'table',align,head:head.map(cell=>parseInline(cell,false)),rows};
    } else if (open && line.trimStart().startsWith('|') && (i === lastContent || (i + 1 === lastContent && /^[ \t]*\|?[ \t:|-]*$/.test(lines[i+1].text)))) {
      // A table header whose separator has not arrived yet: show the header, not raw pipes.
      const head = splitRow(line);
      block = {type:'table',align:head.map(()=>null),head:head.map(cell=>parseInline(cell,i === lastContent)),rows:[]};
      i = lastContent + 1;
    } else {
      const body = [line.trim()];
      for (i++; i < lines.length && !blank(lines[i].text) && !interrupts(lines[i].text); i++) body.push(lines[i].text.trim());
      block = {type:'paragraph',children:parseInline(body.join('\n'),open && i > lastContent)};
    }
    out.push({block,start:lines[first].start,end:lines[Math.max(first,i - 1)].end});
  }
  return out;
}

const punctuation = /[!-/:-@[-`{-~]/;
const word = /[\p{L}\p{N}]/u;
const bareUrl = /^https?:\/\/[^\s<>]*[^\s<>.,:;"')\]!?*_~]/;

/** Only web and mail links. Anything else (javascript:, data:, …) renders as plain text. */
export function safeHref(raw: string): string|null {
  try {
    const url = new URL(raw,'https://stream-readable.invalid/');
    return ['http:','https:','mailto:'].includes(url.protocol) ? raw : null;
  } catch { return null; }
}
const runLength = (s: string,i: number) => { let j = i; while (s[j] === s[i]) j++; return j - i; };
/** Find a closing run of `char` that can end a span of `size` markers. Returns the closer's start. */
function findCloser(s: string,from: number,char: string,size: number): number {
  for (let j = from; j < s.length; j++) {
    if (s[j] === '\\') { j++; continue; }
    if (s[j] === '`') { const n = runLength(s,j); const end = s.indexOf('`'.repeat(n),j + n); if (end > 0) j = end + n - 1; continue; }
    if (s[j] !== char) continue;
    const n = runLength(s,j);
    const before = s[j-1];
    const after = s[j+n];
    const rightFlanking = before !== undefined && /\S/.test(before) && j > from;
    const wordAfter = char === '_' && after !== undefined && word.test(after);
    if (rightFlanking && !wordAfter && (n === size || n === 3)) return j + n - size;
    j += n - 1;
  }
  return -1;
}

export function parseInline(s: string,open = false,depth = 0): Inline[] {
  // Pathological input (thousands of unclosed markers) stays linear instead of nesting deeply.
  if (depth > 24) return s ? [{type:'text',value:s}] : [];
  const out: Inline[] = [];
  let text = '';
  const flush = () => { if (text) { out.push({type:'text',value:text}); text = ''; } };
  let i = 0;
  while (i < s.length) {
    const c = s[i];
    if (c === '\\' && s[i+1] === '\n') { flush(); out.push({type:'br'}); i += 2; continue; }
    if (c === '\\' && i + 1 < s.length && punctuation.test(s[i+1])) { text += s[i+1]; i += 2; continue; }
    if (c === '\n') { text = text.replace(/[ \t]+$/,''); flush(); out.push({type:'br'}); i++; continue; }
    if (c === '`') {
      const n = runLength(s,i);
      let close = s.indexOf('`'.repeat(n),i + n);
      while (close >= 0 && runLength(s,close) !== n) close = s.indexOf('`'.repeat(n),close + runLength(s,close));
      if (close >= 0 || (open && i + n < s.length)) {
        let value = s.slice(i + n,close >= 0 ? close : s.length).replace(/\n/g,' ');
        if (close >= 0 && value.length > 2 && value.startsWith(' ') && value.endsWith(' ') && value.trim()) value = value.slice(1,-1);
        flush(); out.push({type:'code',value}); i = close >= 0 ? close + n : s.length; continue;
      }
      if (open && i + n === s.length) break; // A marker still arriving.
      text += s.slice(i,i + n); i += n; continue;
    }
    if (c === '[' || (c === '!' && s[i+1] === '[')) {
      const image = c === '!';
      const labelStart = i + (image ? 2 : 1);
      let depth = 1;
      let j = labelStart;
      for (; j < s.length && depth > 0; j++) {
        if (s[j] === '\\') j++;
        else if (s[j] === '[') depth++;
        else if (s[j] === ']') depth--;
      }
      const labelEnd = j - 1;
      if (depth === 0 && s[j] === '(') {
        let parens = 1;
        let k = j + 1;
        for (; k < s.length && parens > 0; k++) {
          if (s[k] === '\\') k++;
          else if (s[k] === '(') parens++;
          else if (s[k] === ')') parens--;
        }
        const label = s.slice(labelStart,labelEnd);
        if (parens === 0) {
          const target = /^\s*<?([^\s>]*)>?(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?\s*$/.exec(s.slice(j + 1,k - 1));
          if (target) {
            const href = safeHref(target[1]);
            // Images are shown as links: auto-loading model-chosen URLs can leak data.
            const children = image ? [{type:'text',value:label || target[1]} as Inline] : parseInline(label,false,depth + 1);
            flush(); out.push({type:'link',href,children}); i = k; continue;
          }
        } else if (open) {
          flush(); out.push({type:'link',href:null,children:image ? [{type:'text',value:label}] : parseInline(label,false,depth + 1)}); i = s.length; continue;
        }
      }
      text += c; i++; continue;
    }
    if (c === '<') {
      const auto = /^<((?:https?:\/\/|mailto:)[^\s<>]+)>/.exec(s.slice(i));
      if (auto) { flush(); out.push({type:'link',href:safeHref(auto[1]),children:[{type:'text',value:auto[1].replace(/^mailto:/,'')}]}); i += auto[0].length; continue; }
    }
    if (c === 'h' && s.startsWith('http',i) && (i === 0 || !word.test(s[i-1]))) {
      const url = bareUrl.exec(s.slice(i,i + 2048));
      if (url) { flush(); out.push({type:'link',href:safeHref(url[0]),children:[{type:'text',value:url[0]}]}); i += url[0].length; continue; }
    }
    if (c === '*' || c === '_' || c === '~') {
      const n = runLength(s,i);
      if (open && i + n === s.length) break; // A marker still arriving.
      const size = c === '~' ? 2 : n >= 2 ? 2 : 1;
      const after = s[i+n];
      const leftFlanking = after !== undefined && /\S/.test(after) && !(c === '_' && i > 0 && word.test(s[i-1]));
      if ((c !== '~' || n === 2) && leftFlanking) {
        const type = c === '~' ? 'del' : size === 2 ? 'strong' : 'em';
        const close = findCloser(s,i + size,c,size);
        if (close >= 0) {
          flush(); out.push({type,children:parseInline(s.slice(i + size,close),false,depth + 1)}); i = close + size; continue;
        }
        if (open) {
          flush(); out.push({type,children:parseInline(s.slice(i + size),true,depth + 1)}); i = s.length; continue;
        }
      }
      text += s.slice(i,i + n); i += n; continue;
    }
    text += c; i++;
  }
  flush();
  return out;
}

/** Parse a whole document. Pass `open: true` while the text is still streaming. */
export function parseMarkdown(src: string,options: {open?: boolean} = {}): Block[] {
  return parseBlocks(src,options.open ?? false).map(b=>b.block);
}
