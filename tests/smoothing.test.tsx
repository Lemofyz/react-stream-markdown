import {StrictMode} from 'react';
import {cleanup,render,screen} from '@testing-library/react';
import {afterEach,beforeEach,describe,it,expect,vi} from 'vitest';
import {createTextStream,SmoothedStreamingText} from '../src/index';

class FakeAnimation extends EventTarget {
  cancelled=false;
  cancel(){this.cancelled=true;this.dispatchEvent(new Event('cancel'));}
  finish(){this.dispatchEvent(new Event('finish'));}
}
let animations:FakeAnimation[]=[];
let reduced=false;
let motionListeners=new Set<EventListener>();
let descriptor:PropertyDescriptor|undefined;
let animate:ReturnType<typeof vi.fn>;
beforeEach(()=>{
 animations=[];reduced=false;motionListeners=new Set();
 descriptor=Object.getOwnPropertyDescriptor(Element.prototype,'animate');
 animate=vi.fn(()=>{const value=new FakeAnimation();animations.push(value);return value;});
 Object.defineProperty(Element.prototype,'animate',{configurable:true,value:animate});
 vi.stubGlobal('requestAnimationFrame',()=>1);vi.stubGlobal('cancelAnimationFrame',vi.fn());
 vi.stubGlobal('matchMedia',()=>({get matches(){return reduced;},addEventListener:(_type:string,listener:EventListener)=>motionListeners.add(listener),removeEventListener:(_type:string,listener:EventListener)=>motionListeners.delete(listener)}));
});
afterEach(()=>{cleanup();if(descriptor)Object.defineProperty(Element.prototype,'animate',descriptor);else delete (Element.prototype as {animate?:unknown}).animate;vi.unstubAllGlobals();});
function fixture(){const stream=createTextStream({batch:false});const session=stream.begin();session.requestStarted();const view=render(<SmoothedStreamingText stream={stream} session={session}/>);return {stream,session,view,region:screen.getByRole('region')};}

