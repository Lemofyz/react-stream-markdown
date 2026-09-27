import type {StreamSession} from './stream.js';

/** Same first-readable-character probe for plain and segmented DOM. Not physical paint timing. */
function firstCharacterVisible(element: HTMLElement): boolean {
  if (document.visibilityState !== 'visible') return false;
  const walker = document.createTreeWalker(element,NodeFilter.SHOW_TEXT);
  let node: Node | null;
  let offset = -1;
  while ((node = walker.nextNode())) {
    offset = node.textContent?.search(/\S/) ?? -1;
    if (offset >= 0) break;
  }
  if (!node || offset < 0) return false;
  const range = document.createRange();
  range.setStart(node,offset); range.setEnd(node,offset+1);
  const box = range.getBoundingClientRect();
  if (box.width <= 0 || box.height <= 0 || box.bottom <= 0 || box.top >= innerHeight || box.right <= 0 || box.left >= innerWidth) return false;
  let opacity = 1;
  for (let parent: HTMLElement | null = node.parentElement; parent; parent = parent.parentElement) {
    const style = getComputedStyle(parent);
    opacity *= Number(style.opacity);
    if (style.visibility !== 'visible' || style.display === 'none' || opacity < 0.95) return false;
    if (/(auto|scroll|hidden|clip)/.test(style.overflowX + style.overflowY)) {
      const clip = parent.getBoundingClientRect();
      if (box.bottom <= clip.top || box.top >= clip.bottom || box.right <= clip.left || box.left >= clip.right) return false;
    }
  }
  return true;
}

export function observeFirstVisible(element: HTMLElement,session: StreamSession): () => void {
  let frame = 0;
  let cancelled = false;
  const sample = () => {
    if (cancelled) return;
    if (firstCharacterVisible(element)) session.visible();
    else frame = requestAnimationFrame(sample);
  };
  // Two frames after a DOM commit, with an intervening rendering opportunity.
  frame = requestAnimationFrame(() => { frame = requestAnimationFrame(sample); });
  return () => { cancelled = true; cancelAnimationFrame(frame); };
}
