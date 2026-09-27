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

describe('letter reveal',()=>{
 it('reveals new graphemes left to right from opacity zero while keeping text in the DOM',()=>{
   const f=fixture();f.session.append('Hi!');
   expect(f.region.textContent).toBe('Hi!');
   expect(animate).toHaveBeenCalledTimes(3);
   expect(animate.mock.calls[0]).toEqual([[{opacity:0,transform:'translateX(-2px)'},{opacity:1,transform:'translateX(0)'}],{duration:180,delay:0,easing:'ease-out',fill:'backwards'}]);
   expect(animate.mock.calls[1][1].delay).toBe(22);
   expect(animate.mock.calls[2][1].delay).toBe(44);
 });
 it('keeps old nodes stable and does not replay their animations',()=>{
   const f=fixture();f.session.append('a');const first=f.region.firstChild;
   animations[0].finish();f.session.append('bc');
   expect(f.region.firstChild).toBe(first);expect(f.region.textContent).toBe('abc');
   expect(animate).toHaveBeenCalledTimes(3);expect((first as HTMLElement).hasAttribute('data-revealing')).toBe(false);
 });
 it('keeps emoji graphemes intact',()=>{const f=fixture();f.session.append('👩🏽‍🚀a');expect(f.region.childNodes).toHaveLength(2);expect(f.region.textContent).toBe('👩🏽‍🚀a');});
 it('keeps burst text ordered and bounds animation work',()=>{
   const f=fixture();let expected='';for(let i=0;i<1000;i++){expected+='x';f.session.append('x');}
   expect(f.region.textContent).toBe(expected);expect(animate.mock.calls.length).toBeLessThanOrEqual(160);
 });
 it('keeps the final reveal running on completion, then removes its effect',()=>{
   const f=fixture();f.session.append('a');f.session.complete();expect(animations[0].cancelled).toBe(false);
   animations[0].finish();expect(f.region.querySelector('[data-revealing]')).toBeNull();expect(f.region.textContent).toBe('a');
 });
 it.each(['interrupt','fail'] as const)('settles motion and retains text on %s',terminal=>{
   const f=fixture();f.session.append('partial');if(terminal==='fail')f.session.fail('offline');else f.session.interrupt();
   expect(f.region.textContent).toBe('partial');expect(animations.every(a=>a.cancelled)).toBe(true);expect(f.region.querySelector('[data-revealing]')).toBeNull();
 });
 it('clears old effects at restart and ignores stale callbacks',()=>{
   const f=fixture();f.session.append('old');const newer=f.stream.begin();expect(animations.every(a=>a.cancelled)).toBe(true);
   f.view.rerender(<SmoothedStreamingText stream={f.stream} session={newer}/>);newer.requestStarted();newer.append('new');f.session.append('late');
   expect(f.region.textContent).toBe('new');
 });
 it('disables motion for reduced-motion and reacts to a preference change',()=>{
   reduced=true;const f=fixture();f.session.append('a');expect(animate).not.toHaveBeenCalled();
   reduced=false;motionListeners.forEach(fn=>fn(new Event('change')));f.session.append('b');expect(animate).toHaveBeenCalledTimes(1);
   reduced=true;motionListeners.forEach(fn=>fn(new Event('change')));expect(animations[0].cancelled).toBe(true);
 });
 it('shows text when animation APIs are unavailable',()=>{
   const f=fixture();animate.mockImplementation(()=>{throw Error('unsupported');});f.session.append('a');
   Object.defineProperty(Element.prototype,'animate',{configurable:true,value:undefined});f.session.append('b');
   expect(f.region.textContent).toBe('ab');expect(f.region.querySelector('[data-revealing]')).toBeNull();
 });
 it('removes subscriptions and effects on unmount',()=>{
   const f=fixture();f.session.append('a');f.view.unmount();expect(animations[0].cancelled).toBe(true);
   expect(motionListeners.size).toBe(0);f.session.append('b');expect(animate).toHaveBeenCalledTimes(1);
 });
 it('survives StrictMode replay without replacing old text',()=>{
   const stream=createTextStream({batch:false});const session=stream.begin();session.requestStarted();
   const view=render(<StrictMode><SmoothedStreamingText stream={stream} session={session}/></StrictMode>);
   session.append('a');const node=screen.getByRole('region').firstChild;
   view.rerender(<StrictMode><SmoothedStreamingText stream={stream} session={session}/></StrictMode>);
   session.append('b');expect(screen.getByRole('region').firstChild).toBe(node);
 });
 it('renders HTML-shaped input only as text',()=>{
   const f=fixture();f.session.append('<script>');expect(f.region.querySelector('script')).toBeNull();expect(f.region.textContent).toBe('<script>');
 });
});
