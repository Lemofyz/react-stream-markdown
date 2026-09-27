import {useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {createTextStream,durations,StreamingText,useStreamingText,type StreamSession,type TextStream} from '../src/index';
import {readMockStream,sentenceExperiment} from './protocol';
import '../src/style.css';
import './theme.css';

function Panel({title,description,stream,session,id}: {title:string;description:string;stream:TextStream;session:StreamSession|null;id:string}) {
  const state=useStreamingText(stream);
  const values=durations(state.marks);
  const format=(value:number|null)=>value===null?'—':`${value.toFixed(1)} ms`;
  return <section className="panel" data-testid={id}>
    <div className="panel-head"><h2>{title}</h2><span className="status" role="status">{state.status}</span></div>
    <p className="description">{description}</p>
    <div className="reply-box"><StreamingText stream={stream} session={session} label={`${title} reply`}/>{!state.text&&<span className="placeholder">{state.status==='streaming'?'Waiting for readable text…':'Run a synthetic stream to begin.'}</span>}</div>
    {state.error&&<p className="error" role="alert">{state.error}</p>}
    <dl>
      <div><dt>Click → request start</dt><dd>{format(values.clickToRequestMs)}</dd></div>
      <div><dt>Request start → first text</dt><dd>{format(values.timeToFirstTextMs)}</dd></div>
      <div><dt>First text → DOM commit</dt><dd>{format(values.firstTextToCommitMs)}</dd></div>
      <div><dt>First text → visible estimate</dt><dd>{format(values.firstTextToVisibleEstimateMs)}</dd></div>
      <div><dt>First text → terminal event</dt><dd>{format(values.firstTextToEndMs)}</dd></div>
      <div><dt>Fragments / text publications</dt><dd>{state.fragments} / {state.publications}</dd></div>
    </dl>
    <details><summary>Machine-readable measurement</summary><pre data-testid={`${id}-json`}>{JSON.stringify({...state,durations:values},null,2)}</pre></details>
  </section>;
}
function App() {
  const [streams]=useState(()=>[createTextStream(),createTextStream({batch:false}),createTextStream()]);
  const [sessions,setSessions]=useState<(StreamSession|null)[]>([null,null,null]);
  const [scenario,setScenario]=useState('normal');
  const [preflight,setPreflight]=useState(80);
  const [running,setRunning]=useState(false);
  const active=useRef<{abort:AbortController;sessions:StreamSession[];id:number;flush:()=>void}|null>(null);
  const sequence=useRef(0);
  useEffect(()=>()=>{active.current?.abort.abort();active.current?.sessions.forEach(s=>s.interrupt());},[]);
  const stop=()=>{active.current?.abort.abort();active.current?.flush();active.current?.sessions.forEach(s=>s.interrupt());active.current=null;setRunning(false);};
  const run=async()=>{
    stop();
    const clickedAt=performance.now();
    const newSessions=streams.map(s=>s.begin(clickedAt));
    const abort=new AbortController();
    const id=++sequence.current;
    const sentence=sentenceExperiment(text=>newSessions[2].append(text));
    active.current={abort,sessions:newSessions,id,flush:()=>sentence.flush()};setSessions(newSessions);setRunning(true);
    try {
      // Deliberate client delay demonstrates metric #1. Never required by the library.
      await new Promise<void>((resolve,reject)=>{
        const onAbort=()=>{clearTimeout(timer);reject(new DOMException('Stopped','AbortError'));};
        const timer=setTimeout(()=>{abort.signal.removeEventListener('abort',onAbort);resolve();},preflight);
        abort.signal.addEventListener('abort',onAbort,{once:true});
      });
      if(abort.signal.aborted)return;
      newSessions.forEach(s=>s.requestStarted());
      const response=await fetch('/api/mock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scenario}),signal:abort.signal});
      await readMockStream(response,event=>{
        if(abort.signal.aborted||active.current?.id!==id)return;
        if(event.type==='text'){
          const receivedAt=performance.now();
          if(event.text.trim())newSessions.forEach(s=>s.textReceived(receivedAt));
          newSessions[0].append(event.text);newSessions[1].append(event.text);sentence.push(event.text);
        } else if(event.type==='done') {sentence.flush();newSessions.forEach(s=>s.complete());}
      });
    }catch(error){
      if(!abort.signal.aborted){sentence.flush();newSessions.forEach(s=>s.fail(error));}
    }finally{
      // ResourceTiming arrives when the body is consumed. It refines dispatch to network-stack requestStart.
      setTimeout(()=>{
        const entries=performance.getEntriesByName(new URL('/api/mock',location.href).href) as PerformanceResourceTiming[];
        const entry=entries.filter(e=>e.startTime>=clickedAt).at(-1);
        if(entry?.requestStart)newSessions.forEach(s=>s.networkRequestStarted(entry.requestStart));
      },0);
      if(active.current?.id===id){active.current=null;setRunning(false);}
    }
  };
  return <main>
    <header><div className="eyebrow">STREAM READABLE / LATENCY LAB</div><h1>Read sooner.<br/><span>Measure the wait.</span></h1><p>One synthetic stream, three display policies. Separate request preparation, first text, rendering, and completion.</p></header>
    <form className="controls" onSubmit={e=>{e.preventDefault();void run();}}>
      <label>Stream scenario<select value={scenario} onChange={e=>setScenario(e.target.value)}><option value="normal">Normal fragments</option><option value="burst">1,000-fragment burst</option><option value="no-punctuation">No sentence boundary</option><option value="error">Transport error</option></select></label>
      <label>Client preparation (ms)<input type="number" min="0" max="3000" value={preflight} onChange={e=>setPreflight(Math.max(0,Math.min(3000,Number(e.target.value)||0)))}/></label>
      <button type="submit">{running?'Restart stream':'Run stream'}</button><button className="secondary" type="button" onClick={stop} disabled={!running}>Stop</button>
    </form>
    <div className="panels">
      <Panel title="Immediate + frame batching" description="First readable fragment is published immediately. Later deltas share a frame." stream={streams[0]} session={sessions[0]} id="frame"/>
      <Panel title="Every event" description="Immediate per-event baseline. Already fast; can publish many redundant snapshots." stream={streams[1]} session={sessions[1]} id="event"/>
      <Panel title="Sentence buffer experiment" description="Comparison only. Holds text until punctuation or the terminal event." stream={streams[2]} session={sessions[2]} id="sentence"/>
    </div>
    <footer><p><strong>Visible time is an estimate.</strong> Request start uses same-origin ResourceTiming when available, otherwise fetch dispatch. Visible time uses two animation frames after commit, with first-character viewport, clipping, and opacity checks. It does not measure physical display pixels or human comprehension.</p><p>No content animation, model calls, external assets, or credentials. Frame batching reduces text publications; it does not reduce model time to first text. Readability preference requires a user study.</p></footer>
  </main>;
}
createRoot(document.getElementById('root')!).render(<App/>);
