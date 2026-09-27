import {createTextStream,durations} from '../lib/stream.js';
import {mkdirSync,writeFileSync} from 'node:fs';
const scenarios=[
 {name:'punctuated',events:[[280,'You can read'],[460,' before completion'],[640,'. More'],[820,' text.']],end:1000},
 {name:'no-punctuation',events:[[280,'You can read'],[460,' before completion'],[640,' and more'],[820,' text']],end:1000},
];
function run(scenario,policy){
 let time=0,queue=[];const stream=createTextStream({now:()=>time,batch:policy!=='event',schedule:cb=>{queue.push(cb);return()=>{queue=queue.filter(x=>x!==cb);};}});
 const session=stream.begin();time=80;session.requestStarted();let pending='';let firstPublicationAt=null;
 const emit=text=>{session.append(text);if(firstPublicationAt===null)firstPublicationAt=time;};
 for(const [at,text] of scenario.events){time=at;session.textReceived();if(policy==='sentence'){pending+=text;const boundary=pending.search(/[.!?。！？]/);if(boundary>=0){emit(pending.slice(0,boundary+1));pending=pending.slice(boundary+1);}}else emit(text);queue.splice(0).forEach(cb=>cb());}
 time=scenario.end;if(pending)emit(pending);session.complete();
 return {scenario:scenario.name,policy,firstPublicationAfterFirstTextMs:firstPublicationAt-280,...durations(stream.getSnapshot().marks),text:stream.getSnapshot().text};
}
const burst=[];
for(const batch of [true,false]){const stream=createTextStream({batch,schedule:()=>()=>{}});const s=stream.begin();s.requestStarted();for(let i=0;i<1000;i++)s.append('x');s.complete();burst.push({policy:batch?'frame':'event',fragments:1000,publications:stream.getSnapshot().publications,length:stream.getSnapshot().text.length});}
const result={kind:'deterministic prescribed-clock simulation; not a browser performance claim',runs:scenarios.flatMap(s=>['frame','event','sentence'].map(p=>run(s,p))),burst};
mkdirSync('docs',{recursive:true});writeFileSync('docs/deterministic-results.json',JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result,null,2));
