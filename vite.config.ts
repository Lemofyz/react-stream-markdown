import {defineConfig} from 'vitest/config';
import type {Plugin} from 'vite';
const mockStream = (): Plugin => ({
  name:'synthetic-stream-only',
  configureServer(server) { server.middlewares.use('/api/mock',async (req,res) => {
    if (req.method !== 'POST') { res.statusCode=405;res.end('POST required');return; }
    let raw='';
    for await (const part of req) { raw+=part; if(raw.length>4096){res.statusCode=413;res.end();return;} }
    let scenario: string;
    let playback: string;
    try { const body=JSON.parse(raw); scenario=body.scenario; playback=body.playback??'normal'; } catch {res.statusCode=400;res.end();return;}
    if(!['normal','burst','no-punctuation','error'].includes(scenario)){res.statusCode=400;res.end();return;}
    if(!['normal','slow'].includes(playback)){res.statusCode=400;res.end();return;}
    const speed=playback==='slow'?4:1;
    res.writeHead(200,{'Content-Type':'application/x-ndjson','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});
    res.flushHeaders();
    const timers: ReturnType<typeof setTimeout>[]=[];
    const write=(event: object)=>{if(!res.destroyed)res.write(JSON.stringify(event)+'\n');};
    const at=(ms:number,fn:()=>void)=>timers.push(setTimeout(()=>{if(!res.destroyed)fn();},200+Math.max(0,ms-200)*speed));
    // Entirely synthetic text; no models, accounts, keys, or external services.
    at(200,()=>write({type:'text',text:'You can start reading'}));
    if(scenario==='burst') {
      at(300,()=>{for(let i=0;i<1000;i++)write({type:'text',text:' x'});});
      at(550,()=>{write({type:'done'});res.end();});
    } else {
      at(380,()=>write({type:'text',text:' before the reply'}));
      at(560,()=>write({type:'text',text:scenario==='no-punctuation'?' has finished':' has finished.'}));
      at(740,()=>write({type:'text',text:'\n\nUpdates stay steady'}));
      if(scenario==='error') at(900,()=>{write({type:'error',message:'Synthetic transport failure'});res.end();});
      else {at(920,()=>write({type:'text',text:scenario==='no-punctuation'?' and no words are replayed':' and no words are replayed.'}));at(1100,()=>{write({type:'done'});res.end();});}
    }
    res.on('close',()=>timers.forEach(clearTimeout));
  }); },
});
export default defineConfig({optimizeDeps:{include:['react','react-dom','react-dom/client','react/jsx-runtime','react/jsx-dev-runtime']},plugins:[mockStream()],server:{host:'127.0.0.1',port:4318,strictPort:true},test:{environment:'jsdom',include:['tests/**/*.test.{ts,tsx}']}});
