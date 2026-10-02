import {useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {createTextStream,StreamingMarkdown,useStreamingText,type StreamSession,type TextStream} from '../src/index';
import {chunk,replies,type Lang} from './replies';
import '../src/style.css';
import './landing.css';

const text = {
  en: {
    tagline: 'Show AI replies while they are still being written.',
    lead: 'Long answers take seconds to generate. react-stream-markdown renders Markdown as each chunk arrives, so people start reading with the first token instead of staring at a spinner until the last one.',
    replay: 'Replay', speed: 'Model speed', slow: 'Slow', typical: 'Typical', fast: 'Fast', fade: 'Fade-in',
    waitTitle: 'Wait for the full reply', liveTitle: 'react-stream-markdown',
    waitNote: 'Generating the complete answer…', first: 'First words', done: 'Complete',
    headStart: (s: string) => `Reader started ${s} earlier`, question: 'Question',
    synthetic: 'Both panels replay the same synthetic chunk timeline in your browser. No model is called.',
    other: '中文',
  },
  zh: {
    tagline: '边生成，边阅读。',
    lead: '长回答往往要生成好几秒。react-stream-markdown 在每个片段到达时就把 Markdown 渲染出来，用户收到第一个 token 就能开始读，而不是盯着加载动画等到最后一个。',
    replay: '重新播放', speed: '模型速度', slow: '慢', typical: '一般', fast: '快', fade: '淡入效果',
    waitTitle: '等完整回复再显示', liveTitle: 'react-stream-markdown',
    waitNote: '正在生成完整回答…', first: '首字出现', done: '全部完成',
    headStart: (s: string) => `用户提前 ${s} 开始阅读`, question: '问题',
    synthetic: '两个面板在浏览器里回放同一条模拟片段时间线，没有调用任何模型。',
    other: 'English',
  },
};
const speeds = {slow:15,typical:30,fast:60} as const; // chunks per second
type Speed = keyof typeof speeds;
const FIRST_TOKEN_MS = 600;
const seconds = (ms: number|null) => ms === null ? '—' : `${(ms / 1000).toFixed(1)} s`;

/** Keep the newest text in view unless the reader scrolled up. */
function useFollow(box: React.RefObject<HTMLDivElement|null>) {
  useEffect(() => {
    const element = box.current!;
    let stick = true;
    const onScroll = () => { stick = element.scrollHeight - element.scrollTop - element.clientHeight < 48; };
    const observer = new MutationObserver(() => { if (stick) element.scrollTop = element.scrollHeight; });
    observer.observe(element,{childList:true,subtree:true,characterData:true});
    element.addEventListener('scroll',onScroll,{passive:true});
    return () => { observer.disconnect(); element.removeEventListener('scroll',onScroll); };
  },[box]);
}

function Reply({stream,session,label,animate,waiting}: {stream:TextStream;session:StreamSession|null;label:string;animate:boolean;waiting?:string|null}) {
  const box = useRef<HTMLDivElement>(null);
  const state = useStreamingText(stream);
  useFollow(box);
  return <div className="reply" ref={box}>
    {waiting && !state.text && <div className="waiting" aria-live="polite"><span className="spinner" aria-hidden/>{waiting}<div className="skeleton" aria-hidden><i/><i/><i/></div></div>}
    <StreamingMarkdown stream={stream} session={session} label={label} animate={animate}/>
  </div>;
}

function App() {
  const params = new URLSearchParams(location.search);
  const [lang,setLang] = useState<Lang>(() => params.get('lang') === 'zh' ? 'zh' : 'en');
  const [speed,setSpeed] = useState<Speed>(() => (params.get('speed') as Speed) in speeds ? params.get('speed') as Speed : 'typical');
  const [animate,setAnimate] = useState(params.get('fade') !== '0');
  const [streams] = useState(() => ({wait:createTextStream(),live:createTextStream()}));
  const [sessions,setSessions] = useState<{wait:StreamSession|null;live:StreamSession|null}>({wait:null,live:null});
  const [clock,setClock] = useState({start:0,now:0,first:null as number|null,done:null as number|null});
  const timers = useRef<number[]>([]);
  const t = text[lang];

  const run = (language = lang,pace = speed) => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    const wait = streams.wait.begin();
    const live = streams.live.begin();
    wait.requestStarted(); live.requestStarted();
    setSessions({wait,live});
    const start = performance.now();
    setClock({start,now:start,first:null,done:null});
    const answer = replies[language].answer;
    const pieces = chunk(answer);
    let at = FIRST_TOKEN_MS;
    pieces.forEach((piece,index) => {
      // Same jittered timeline for both panels; only what each one shows differs.
      if (index) at += (1000 / speeds[pace]) * (0.5 + ((index * 37) % 10) / 10);
      timers.current.push(window.setTimeout(() => {
        live.append(piece);
        if (index === 0) setClock(c => ({...c,first:performance.now() - start}));
      },at));
    });
    timers.current.push(window.setTimeout(() => {
      live.complete();
      wait.append(answer); wait.complete();
      setClock(c => ({...c,done:performance.now() - start}));
    },at + 1));
  };
  useEffect(() => { run(); return () => timers.current.forEach(clearTimeout); },[]);
  useEffect(() => {
    if (clock.done !== null || !clock.start) return;
    const id = window.setInterval(() => setClock(c => ({...c,now:performance.now()})),100);
    return () => clearInterval(id);
  },[clock.done,clock.start]);
  useEffect(() => { document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en'; },[lang]);

  const elapsed = clock.done ?? (clock.now - clock.start);
  const headStart = clock.done !== null && clock.first !== null ? clock.done - clock.first : null;
  return <main>
    <header>
      <div className="brand"><span className="dot"/>react-stream-markdown</div>
      <nav>
        <button className="link" onClick={() => { const next = lang === 'en' ? 'zh' : 'en'; setLang(next); run(next); }}>{t.other}</button>
        <a href="https://github.com/Lemofyz/react-stream-markdown">GitHub</a>
      </nav>
    </header>
    <section className="hero">
      <h1>{t.tagline}</h1>
      <p>{t.lead}</p>
      <pre className="install"><code>npm i react-stream-markdown</code></pre>
    </section>
    <form className="controls" onSubmit={e => { e.preventDefault(); run(); }}>
      <button type="submit">↻ {t.replay}</button>
      <label>{t.speed}<select value={speed} onChange={e => { const next = e.target.value as Speed; setSpeed(next); run(lang,next); }}>
        {(Object.keys(speeds) as Speed[]).map(key => <option key={key} value={key}>{t[key]}</option>)}
      </select></label>
      <label className="toggle"><input type="checkbox" checked={animate} onChange={e => setAnimate(e.target.checked)}/>{t.fade}</label>
      <span className={`headstart ${headStart !== null ? 'show' : ''}`} role="status">{headStart !== null ? t.headStart(seconds(headStart)) : ''}</span>
    </form>
    <p className="question"><b>{t.question}</b>{replies[lang].question}</p>
    <div className="panels">
      <section className="panel muted" data-testid="wait">
        <div className="panel-head"><h2>{t.waitTitle}</h2>
          <dl><div><dt>{t.first}</dt><dd>{seconds(clock.done ?? null)}</dd></div><div><dt>{t.done}</dt><dd>{seconds(clock.done)}</dd></div></dl>
        </div>
        <Reply stream={streams.wait} session={sessions.wait} label={t.waitTitle} animate={false} waiting={clock.done === null ? `${t.waitNote} ${seconds(elapsed)}` : null}/>
      </section>
      <section className="panel live" data-testid="live">
        <div className="panel-head"><h2>{t.liveTitle}</h2>
          <dl><div><dt>{t.first}</dt><dd>{seconds(clock.first)}</dd></div><div><dt>{t.done}</dt><dd>{seconds(clock.done)}</dd></div></dl>
        </div>
        <Reply stream={streams.live} session={sessions.live} label={t.liveTitle} animate={animate}/>
      </section>
    </div>
    <footer>{t.synthetic} <a href="https://github.com/Lemofyz/react-stream-markdown#readme">README</a></footer>
  </main>;
}
createRoot(document.getElementById('root')!).render(<App/>);
