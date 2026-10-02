/** Synthetic replies for the demo. No model is called; chunks are replayed on a timer. */
export const replies = {
  en: {
    question: 'Why should a long AI answer stream instead of arriving all at once?',
    answer: `## Streaming beats waiting

A model writes a long answer one piece at a time, so **the first sentence exists seconds before the last one**. If the page waits for the complete response, the reader stares at a spinner for the entire generation time.

Streaming removes that wait:

1. The server forwards each chunk the moment the model emits it.
2. The page appends it right away, with *no sentence buffering*.
3. The reader starts at the top while the rest is still being written.

\`\`\`ts
for await (const chunk of response) {
  session.append(chunk);
}
session.complete();
\`\`\`

| | Wait for full reply | Stream Readable |
|---|---|---|
| First words on screen | after the last token | with the first token |
| Reading starts | when generation ends | immediately |

> Finished paragraphs never re-render, half-written \`**bold**\` never flashes raw markers, and new words fade in gently.`,
  },
  zh: {
    question: '为什么 AI 的长回答应该流式输出，而不是等全部生成完再显示？',
    answer: `## 边生成，边阅读

模型是一段一段写出长回答的，所以**第一句话比最后一句早好几秒就已经存在了**。如果页面要等完整结果再显示，用户在整个生成过程中只能盯着加载动画。

流式显示把这段等待去掉了：

1. 服务器在模型吐出每个片段时立即转发。
2. 页面马上追加显示，*不等句子结束*。
3. 用户从开头读起，后面的内容还在继续生成。

\`\`\`ts
for await (const chunk of response) {
  session.append(chunk);
}
session.complete();
\`\`\`

| | 等完整回复 | Stream Readable |
|---|---|---|
| 第一行字出现 | 最后一个 token 之后 | 第一个 token 到达时 |
| 开始阅读 | 生成结束后 | 立刻 |

> 已完成的段落不会重新渲染，写到一半的 \`**粗体**\` 不会闪出原始符号，新文字会轻轻淡入。`,
  },
};
export type Lang = keyof typeof replies;

/** Deterministic pseudo-random chunking that resembles model token output. */
export function chunk(text: string,seed = 7): string[] {
  let state = seed;
  const random = () => { state = (state * 1103515245 + 12345) % 2147483648; return state / 2147483648; };
  const out: string[] = [];
  const cjk = /[㐀-鿿]/;
  for (let i = 0; i < text.length;) {
    const size = cjk.test(text[i]) ? 1 + Math.floor(random() * 2) : 2 + Math.floor(random() * 5);
    out.push(text.slice(i,i + size));
    i += size;
  }
  return out;
}