describe('append-only smoothing',()=>{
 it('places the first incomplete fragment in DOM before append returns, with no animation',()=>{const f=fixture();f.session.append('Read this');expect(f.region.textContent).toBe('Read this');expect(animate).not.toHaveBeenCalled();expect(f.stream.getSnapshot().marks.firstCommitAt).not.toBeNull();});
 it('immediately appends only the new fragment with 100 ms, .8-to-1 decoration',()=>{const f=fixture();f.session.append('first');const original=f.region.firstChild;f.session.append(' second');expect(f.region.textContent).toBe('first second');expect(f.region.firstChild).toBe(original);expect(animate).toHaveBeenCalledTimes(1);expect(animate.mock.calls[0]).toEqual([[{opacity:0.8},{opacity:1}],{duration:100,easing:'ease-out',fill:'none'}]);expect((f.region.lastChild as HTMLElement).style.opacity).toBe('');});
 it('preserves every rapid append synchronously and in order without reanimating old spans',()=>{const f=fixture();f.session.append('start');const first=f.region.firstChild;let expected='start';for(let i=0;i<1000;i++){const part=` ${i}`;expected+=part;f.session.append(part);expect(f.region.textContent).toBe(expected);}expect(f.region.firstChild).toBe(first);expect(animate).toHaveBeenCalledTimes(1000);f.session.complete();expect(animations.every(a=>a.cancelled)).toBe(true);expect(f.region.querySelector('[data-clarifying]')).toBeNull();});
 it('does not treat leading whitespace as the first readable fragment',()=>{const f=fixture();f.session.append(' \n');f.session.append('你好');expect(animate).not.toHaveBeenCalled();f.session.append('，世界');expect(animate).toHaveBeenCalledTimes(1);expect(f.region.textContent).toBe(' \n你好，世界');});
 it('keeps a finished old span stable when a new delta arrives',()=>{const f=fixture();f.session.append('a');f.session.append('b');const second=f.region.lastChild;animations[0].finish();f.session.append('c');expect(f.region.childNodes[1]).toBe(second);expect(animate).toHaveBeenCalledTimes(2);expect((second as HTMLElement).hasAttribute('data-clarifying')).toBe(false);});
 it.each(['complete','interrupt','fail'] as const)('settles all decoration and retains text on %s',terminal=>{const f=fixture();f.session.append('first');f.session.append(' partial');if(terminal==='fail')f.session.fail('offline');else f.session[terminal]();expect(f.region.textContent).toBe('first partial');expect(animations[0].cancelled).toBe(true);expect(f.region.querySelector('[data-clarifying]')).toBeNull();f.session.append('late');expect(f.region.textContent).toBe('first partial');});
 it('clears old animations at restart and ignores stale callbacks',()=>{const f=fixture();f.session.append('old');f.session.append(' tail');const newer=f.stream.begin();expect(animations[0].cancelled).toBe(true);f.view.rerender(<SmoothedStreamingText stream={f.stream} session={newer}/>);newer.requestStarted();newer.append('new');f.session.append('bad');f.session.fail('stale');expect(f.region.textContent).toBe('new');expect(animate).toHaveBeenCalledTimes(1);expect(f.region.querySelector('[data-clarifying]')).toBeNull();});
 it('skips decoration with reduced motion',()=>{reduced=true;const f=fixture();f.session.append('a');f.session.append('b');expect(f.region.textContent).toBe('ab');expect(animate).not.toHaveBeenCalled();});
 it('cancels active decoration when reduced motion changes without replaying it',()=>{const f=fixture();f.session.append('a');f.session.append('b');reduced=true;motionListeners.forEach(fn=>fn(new Event('change')));expect(animations[0].cancelled).toBe(true);f.session.append('c');expect(animate).toHaveBeenCalledTimes(1);reduced=false;motionListeners.forEach(fn=>fn(new Event('change')));expect(animate).toHaveBeenCalledTimes(1);f.session.append('d');expect(animate).toHaveBeenCalledTimes(2);expect(f.region.textContent).toBe('abcd');});
 it('falls back to fully readable text when WAAPI is unavailable or throws',()=>{const f=fixture();f.session.append('a');animate.mockImplementation(()=>{throw Error('unsupported');});f.session.append('b');Object.defineProperty(Element.prototype,'animate',{configurable:true,value:undefined});f.session.append('c');expect(f.region.textContent).toBe('abc');expect(f.region.querySelector('[data-clarifying]')).toBeNull();});
 it('settles animations and removes subscriptions on unmount',()=>{const f=fixture();f.session.append('a');f.session.append('b');f.view.unmount();expect(animations[0].cancelled).toBe(true);expect(motionListeners.size).toBe(0);f.session.append('c');expect(animate).toHaveBeenCalledTimes(1);});
 it('survives StrictMode replay without animating or replacing old text',()=>{const stream=createTextStream({batch:false});const session=stream.begin();session.requestStarted();const view=render(<StrictMode><SmoothedStreamingText stream={stream} session={session}/></StrictMode>);session.append('first');const node=screen.getByRole('region').firstChild;view.rerender(<StrictMode><SmoothedStreamingText stream={stream} session={session}/></StrictMode>);session.append(' next');expect(screen.getByRole('region').firstChild).toBe(node);expect(animate).toHaveBeenCalledTimes(1);});
 it('escapes HTML-shaped text and never creates markup from a delta',()=>{const f=fixture();f.session.append('<script>');f.session.append('<img src=x onerror=alert(1)>');expect(f.region.querySelector('script,img')).toBeNull();expect(f.region.textContent).toBe('<script><img src=x onerror=alert(1)>');});
});

it('replacing the controller starts a fresh fully visible fragment even with a shared prefix',()=>{const f=fixture();f.session.append('same');const newer=createTextStream({batch:false});const next=newer.begin();next.requestStarted();next.append('same plus new');f.view.rerender(<SmoothedStreamingText stream={newer} session={next}/>);expect(f.region.textContent).toBe('same plus new');expect(animate).not.toHaveBeenCalled();});
