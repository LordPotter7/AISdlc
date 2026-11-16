import "@testing-library/jest-dom/vitest";

// jsdom has no layout engine. React Flow measures its container with ResizeObserver;
// a no-op observer lets the graph mount (with zero size) so tests exercise the view.
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (!("ResizeObserver" in globalThis)) {
  (globalThis as unknown as { ResizeObserver: typeof NoopResizeObserver }).ResizeObserver = NoopResizeObserver;
}
if (!("DOMMatrixReadOnly" in globalThis)) {
  (globalThis as unknown as { DOMMatrixReadOnly: unknown }).DOMMatrixReadOnly = class {
    m22 = 1;
    constructor(_transform?: string) {}
  };
}

// CodeMirror measures text with Range geometry, which jsdom does not implement.
// Empty rectangles let the editor mount and handle input; layout is never asserted.
const emptyRects = () => Object.assign([], { item: () => null }) as unknown as DOMRectList;
const emptyRect = () => ({ x: 0, y: 0, width: 0, height: 0, top: 0, right: 0, bottom: 0, left: 0, toJSON() {} }) as DOMRect;
if (!Range.prototype.getClientRects) Range.prototype.getClientRects = emptyRects;
if (!Range.prototype.getBoundingClientRect) Range.prototype.getBoundingClientRect = emptyRect;
