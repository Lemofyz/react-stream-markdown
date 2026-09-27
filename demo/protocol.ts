export type MockEvent = {type:'text';text:string} | {type:'done'} | {type:'error';message:string};
/** Small NDJSON example transport; the library itself does not prescribe a wire protocol. */
export async function readMockStream(response: Response,onEvent: (event: MockEvent) => void) {
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  if (!response.body) throw new Error('Response has no body');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let doneEvent = false;
  const line = (raw: string) => {
    if (!raw.trim()) return;
    const event: unknown = JSON.parse(raw);
    if (!event || typeof event !== 'object' || !('type' in event)) throw new Error('Invalid stream event');
    const value = event as Record<string,unknown>;
    if (doneEvent) throw new Error('Event after terminal event');
    if (value.type === 'text' && typeof value.text === 'string') onEvent({type:'text',text:value.text});
    else if (value.type === 'done') { doneEvent = true; onEvent({type:'done'}); }
    else if (value.type === 'error' && typeof value.message === 'string') throw new Error(value.message);
    else throw new Error('Invalid stream event');
  };
  try {
    while (true) {
      const result = await reader.read();
      buffer += decoder.decode(result.value,{stream:!result.done});
      let boundary: number;
      while ((boundary=buffer.indexOf('\n')) >= 0) { line(buffer.slice(0,boundary)); buffer=buffer.slice(boundary+1); }
      if (result.done) break;
    }
    if (buffer.trim()) line(buffer);
    if (!doneEvent) throw new Error('Stream closed before done');
  } finally { try { await reader.cancel(); } finally { reader.releaseLock(); } }
}

/** Deliberately simple comparison-only buffer, NOT a production sentence segmenter. */
export function sentenceExperiment(emit: (text: string) => void) {
  let pending = '';
  return {
    push(delta: string) {
      pending += delta;
      const boundary = pending.search(/[.!?。！？]/);
      if (boundary >= 0) { emit(pending.slice(0,boundary+1)); pending=pending.slice(boundary+1); }
    },
    flush() { if (pending) emit(pending); pending=''; },
  };
}
