/**
 * Studio's icons: one 24-unit grid, 1.75 stroke, round joins, no fills except the gate
 * diamond. Drawn here rather than imported so the native bundle carries only what it uses.
 * Icons are decorative: every one sits next to a text label or inside a labelled button.
 */
const PATHS = {
  inbox: "M4 13l2.2-7.1A2 2 0 0 1 8.1 4.5h7.8a2 2 0 0 1 1.9 1.4L20 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2zM4 13h4.5l1.2 2.5h4.6l1.2-2.5H20",
  cockpit: "M12 13l3.5-3.5M4.2 17a8.5 8.5 0 1 1 15.6 0M12 13.2a.2.2 0 1 0 0-.4.2.2 0 0 0 0 .4",
  learn: "M12 6.5c-2-1.4-4.6-2-7.5-2v13c2.9 0 5.5.6 7.5 2m0-13c2-1.4 4.6-2 7.5-2v13c-2.9 0-5.5.6-7.5 2m0-13v13",
  story: "M8 5.5v13l10-6.5z",
  doctor: "M3.5 12h4l2-5 4 10 2-5h5",
  repos: "M3.5 7a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z",
  plus: "M12 5v14M5 12h14",
  search: "M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM20 20l-4.8-4.8",
  refresh: "M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v4h-4",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 13.5l1.6 1.2-2 3.4-1.9-.7a7 7 0 0 1-1.8 1l-.3 2h-4l-.3-2a7 7 0 0 1-1.8-1l-1.9.7-2-3.4 1.6-1.2a7 7 0 0 1 0-2l-1.6-1.2 2-3.4 1.9.7a7 7 0 0 1 1.8-1l.3-2h4l.3 2a7 7 0 0 1 1.8 1l1.9-.7 2 3.4-1.6 1.2a7 7 0 0 1 0 2z",
  chevronRight: "M9.5 6l6 6-6 6",
  collapse: "M4 5.5h16v13H4zM9.5 5.5v13M15.5 10l-2 2 2 2",
  expand: "M4 5.5h16v13H4zM9.5 5.5v13M13.5 10l2 2-2 2",
  file: "M13.5 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9zM13.5 3.5V9H19",
  lock: "M7 10.5V8a5 5 0 0 1 10 0v2.5M5.5 10.5h13v9.5h-13z",
  check: "M5 12.5l4.5 4.5L19 7.5",
  alert: "M12 4l9 15.5H3zM12 10v4M12 17v.5",
  close: "M6 6l12 12M18 6L6 18",
  keyboard: "M3.5 6.5h17v11h-17zM7 10h1M11 10h1M15 10h1M7 13.5h10",
  sun: "M12 16.5a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9zM12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4",
  intent: "M5 4.5h14v15H5zM8.5 9h7M8.5 12.5h7M8.5 16h4",
  command: "M8.5 5a2 2 0 1 0 0 4h7a2 2 0 1 0 0-4 2 2 0 0 0-2 2v10a2 2 0 1 0 2-2h-7a2 2 0 1 0 0 4 2 2 0 0 0 2-2V7a2 2 0 0 0-2-2z",
  arrowRight: "M5 12h13M13 7l5 5-5 5",
  codebase: "M4 5.5h6v5H4zM14 13.5h6v5h-6zM4 14.5h6v4H4zM7 10.5v4M10 16.5h4M14 5.5h6v4h-6zM17 9.5v4",
  copy: "M8.5 8.5h11v11h-11zM15.5 8.5v-4h-11v11h4",
  portfolio: "M4 6h3M4 12h3M4 18h3M10 6h10M10 12h7M10 18h4",
  pipeline: "M4 7h4l2 5-2 5H4M12 12h8M16 8l4 4-4 4",
} as const;

export type IconName = keyof typeof PATHS | "gate";

export function Icon({ name, size = 16, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      className={`icon${className ? ` ${className}` : ""}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {name === "gate" ? <path d="M12 3l9 9-9 9-9-9z" fill="currentColor" stroke="none" /> : <path d={PATHS[name]} />}
    </svg>
  );
}
