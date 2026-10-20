import {describe,expect,it} from 'vitest';
import {act,render} from '@testing-library/react';
import katex from 'katex';
import {parseInline,parseMarkdown} from '../src/markdown';
import {createTextStream,katexMath,StreamingMarkdown} from '../src/index';

describe('math parsing',()=>{
 it('is off unless asked for',()=>{
   expect(parseInline('costs $5 or $x$')).toEqual([{type:'text',value:'costs $5 or $x$'}]);
   expect(parseMarkdown('$$\nx\n$$')[0].type).toBe('paragraph');
 });
 it('parses inline and display delimiters',()=>{
   expect(parseInline('a $x^2$ b',false,0,true)).toEqual([{type:'text',value:'a '},{type:'math',value:'x^2',display:false},{type:'text',value:' b'}]);
   expect(parseInline('\\(a_1\\) and \\[b\\]',false,0,true)).toEqual([{type:'math',value:'a_1',display:false},{type:'text',value:' and '},{type:'math',value:'b',display:true}]);
   expect(parseInline('so $$e=mc^2$$ holds',false,0,true)[1]).toEqual({type:'math',value:'e=mc^2',display:true});
   expect(parseInline('*$a*b$*',false,0,true)).toEqual([{type:'em',children:[{type:'math',value:'a*b',display:false}]}]);
 });
 it('keeps prices and escaped dollars as text',()=>{
   expect(parseInline('from $5 to $10',false,0,true)).toEqual([{type:'text',value:'from $5 to $10'}]);
   expect(parseInline('$5 and $6',false,0,true)).toEqual([{type:'text',value:'$5 and $6'}]);
   expect(parseInline('\\$x\\$',false,0,true)).toEqual([{type:'text',value:'$x$'}]);
 });
 it('parses display blocks, including ones that interrupt a paragraph',()=>{
   expect(parseMarkdown('Area:\n$$\n\\pi r^2\n$$\nDone.',{math:true})).toEqual([
     {type:'paragraph',children:[{type:'text',value:'Area:'}]},
     {type:'math',value:'\\pi r^2',pending:false},
     {type:'paragraph',children:[{type:'text',value:'Done.'}]},
   ]);
   expect(parseMarkdown('\\[\na+b\n\\]',{math:true})).toEqual([{type:'math',value:'a+b',pending:false}]);
   expect(parseMarkdown('$$a$$ is inline here',{math:true})[0].type).toBe('paragraph');
   expect(parseMarkdown('- item\n  $$x$$',{math:true})[0]).toMatchObject({type:'list',items:[{children:[{type:'paragraph'},{type:'math',value:'x'}]}]});
 });
 it('leaves unfinished math as text while streaming',()=>{
   expect(parseInline('so $x^2 +',true,0,true)).toEqual([{type:'text',value:'so $x^2 +'}]);
   expect(parseMarkdown('$$\n\\frac{a}{b}\n$',{open:true,math:true})).toEqual([{type:'math',value:'\\frac{a}{b}',pending:true}]);
   expect(parseMarkdown('$$\nx',{math:true})).toEqual([{type:'math',value:'x',pending:false}]);
 });
});

describe('math rendering',()=>{
 const stream=()=>{const s=createTextStream({batch:false});const session=s.begin();session.requestStarted();return {s,session};};
 it('shows TeX source without a renderer',()=>{
   const {s,session}=stream();
   const {container}=render(<StreamingMarkdown stream={s} math animate={false}/>);
   act(()=>{session.append('Let $x^2$ be');session.complete();});
   expect(container.querySelector('.rsm-math')?.textContent).toBe('x^2');
 });
 it('typesets with KaTeX once the formula is closed, and keeps it while more text streams',()=>{
   const {s,session}=stream();
   const {container}=render(<StreamingMarkdown stream={s} math={katexMath(katex)} animate={false}/>);
   act(()=>session.append('Euler:\n\n$$\ne^{i\\pi}'));
   expect(container.querySelector('.rsm-math-pending')?.textContent).toBe('e^{i\\pi}');
   expect(container.querySelector('.katex')).toBeNull();
   act(()=>session.append(' + 1 = 0\n$$\n\nNext'));
   const typeset=container.querySelector('.rsm-math-display .katex');
   expect(typeset).not.toBeNull();
   expect(container.querySelector('annotation')?.textContent).toBe('e^{i\\pi} + 1 = 0');
   act(()=>{session.append(' line with $a_1$.');session.complete();});
   expect(container.querySelector('.rsm-math-display .katex')).toBe(typeset);
   expect(container.querySelectorAll('.katex')).toHaveLength(2);
 });
 it('does not let model TeX create links',()=>{
   const {s,session}=stream();
   const {container}=render(<StreamingMarkdown stream={s} math={katexMath(katex)} animate={false}/>);
   act(()=>{session.append('$\\href{javascript:alert(1)}{x}$');session.complete();});
   expect(container.querySelector('a')).toBeNull();
 });
});
