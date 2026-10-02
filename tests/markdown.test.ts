import {describe,expect,it} from 'vitest';
import {parseBlocks,parseInline,parseMarkdown,safeHref} from '../src/markdown';

describe('markdown parser',()=>{
 it('parses the blocks chat replies use',()=>{
   const blocks=parseMarkdown('# Title\n\nText **bold** *em* `code` ~~gone~~\n\n- a\n- [x] b\n\n1. one\n2. two\n\n> quote\n\n```ts\nconst a = 1;\n```\n\n---\n\n| a | b |\n|:-|-:|\n| 1 | 2 |');
   expect(blocks.map(b=>b.type)).toEqual(['heading','paragraph','list','list','blockquote','code','hr','table']);
   expect(blocks[1]).toEqual({type:'paragraph',children:[{type:'text',value:'Text '},{type:'strong',children:[{type:'text',value:'bold'}]},{type:'text',value:' '},{type:'em',children:[{type:'text',value:'em'}]},{type:'text',value:' '},{type:'code',value:'code'},{type:'text',value:' '},{type:'del',children:[{type:'text',value:'gone'}]}]});
   expect(blocks[2]).toMatchObject({type:'list',ordered:false,tight:true,items:[{checked:null},{checked:true}]});
   expect(blocks[5]).toEqual({type:'code',lang:'ts',value:'const a = 1;'});
   expect(blocks[7]).toMatchObject({type:'table',align:['left','right'],rows:[[[{value:'1'}],[{value:'2'}]]]});
 });
 it('nests lists and keeps intraword underscores literal',()=>{
   expect(parseMarkdown('1. a\n   - b\n2. c')[0]).toMatchObject({type:'list',ordered:true,items:[{children:[{type:'paragraph'},{type:'list',ordered:false}]},{}]});
   expect(parseInline('snake_case_name')).toEqual([{type:'text',value:'snake_case_name'}]);
   expect(parseInline('2 * 3 * 4')).toEqual([{type:'text',value:'2 * 3 * 4'}]);
 });
 it('renders unfinished syntax optimistically only while streaming',()=>{
   expect(parseInline('a **bo',true)).toEqual([{type:'text',value:'a '},{type:'strong',children:[{type:'text',value:'bo'}]}]);
   expect(parseInline('a **bo')).toEqual([{type:'text',value:'a **bo'}]);
   expect(parseInline('see `npm i',true)).toEqual([{type:'text',value:'see '},{type:'code',value:'npm i'}]);
   expect(parseInline('dangling *',true)).toEqual([{type:'text',value:'dangling '}]);
   expect(parseInline('[docs](https://exa',true)).toEqual([{type:'link',href:null,children:[{type:'text',value:'docs'}]}]);
   expect(parseMarkdown('```js\nlet a\n``',{open:true})).toEqual([{type:'code',lang:'js',value:'let a'}]);
   expect(parseMarkdown('| a | b |',{open:true})[0]).toMatchObject({type:'table',rows:[]});
 });
 it('only links to web and mail URLs',()=>{
   for(const bad of ['javascript:alert(1)',' javascript:alert(1)','JaVaScRiPt:x','data:text/html,x','vbscript:x'])expect(safeHref(bad)).toBeNull();
   for(const good of ['https://a.b','http://a.b/c?d#e','mailto:a@b.c','/relative','#anchor'])expect(safeHref(good)).toBe(good);
   expect(parseInline('[x](javascript:alert(1))')).toEqual([{type:'link',href:null,children:[{type:'text',value:'x'}]}]);
   expect(parseInline('<img src=x onerror=alert(1)>')).toEqual([{type:'text',value:'<img src=x onerror=alert(1)>'}]);
 });
 it('reports source ranges for top-level blocks',()=>{
   const src='# A\n\npara\n\n- x';
   expect(parseBlocks(src).map(b=>src.slice(b.start,b.end))).toEqual(['# A\n','para\n','- x']);
 });
});

describe('markdown parser limits',()=>{
 it('handles thousands of unclosed markers without deep recursion',()=>{
   const text='*a '.repeat(5000);
   const start=performance.now();
   const flat=(nodes:ReturnType<typeof parseInline>):string=>nodes.map(n=>'value' in n?n.value:'children' in n?flat(n.children):'').join('');
   expect(flat(parseInline(text,true))).toContain('a a a');
   expect(performance.now()-start).toBeLessThan(2000);
 });
});
