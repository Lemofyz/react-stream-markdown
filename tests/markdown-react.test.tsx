import {cleanup,render,screen} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {createTextStream,StreamingMarkdown} from '../src/index';

class FakeAnimation extends EventTarget {
  cancel(){this.dispatchEvent(new Event('cancel'));}
  finish(){this.dispatchEvent(new Event('finish'));}
}
let animations:FakeAnimation[]=[];
let animate:ReturnType<typeof vi.fn>;
let descriptor:PropertyDescriptor|undefined;
beforeEach(()=>{
 animations=[];
 descriptor=Object.getOwnPropertyDescriptor(Element.prototype,'animate');
 animate=vi.fn(()=>{const value=new FakeAnimation();animations.push(value);return value;});
 Object.defineProperty(Element.prototype,'animate',{configurable:true,value:animate});
 vi.stubGlobal('requestAnimationFrame',()=>1);vi.stubGlobal('cancelAnimationFrame',vi.fn());
 vi.stubGlobal('matchMedia',()=>({matches:false,addEventListener:()=>{},removeEventListener:()=>{}}));
});
afterEach(()=>{cleanup();if(descriptor)Object.defineProperty(Element.prototype,'animate',descriptor);else delete (Element.prototype as {animate?:unknown}).animate;vi.unstubAllGlobals();});

const doc='# Plan\n\nRead **while** it streams, not *after*.\n\n- first `item`\n- [x] second\n\n```js\nconst a = 1;\nconst b = 2;\n```\n\n| a | b |\n|---|--:|\n| 1 | 2 |\n\n> done\n\nSee [docs](https://example.com).';
function setup(animateText=true){
 const stream=createTextStream({batch:false});const session=stream.begin();session.requestStarted();
 const view=render(<StreamingMarkdown stream={stream} session={session} animate={animateText}/>);
 return {stream,session,view,region:screen.getByRole('region')};
}
/** Structure without the text wrapper spans used for reveals. */
const shape=(root:Element):string=>Array.from(root.childNodes).map(node=>node.nodeType===3?node.textContent:(node as Element).tagName==='SPAN'&&!(node as Element).className?shape(node as Element):(node as Element).tagName==='SPAN'&&(node as Element).className==='stream-readable-append'?node.textContent:`<${(node as Element).tagName.toLowerCase()}>${shape(node as Element)}</>`).join('');

describe('StreamingMarkdown',()=>{
 it.each([1,3,7,1000])('streams in %i-character chunks to the same result as one-shot rendering',size=>{
   const once=setup(false);once.session.append(doc);once.session.complete();
   const expected=shape(once.region);cleanup();
   const f=setup();
   for(let i=0;i<doc.length;i+=size)f.session.append(doc.slice(i,i+size));
   f.session.complete();
   expect(shape(f.region)).toBe(expected);
   expect(f.region.querySelector('h1')?.textContent).toBe('Plan');
   expect(f.region.querySelector('strong')?.textContent).toBe('while');
   expect(f.region.querySelector('pre code')?.textContent).toBe('const a = 1;\nconst b = 2;');
   expect(f.region.querySelectorAll('td[data-align=right]')).toHaveLength(1);
   expect(f.region.querySelector('a')?.getAttribute('href')).toBe('https://example.com');
 });
 it('never shows raw markers for unfinished bold and keeps finished blocks untouched',()=>{
   const f=setup();
   f.session.append('# Title\n\nSome **bo');
   expect(f.region.textContent).toBe('TitleSome bo');
   expect(f.region.querySelector('strong')?.textContent).toBe('bo');
   const heading=f.region.querySelector('h1');
   f.session.append('ld** text\n\nNext');
   expect(f.region.querySelector('h1')).toBe(heading);
   expect(f.region.textContent).toBe('TitleSome bold textNext');
 });
 it('animates each character exactly once, even when the structure changes',()=>{
   const f=setup();
   const chunks=['Some **bo','ld** te','xt\n\n- a','\n- b'];
   let seen='';
   for(const chunk of chunks){
     const before=animate.mock.calls.length;
     f.session.append(chunk);
     const now=f.region.textContent!;
     expect(animate.mock.calls.length-before).toBe(now.length-seen.length);
     animations.forEach(a=>a.finish());
     seen=now;
   }
   expect(f.region.querySelectorAll('.stream-readable-append')).toHaveLength(0);
 });
 it('renders HTML and unsafe links as inert text',()=>{
   const f=setup();
   f.session.append('<img src=x onerror="alert(1)"> [x](javascript:alert(1)) <script>bad()</script>');f.session.complete();
   expect(f.region.querySelector('img,script,a')).toBeNull();
   expect(f.region.textContent).toContain('<img src=x onerror="alert(1)">');
 });
 it('shows everything at once on stop and keeps the partial reply',()=>{
   const f=setup();f.session.append('Partial **answ');f.session.interrupt();
   expect(f.region.querySelector('[data-revealing]')).toBeNull();
   expect(f.region.textContent).toBe('Partial **answ');
 });
 it('clears the old reply on restart and ignores the stale session',()=>{
   const f=setup();f.session.append('old');const newer=f.stream.begin();
   f.view.rerender(<StreamingMarkdown stream={f.stream} session={newer}/>);
   newer.requestStarted();newer.append('new');f.session.append('late');
   expect(f.region.textContent).toBe('new');
 });
 it('creates no per-character spans when animation is off',()=>{
   const f=setup(false);f.session.append('Hello **world**');
   expect(animate).not.toHaveBeenCalled();expect(f.region.querySelector('.stream-readable-append')).toBeNull();
 });
 it('bounds animation work in a burst',()=>{
   const f=setup();for(let i=0;i<1000;i++)f.session.append('x ');
   expect(animate.mock.calls.length).toBeLessThanOrEqual(160);expect(f.region.textContent).toBe('x '.repeat(1000).trim());
 });
});

describe('StreamingMarkdown remounting',()=>{
 it('keeps visible text when the session prop arrives after streaming began',()=>{
   const stream=createTextStream({batch:false});const session=stream.begin();session.requestStarted();
   const view=render(<StreamingMarkdown stream={stream} session={null}/>);
   session.append('Hello **wor');const strong=screen.getByRole('region').querySelector('strong');
   const calls=animate.mock.calls.length;
   view.rerender(<StreamingMarkdown stream={stream} session={session}/>);
   session.append('ld**');
   expect(screen.getByRole('region').querySelector('strong')).toBe(strong);
   expect(animate.mock.calls.length-calls).toBe(2);
 });
});
