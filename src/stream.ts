export type StreamStatus = 'idle' | 'streaming' | 'complete' | 'interrupted' | 'error';
export interface Marks {
  clickedAt: number | null;
  requestAt: number | null;
  /** Optional same-origin PerformanceResourceTiming.requestStart (not the API invocation). */
  networkRequestAt: number | null;
  firstTextAt: number | null;
  firstCommitAt: number | null;
  firstVisibleAt: number | null;
  endedAt: number | null;
}
export interface Snapshot {
  readonly id: number;
  readonly text: string;
  readonly status: StreamStatus;
  readonly error: string | null;
  readonly marks: Readonly<Marks>;
  readonly fragments: number;
  /** Number of text publications, not React commits or painted frames. */
  readonly publications: number;
}
export type Schedule = (callback: () => void) => () => void;
export interface StreamSession {
  readonly id: number;
  requestStarted(at?: number): void;
  networkRequestStarted(at: number): void;
  /** For adapters that buffer input: measure arrival before buffering. */
  textReceived(at?: number): void;
  append(delta: string): void;
  complete(): void;
  interrupt(): void;
  fail(error: unknown): void;
  committed(): void;
  visible(): void;
}

const emptyMarks = (): Marks => ({clickedAt:null,requestAt:null,networkRequestAt:null,firstTextAt:null,firstCommitAt:null,firstVisibleAt:null,endedAt:null});
const initial = (): Snapshot => ({id:0,text:'',status:'idle',error:null,marks:emptyMarks(),fragments:0,publications:0});

/** rAF wins in visible tabs; a timer prevents hidden tabs retaining pending text indefinitely. */
export const scheduleFrame: Schedule = callback => {
  let done = false;
  let frame: number | undefined;
  const run = () => {
    if (done) return;
    done = true;
    clearTimeout(timer);
    if (frame !== undefined) cancelAnimationFrame(frame);
    callback();
  };
  const timer = setTimeout(run, 50);
  if (typeof requestAnimationFrame === 'function') frame = requestAnimationFrame(run);
  return () => { done = true; clearTimeout(timer); if (frame !== undefined) cancelAnimationFrame(frame); };
};

/** Transport-independent delta accumulator. No sentence boundaries and no artificial typing delays. */
export function createTextStream(options: {now?: () => number; schedule?: Schedule; batch?: boolean} = {}) {
  const now = options.now ?? (() => performance.now());
  const schedule = options.schedule ?? scheduleFrame;
  let snapshot = initial();
  let fullText = '';
  let fragments = 0;
  let cancel: (() => void) | null = null;
  let disposed = false;
  const listeners = new Set<() => void>();
  const publish = (next: Snapshot) => { snapshot = next; for (const listener of listeners) listener(); };
  const cancelPending = () => { cancel?.(); cancel = null; };
  const flush = () => {
    cancelPending();
    if (snapshot.text !== fullText || snapshot.fragments !== fragments) {
      publish({...snapshot,text:fullText,fragments,publications:snapshot.publications + 1});
    }
  };
  const begin = (clickedAt = now()): StreamSession => {
    if (disposed) throw new Error('Cannot begin a disposed stream');
    cancelPending();
    fullText = ''; fragments = 0;
    const id = snapshot.id + 1;
    publish({...initial(),id,status:'streaming',marks:{...emptyMarks(),clickedAt}});
    const current = () => !disposed && snapshot.id === id;
    const active = () => current() && snapshot.status === 'streaming';
    const mark = (key: keyof Marks, at = now()) => {
      if (!current() || snapshot.marks[key] !== null) return;
      // Copy the object: useSyncExternalStore requires immutable snapshots.
      publish({...snapshot,marks:{...snapshot.marks,[key]:at}});
    };
    const terminal = (status: StreamStatus, error: string | null = null) => {
      if (!active()) return;
      flush();
      publish({...snapshot,status,error,marks:{...snapshot.marks,endedAt:now()}});
    };
    const session: StreamSession = {
      id,
      requestStarted(at) { if (active()) mark('requestAt',at); },
      networkRequestStarted(at) { if (current() && snapshot.marks.requestAt !== null && at >= (snapshot.marks.clickedAt ?? 0)) mark('networkRequestAt',at); },
      textReceived(at) { if (active()) mark('firstTextAt',at); },
      append(delta) {
        if (!active() || delta === '') return;
        if (snapshot.marks.requestAt === null) throw new Error('Call requestStarted immediately before fetch');
        fullText += delta; fragments++;
        if (fullText.trim() && snapshot.marks.firstTextAt === null) mark('firstTextAt');
        // The first readable fragment skips the frame queue. React still schedules its own commit.
        if ((snapshot.text.trim() === '' && fullText.trim() !== '') || options.batch === false) {
          flush();
        } else if (cancel === null) {
          cancel = schedule(() => { cancel = null; if (active()) flush(); });
        }
      },
      complete() { terminal('complete'); },
      interrupt() { terminal('interrupted'); },
      fail(error) { terminal('error',error instanceof Error ? error.message : String(error)); },
      committed() { if (current() && snapshot.text.trim()) mark('firstCommitAt'); },
      visible() { if (current() && snapshot.text.trim()) mark('firstVisibleAt'); },
    };
    return session;
  };
  return {
    begin,
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    dispose() { cancelPending(); disposed = true; listeners.clear(); },
  };
}
export type TextStream = ReturnType<typeof createTextStream>;

const difference = (end: number | null, start: number | null) => end === null || start === null ? null : end - start;
export function durations(marks: Readonly<Marks>) {
  return {
    clickToRequestMs: difference(marks.networkRequestAt ?? marks.requestAt,marks.clickedAt),
    timeToFirstTextMs: difference(marks.firstTextAt,marks.networkRequestAt ?? marks.requestAt),
    firstTextToCommitMs: difference(marks.firstCommitAt,marks.firstTextAt),
    firstTextToVisibleEstimateMs: difference(marks.firstVisibleAt,marks.firstTextAt),
    firstTextToEndMs: difference(marks.endedAt,marks.firstTextAt),
  };
}
