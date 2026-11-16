import { useEffect, useMemo, useRef } from "react";
import DOMPurify from "dompurify";
import MarkdownIt from "markdown-it";
import type { SpecIdentifier } from "@/lib/contract";

// Raw HTML is off, and the output is sanitised again: a brief can never run script in Studio.
const md = new MarkdownIt({ html: false, linkify: true, typographer: false });
const ID = /(?:REQ-(?:F|NFR)|AC|SC|TM|TB|TH|F)-\d{3,}/g;

/** Resolve a relative link against the file it is written in, inside the Intent. */
function resolve(from: string, href: string): string | null {
  if (/^[a-z]+:/i.test(href) || href.startsWith("#") || href.startsWith("/")) return null;
  const parts = from.split("/").slice(0, -1);
  for (const piece of href.split("#")[0]!.split("/")) {
    if (piece === "..") parts.pop();
    else if (piece && piece !== ".") parts.push(piece);
  }
  return parts.join("/") || null;
}

/**
 * A Markdown file as a person reads it. Identifiers the specification defines become
 * buttons that open where they are written; links to other files in the Intent open in
 * Studio; web links are shown, never followed inside the app window.
 */
export function MarkdownView({
  text,
  path,
  identifiers = [],
  onOpenId,
  onOpenPath,
  headingRef,
}: {
  text: string;
  path: string;
  identifiers?: SpecIdentifier[];
  onOpenId?(identifier: SpecIdentifier): void;
  onOpenPath?(path: string): void;
  /** Receives the rendered headings in order, so an outline can scroll to them. */
  headingRef?(headings: HTMLElement[]): void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const html = useMemo(() => DOMPurify.sanitize(md.render(text)), [text]);
  const known = useMemo(() => new Map(identifiers.map((item) => [item.id, item])), [identifiers]);

  useEffect(() => {
    const root = host.current;
    if (!root) return;
    root.innerHTML = html;
    // Wrap identifiers in text (never inside code or links) as buttons.
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: (node) =>
        node.parentElement?.closest("code, pre, a, button") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    const texts: Text[] = [];
    while (walker.nextNode()) texts.push(walker.currentNode as Text);
    for (const node of texts) {
      const value = node.nodeValue ?? "";
      if (!value.match(ID)) continue;
      const fragment = document.createDocumentFragment();
      let last = 0;
      for (const match of value.matchAll(ID)) {
        const item = known.get(match[0]);
        if (!item) continue;
        fragment.append(value.slice(last, match.index));
        const button = document.createElement("button");
        button.type = "button";
        button.className = "id-link";
        button.dataset.id = item.id;
        button.textContent = item.id;
        button.title = item.label || item.id;
        fragment.append(button);
        last = match.index! + match[0].length;
      }
      if (last === 0) continue;
      fragment.append(value.slice(last));
      node.replaceWith(fragment);
    }
    for (const link of root.querySelectorAll("a[href]")) {
      const href = link.getAttribute("href") ?? "";
      link.setAttribute("title", resolve(path, href) ? `Open ${resolve(path, href)}` : href);
    }
    headingRef?.([...root.querySelectorAll<HTMLElement>("h1, h2, h3, h4")]);
  }, [html, known, path, headingRef]);

  return (
    <div
      className="md-view"
      ref={host}
      onClick={(event) => {
        const target = event.target as HTMLElement;
        const id = target.closest<HTMLElement>("button.id-link")?.dataset.id;
        if (id) {
          const item = known.get(id);
          if (item) onOpenId?.(item);
          return;
        }
        const link = target.closest("a");
        if (!link) return;
        event.preventDefault(); // the app window never navigates away
        const inside = resolve(path, link.getAttribute("href") ?? "");
        if (inside) onOpenPath?.(inside);
      }}
    />
  );
}
