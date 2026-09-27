import {flushSync} from 'react-dom';
import type {Root} from 'react-dom/client';
import {createTextStream,SmoothedStreamingText,durations} from '../src/index';
import {readMockStream} from '../demo/protocol';
export interface BrowserCheck {name:string;passed:boolean;detail?:unknown}
export async function runSmoothingChecks(root:Root,fixture:HTMLElement):Promise<BrowserCheck[]> {
 const results:BrowserCheck[]=[];
 const assert=(value:unknown,message:string)=>{if(!value)throw Error(message);};
 const delay=(ms:number)=>new Promise(resolve=>setTimeout(resolve,ms));
 const opaque=()=>[...fixture.querySelectorAll('.stream-readable-append')].every(s=>getComputedStyle(s).opacity==='1'&&s.getAnimations().length===0);
 const mount=()=>{const stream=createTextStream({batch:false});const session=stream.begin();session.requestStarted();flushSync(()=>root.render(<SmoothedStreamingText stream={stream} session={session}/>));return {stream,session,region:fixture.querySelector('.stream-readable-smoothed')!};};
 const check=async(name:string,fn:()=>Promise<unknown>)=>{try{results.push({name,passed:true,detail:await fn()});}catch(e){results.push({name,passed:false,detail:String(e)});}};
 await check('smoothed first fragment is synchronously in DOM, fully opaque and unanimated',async()=>{const f=mount();f.session.append('First unfinished fragment');const first=f.region.firstElementChild!;assert(f.region.textContent==='First unfinished fragment','deferred first DOM');assert(getComputedStyle(first).opacity==='1'&&first.getAnimations().length===0,'animated first text');await delay(60);assert(f.stream.getSnapshot().marks.firstVisibleAt!==null,'missing first-visible estimate');return durations(f.stream.getSnapshot().marks);});
 await check('only the new fragment clarifies over 100 ms while old text stays opaque',async()=>{
  const f=mount();f.session.append('first');const first=f.region.firstElementChild!;f.session.append(' second');const second=f.region.lastElementChild!;
  assert(f.region.textContent==='first second','new text deferred');assert(getComputedStyle(first).opacity==='1'&&first.getAnimations().length===0,'old text changed');
  if(matchMedia('(prefers-reduced-motion: reduce)').matches){assert(second.getAnimations().length===0,'reduced-motion decoration');return {reducedMotion:true};}
  const animation=second.getAnimations()[0];assert(animation,'missing decoration');assert(animation.effect!.getTiming().duration===100,'wrong duration');
  await delay(25);const mid=Number(getComputedStyle(second).opacity);assert(mid>=0.8&&mid<=1,'new fragment unreadable');
  f.session.append(' third');assert(f.region.children[1]===second,'old node replaced');assert(second.getAnimations()[0]===animation,'old animation replayed');assert(getComputedStyle(first).opacity==='1','first dimmed');
  await delay(180);assert(opaque(),'animation did not settle');return {newFragmentMidOpacity:mid,durationMs:100,settledOpacity:getComputedStyle(second).opacity,oldFirstOpacity:getComputedStyle(first).opacity};
 });
 await check('rapid overlapping additions enter DOM in order without leaving translucent text',async()=>{const f=mount();f.session.append('start');let expected='start';for(let i=0;i<100;i++){const part=` ${i}`;expected+=part;f.session.append(part);assert(f.region.textContent===expected,'delayed or reordered DOM');}assert(f.region.children.length===101,'lost spans');await delay(180);assert(opaque(),'rapid appends stayed translucent');return {fragments:101,characters:expected.length,allOpaque:true};});
 await check('smoothed HTTP burst matches every decoded delta synchronously',async()=>{const f=mount();const response=await fetch('/api/mock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scenario:'burst'})});let expected='';let count=0;let maxAppendMs=0;
  await readMockStream(response,event=>{if(event.type==='text'){expected+=event.text;const start=performance.now();f.session.append(event.text);maxAppendMs=Math.max(maxAppendMs,performance.now()-start);assert(f.region.textContent===expected,'DOM differs from received deltas');count++;}else f.session.complete();});
  assert(count===1001&&expected==='You can start reading'+' x'.repeat(1000),'missing burst data');assert(opaque(),'terminal event left active animation');return {fragments:count,characters:expected.length,maxSynchronousAppendMs:maxAppendMs,allOpaque:true};
 });
 await check('stop cancels active clarification and retains partial text immediately',async()=>{const f=mount();f.session.append('a');f.session.append(' partial');f.session.interrupt();assert(f.region.textContent==='a partial'&&opaque(),'stop lost or dimmed text');f.session.append('late');await delay(120);assert(f.region.textContent==='a partial'&&opaque(),'late animation or text after stop');});
 await check('HTTP error settles every fragment and retains partial text',async()=>{
  const f=mount();let expected='';
  try{await readMockStream(await fetch('/api/mock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scenario:'error'})}),event=>{if(event.type==='text'){expected+=event.text;f.session.append(event.text);}});}catch(error){f.session.fail(error);}
  assert(expected.length>0&&f.region.textContent===expected&&opaque(),'HTTP error lost or dimmed text');assert(f.stream.getSnapshot().error==='Synthetic transport failure','error missing');return {characters:expected.length,allOpaque:true};
 });
 await check('restart cancels old animations, ignores stale callbacks and resets the first fragment',async()=>{const f=mount();f.session.append('old');f.session.append(' tail');const old=f.region.lastElementChild!;const next=f.stream.begin();assert(old.getAnimations().length===0,'old animation survived restart');flushSync(()=>root.render(<SmoothedStreamingText stream={f.stream} session={next}/>));next.requestStarted();next.append('new');f.session.append('bad');f.session.complete();assert(f.region.textContent==='new'&&opaque(),'stale or dimmed first text');});
 await check('reduced-motion preference disables and cancels decoration without replay',async()=>{
  // In-browser preference simulation tests the actual MediaQueryList change listener,
  // independently of OS settings. Playwright additionally emulates the real media feature.
  const original=window.matchMedia;let reduced=true;const target=new EventTarget();
  const simulated={get matches(){return reduced;},media:'(prefers-reduced-motion: reduce)',onchange:null,addEventListener:target.addEventListener.bind(target),removeEventListener:target.removeEventListener.bind(target),dispatchEvent:target.dispatchEvent.bind(target)} as unknown as MediaQueryList;
  window.matchMedia=(query:string)=>query==='(prefers-reduced-motion: reduce)'?simulated:original.call(window,query);
  try{const f=mount();f.session.append('a');f.session.append('b');assert(opaque(),'initial reduced motion animated');reduced=false;target.dispatchEvent(new Event('change'));f.session.append('c');reduced=true;target.dispatchEvent(new Event('change'));assert(f.region.textContent==='abc'&&opaque(),'preference change left opacity');reduced=false;target.dispatchEvent(new Event('change'));assert(opaque(),'old animation replayed');return {method:'simulated MediaQueryList + change event',allOpaque:true};}
  finally{flushSync(()=>root.render(null));window.matchMedia=original;}
 });
 return results;
}
