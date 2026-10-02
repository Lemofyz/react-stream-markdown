import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';
import {createTextStream, StreamingMarkdown} from '../src/index.ts';
import {Streamdown as VercelStreamdown} from 'streamdown';
import {Streamdown as LobeStreamdown} from '@lobehub/streamdown';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {makeDoc, chunks} from './doc.js';

const doc = makeDoc();
const pieces = chunks(doc, 24);

const frame = () => new Promise(r => requestAnimationFrame(() => r()));
const settle = async host => {
  // Wait until the text stops changing (paced renderers keep revealing after the last chunk).
  let last = -1, stable = 0;
  for (let i = 0; i < 1200 && stable < 30; i++) { await frame(); const n = host.textContent.length; stable = n === last ? stable + 1 : 0; last = n; }
};

/** One chunk per animation frame, like a fast model. Records each synchronous update incl. layout. */
async function measure(name, setup) {
  const host = document.createElement('div');
  host.style.width = '720px';
  document.body.append(host);
  const {push, done, dispose} = setup(host);
  const times = [];
  let text = '';
  const before = await window.cpuMs();
  for (const piece of pieces) {
    text += piece;
    const t = performance.now();
    push(piece, text);
    host.offsetHeight; // include style recalculation and layout
    times.push(performance.now() - t);
    await frame();
  }
  done?.(text);
  await settle(host);
  const cpu = (await window.cpuMs()) - before;
  const rendered = host.textContent.replace(/\s+/g, '').length;
  dispose();
  host.remove();
  return {name, chunks: pieces.length, chars: doc.length, cpuMs: Math.round(cpu - 30 * 0.1),
    maxMs: +Math.max(...times).toFixed(1), lastAvgMs: +(times.slice(-20).reduce((a, b) => a + b, 0) / 20).toFixed(2), renderedChars: rendered};
}
function reactSetup(render) {
  return host => {
    const root = createRoot(host);
    return {push: (_, text) => flushSync(() => root.render(render(text, true))),
      done: text => flushSync(() => root.render(render(text, false))), dispose: () => root.unmount()};
  };
}
function ours(animate) {
  return host => {
    const stream = createTextStream({batch: false});
    const session = stream.begin();
    session.requestStarted();
    const root = createRoot(host);
    flushSync(() => root.render(<StreamingMarkdown stream={stream} session={session} animate={animate}/>));
    return {push: piece => session.append(piece), done: () => session.complete(), dispose: () => root.unmount()};
  };
}

window.runBench = async (only) => {
  const runs = [
    ['react-stream-markdown StreamingMarkdown', ours(false)],
    ['react-stream-markdown StreamingMarkdown + fade', ours(true)],
    ['streamdown (Vercel)', reactSetup((text, streaming) => <VercelStreamdown mode={streaming ? 'streaming' : 'static'} isAnimating={streaming}>{text}</VercelStreamdown>)],
    ['@lobehub/streamdown (realtime)', reactSetup(text => <LobeStreamdown content={text} smoothing="realtime" remarkPlugins={[remarkGfm]}/>)],
    ['react-markdown + remark-gfm (re-render all)', reactSetup(text => <Markdown remarkPlugins={[remarkGfm]}>{text}</Markdown>)],
  ];
  const results = [];
  for (const [name, setup] of runs.filter(([n]) => !only || n.startsWith(only))) {
    results.push(await measure(name, setup));
    await new Promise(r => setTimeout(r, 50));
  }
  return results;
};
