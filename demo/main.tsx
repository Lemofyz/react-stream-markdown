import {useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {createTextStream,durations,StreamingText,SmoothedStreamingText,useStreamingText,type StreamSession,type TextStream} from '../src/index';
import {readMockStream,sentenceExperiment} from './protocol';
import '../src/style.css';
import './theme.css';

function Panel({title,description,stream,session,id,smoothed=false}: {title:string;description:string;stream:TextStream;session:StreamSession|null;id:string;smoothed?:boolean}) {
  const state=useStreamingText(stream);
  const values=durations(state.marks);
  const format=(value:number|null)=>value===null?'—':`${value.toFixed(1)} ms`;
  return <section className="panel" data-testid={id}>
    <div className="panel-head"><h2>{title}</h2><span className="status" role="status">{state.status}</span></div>
    <p className="description">{description}</p>
    <div className="reply-box"><>{smoothed ? <SmoothedStreamingText stream={stream} session={session} label={`${title} reply`}/> : <StreamingText stream={stream} session={session} label={`${title} reply`}/>}</>{!state.text&&<span className="placeholder">{state.status==='streaming'?'Waiting for readable text…':'Run a synthetic stream to begin.'}</span>}</div>
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
  const [streams]=useState(()=>[createTextStream(),createTextStream({batch:false}),createTextStream({batch:false}),createTextStream()]);
  const [sessions,setSessions]=useState<(StreamSession|null)[]>([null,null,null,null]);
  const [scenario,setScenario]=useState(()=>{const value=new URLSearchParams(location.search).get('scenario');return ['normal','burst','no-punctuation','error'].includes(value??'')?value!:'normal';});
  const [playback,setPlayback]=useState(()=>new URLSearchParams(location.search).get('playback')==='slow'?'slow':'normal');
  const [preflight,setPreflight]=useState(80);
  const [running,setRunning]=useState(false);
  const arrivals=useRef<{fragment:number;receivedAt:number;characters:number}[]>([]);
  const autoplayed=useRef(false);
  const active=useRef<{abort:AbortController;sessions:StreamSession[];id:number;flush:()=>void}|null>(null);
  const sequence=useRef(0);
  useEffect(()=>()=>{active.current?.abort.abort();active.current?.sessions.forEach(s=>s.interrupt());},[]);
  const stop=()=>{active.current?.abort.abort();active.current?.flush();active.current?.sessions.forEach(s=>s.interrupt());active.current=null;setRunning(false);};
  const run=async()=>{
    stop();
    const clickedAt=performance.now();
    arrivals.current=[];
    const newSessions=streams.map(s=>s.begin(clickedAt));
    const abort=new AbortController();
    const id=++sequence.current;
    const sentence=sentenceExperiment(text=>newSessions[3].append(text));
    active.current={abort,sessions:newSessions,id,flush:()=>sentence.flush()};setSessions(newSessions);setRunning(true);
    try {
      // Deliberate client delay demonstrates metric #1. Never required by the library.
      await new Promise<void>((resolve,reject)=>{
        const onAbort=()=>{clearTimeout(timer);reject(new DOMException('Stopped','AbortError'));};
        const timer=setTimeout(()=>{abort.signal.removeEventListener('abort',onAbort);resolve();},preflight);
        abort.signal.addEventListener('abort',onAbort,{once:true});
      });
      if(abort.signal.aborted)return;
      const requestAt=performance.now();
      newSessions.forEach(s=>s.requestStarted(requestAt));
      const response=await fetch('/api/mock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scenario,playback}),signal:abort.signal});
      await readMockStream(response,event=>{
        if(abort.signal.aborted||active.current?.id!==id)return;
        if(event.type==='text'){
          const receivedAt=performance.now();
          if(event.text.trim())newSessions.forEach(s=>s.textReceived(receivedAt));
          arrivals.current.push({fragment:arrivals.current.length+1,receivedAt,characters:event.text.length});
          newSessions[0].append(event.text);newSessions[1].append(event.text);newSessions[2].append(event.text);sentence.push(event.text);
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
  useEffect(()=>{
    if(new URLSearchParams(location.search).get('autoplay')==='1'&&!autoplayed.current){autoplayed.current=true;void run();}
  },[]);
  return <main>
    <header><div className="eyebrow">STREAM READABLE / LATENCY LAB</div><h1>Read sooner.<br/><span>Measure the wait.</span></h1><p>Original and Smoothed receive the same fragments from one request. Compare the append transition while the reply is still arriving.</p></header>
    <form className="controls" onSubmit={e=>{e.preventDefault();void run();}}>
      <label>Stream scenario<select value={scenario} onChange={e=>setScenario(e.target.value)}><option value="normal">Normal fragments</option><option value="burst">1,000-fragment burst</option><option value="no-punctuation">No sentence boundary</option><option value="error">Transport error</option></select></label>
      <label>Playback<select value={playback} onChange={e=>setPlayback(e.target.value)}><option value="normal">Normal arrival intervals</option><option value="slow">Slow · 4× arrival intervals</option></select></label>
      <label>Client preparation (ms)<input type="number" min="0" max="3000" value={preflight} onChange={e=>setPreflight(Math.max(0,Math.min(3000,Number(e.target.value)||0)))}/></label>
      <button type="submit">{running?'Restart stream':'Run stream'}</button><button className="secondary" type="button" onClick={stop} disabled={!running}>Stop</button>
    </form>
    <div className="panels comparison">
      <Panel title="Original" description="The previous left-column renderer: first text immediately published, subsequent updates batched by frame. No text transition." stream={streams[0]} session={sessions[0]} id="frame"/>
      <Panel title="Smoothed" description="First fragment fully visible. Each later fragment enters the DOM immediately and clarifies from opacity 0.8 to 1 over 100 ms." stream={streams[1]} session={sessions[1]} id="smoothed" smoothed/>
    </div>
    <details className="experiments"><summary>Additional timing experiments</summary><div className="panels">
      <Panel title="Every event" description="Immediate per-event baseline. Already fast; can publish many redundant snapshots." stream={streams[2]} session={sessions[2]} id="event"/>
      <Panel title="Sentence buffer experiment" description="Comparison only. Holds text until punctuation or the terminal event." stream={streams[3]} session={sessions[3]} id="sentence"/>
    </div></details>
    <details className="arrivals"><summary>Shared fragment arrival timestamps</summary><pre data-testid="arrivals-json">{JSON.stringify(arrivals.current,null,2)}</pre></details>
    <footer><p><strong>Visible time is an estimate.</strong> Request start uses same-origin ResourceTiming when available, otherwise fetch dispatch. Visible time uses two animation frames after commit, with first-character viewport, clipping, and opacity checks. It does not measure physical display pixels or human comprehension.</p><p>Original has no content animation. Smoothed uses only a 100 ms new-fragment opacity transition; first text is fully opaque and old fragments never replay. Reduced motion disables the decoration. Slow playback changes synthetic arrival intervals equally for both panels, not model speed. No model calls, external assets, or credentials. Readability preference requires a user study.</p></footer>
  </main>;
}
createRoot(document.getElementById('root')!).render(<App/>);
