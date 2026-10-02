// Minified + gzipped size of what each library adds to an app that already ships React.
import {build} from 'vite';
import {gzipSync} from 'node:zlib';
const entries = {
  'stream-readable (StreamingMarkdown)': 'entries/stream-readable.js',
  'streamdown (Vercel, core only)': 'entries/vercel-streamdown.js',
  '@lobehub/streamdown': 'entries/lobehub-streamdown.js',
  'react-markdown + remark-gfm': 'entries/react-markdown.js',
};
const rows = [];
for (const [name, entry] of Object.entries(entries)) {
  const result = await build({
    logLevel: 'silent', configFile: false,
    build: {write: false, minify: true, cssCodeSplit: false,
      lib: {entry, formats: ['es'], fileName: 'out'},
      rollupOptions: {external: [/^react($|\/)/, /^react-dom($|\/)/]}},
  });
  const output = [result].flat()[0].output;
  // Eager = the entry plus everything it imports statically; lazy = dynamic-import-only chunks.
  const byName = new Map(output.filter(c => c.type === 'chunk').map(c => [c.fileName, c]));
  const eager = new Set();
  const visit = file => { if (eager.has(file)) return; eager.add(file); byName.get(file)?.imports.forEach(visit); };
  output.filter(c => c.type === 'chunk' && c.isEntry).forEach(c => visit(c.fileName));
  let min = 0, gz = 0, lazy = 0;
  for (const chunk of output) {
    const code = chunk.type === 'chunk' ? chunk.code : String(chunk.source);
    if (chunk.type === 'chunk' && !eager.has(chunk.fileName)) { lazy += gzipSync(code).length; continue; }
    min += Buffer.byteLength(code); gz += gzipSync(code).length;
  }
  rows.push({name, min, gz, lazy});
}
const kb = n => (n / 1024).toFixed(1) + ' kB';
console.log('| Library | Minified | Gzipped | Lazy chunks (gz) |\n|---|--:|--:|--:|');
for (const r of rows) console.log(`| ${r.name} | ${kb(r.min)} | ${kb(r.gz)} | ${r.lazy ? kb(r.lazy) : '—'} |`);
