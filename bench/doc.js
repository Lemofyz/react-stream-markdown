/** A long, deeply nested reply: headings, 3-level lists, code, tables, quotes and inline styles. */
export function makeDoc(sections = 12) {
  let out = '';
  for (let s = 1; s <= sections; s++) {
    out += `## Section ${s}: streaming **details** and \`code\`\n\n`;
    out += `This paragraph has *emphasis*, **strong text**, a [link](https://example.com/${s}) and ~~old~~ words. `.repeat(3) + '\n\n';
    out += `- Level one item ${s}\n  - Level two with **bold**\n    - Level three with \`inline\` code\n    - Another deep item\n  - Back to two\n- Second top item\n\n`;
    out += `1. First step\n2. Second step with *emphasis*\n3. Third step\n\n`;
    out += '```ts\n' + `export function handler${s}(input: string) {\n  const parts = input.split(',');\n  return parts.map(p => p.trim()).filter(Boolean);\n}\n` + '```\n\n';
    out += `| Metric | Value | Note |\n|---|--:|---|\n| latency | ${s * 12} ms | **fast** |\n| size | ${s} kB | small |\n| score | ${s * 3} | ok |\n\n`;
    out += `> Quote ${s}: finished blocks never change.\n\n`;
  }
  return out;
}
export function chunks(text, size = 6) {
  const out = [];
  for (let i = 0; i < text.length; i += size) out.push(text.slice(i, i + size));
  return out;
}
