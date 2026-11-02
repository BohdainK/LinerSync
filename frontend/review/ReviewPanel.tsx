// A drawer for walking every screen and leaving notes, optionally pinned to an element.
// Notes live in localStorage and export as Markdown.
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate } from "react-router";
import { GROUPS, SCREENS, screenFor } from "./screens";

type Kind = "issue" | "idea" | "praise";
type Target = { label: string; x: number; y: number };
type Note = { id: string; path: string; kind: Kind; text: string; target: Target | null; width: number; at: string };
type State = { notes: Note[]; reviewed: string[]; introSeen: boolean };

const STORE = "linersync-ui-review-v1";
const KINDS: { kind: Kind; label: string }[] = [
  { kind: "issue", label: "Issue" },
  { kind: "idea", label: "Idea" },
  { kind: "praise", label: "Works well" },
];

function load(): State {
  try {
    const raw = localStorage.getItem(STORE);
    if (raw) return JSON.parse(raw) as State;
  } catch {
    // Storage can be blocked in sandboxed viewers; notes then last for this visit only.
  }
  return { notes: [], reviewed: [], introSeen: false };
}

function save(state: State): void {
  try {
    localStorage.setItem(STORE, JSON.stringify(state));
  } catch {
    // See load().
  }
}

function describe(el: Element): string {
  const text = (s: string | null | undefined) => (s ?? "").replace(/\s+/g, " ").trim().slice(0, 60);
  const tag = el.tagName.toLowerCase();
  const role = el.getAttribute("role");
  // A container's text is everything inside it; call it an area and lean on its heading instead.
  const container = el.children.length > 2;
  const name =
    text(el.getAttribute("aria-label")) ||
    (container ? "" : text((el as HTMLElement).innerText)) ||
    text(el.getAttribute("placeholder"));
  const kind = role ?? (tag === "a" ? "link" : container ? "area" : tag);
  let section = "";
  const region = el.closest("section, [role=dialog], main, aside, form");
  const heading = region?.querySelector("h1, h2, h3, [role=heading]");
  if (heading && !heading.contains(el)) section = ` in “${text((heading as HTMLElement).innerText)}”`;
  return `${kind}${name ? ` “${name}”` : ""}${section}`;
}

function toMarkdown(notes: Note[], reviewed: string[]): string {
  const lines = [
    "# LinerSync UI review",
    "",
    `Screens reviewed: ${reviewed.length}/${SCREENS.length}. Notes: ${notes.length}.`,
    "",
  ];
  const paths = [...SCREENS.map((s) => s.path), ...notes.map((n) => n.path)];
  for (const path of [...new Set(paths)]) {
    const here = notes.filter((n) => n.path === path);
    if (here.length === 0) continue;
    const s = screenFor(path);
    lines.push(`## ${s.group} › ${s.label} (\`${path}\`)`, "");
    for (const n of here) {
      const where = n.target ? ` — on ${n.target.label}` : "";
      const narrow = n.width < 768 ? " _(phone width)_" : "";
      lines.push(`- **${KINDS.find((k) => k.kind === n.kind)?.label}**${where}${narrow}: ${n.text.replace(/\n/g, "\n  ")}`);
    }
    lines.push("");
  }
  const skipped = SCREENS.filter((s) => !reviewed.includes(s.path));
  if (skipped.length > 0) {
    lines.push("## Not reviewed yet", "", ...skipped.map((s) => `- ${s.group} › ${s.label}`), "");
  }
  return lines.join("\n");
}

function usePicker(active: boolean, onPick: (t: Target) => void, onCancel: () => void) {
  const [box, setBox] = useState<DOMRect | null>(null);
  useEffect(() => {
    if (!active) return;
    const outside = (e: Event) => !(e.target as Element).closest?.(".rv-root");
    const move = (e: MouseEvent) => {
      if (!outside(e)) return setBox(null);
      setBox((e.target as Element).getBoundingClientRect());
    };
    const click = (e: MouseEvent) => {
      if (!outside(e)) return;
      e.preventDefault();
      e.stopPropagation();
      onPick({ label: describe(e.target as Element), x: e.pageX, y: e.pageY });
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    document.addEventListener("mousemove", move, true);
    document.addEventListener("click", click, true);
    document.addEventListener("keydown", key, true);
    document.body.classList.add("rv-picking");
    return () => {
      document.removeEventListener("mousemove", move, true);
      document.removeEventListener("click", click, true);
      document.removeEventListener("keydown", key, true);
      document.body.classList.remove("rv-picking");
      setBox(null);
    };
  }, [active, onPick, onCancel]);
  return box;
}

export function ReviewPanel() {
  const location = useLocation();
  const navigate = useNavigate();
  const path = location.pathname;
  const [state, setState] = useState<State>(load);
  const [open, setOpen] = useState(true);
  const [tab, setTab] = useState<"screen" | "all" | "export">("screen");
  const [kind, setKind] = useState<Kind>("issue");
  const [text, setText] = useState("");
  const [target, setTarget] = useState<Target | null>(null);
  const [picking, setPicking] = useState(false);
  const [copied, setCopied] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => save(state), [state]);
  useEffect(() => {
    document.body.classList.toggle("rv-open", open);
  }, [open]);
  // A pin belongs to the screen it was made on.
  const [pinPath, setPinPath] = useState(path);
  if (pinPath !== path) {
    setPinPath(path);
    setTarget(null);
    setPicking(false);
  }

  const onPick = useMemo(
    () => (t: Target) => {
      setTarget(t);
      setPicking(false);
      setOpen(true);
      setTimeout(() => textRef.current?.focus(), 0);
    },
    [],
  );
  const onCancel = useMemo(() => () => setPicking(false), []);
  const box = usePicker(picking, onPick, onCancel);

  const index = SCREENS.findIndex((s) => s.path === path);
  const screen = screenFor(path);
  const notesHere = state.notes.filter((n) => n.path === path);
  const done = state.reviewed.includes(path);
  const markdown = useMemo(() => toMarkdown(state.notes, state.reviewed), [state]);

  const go = (to: string) => {
    navigate(to);
    window.scrollTo(0, 0);
  };
  const toggleReviewed = (p: string) =>
    setState((s) => ({
      ...s,
      reviewed: s.reviewed.includes(p) ? s.reviewed.filter((x) => x !== p) : [...s.reviewed, p],
    }));
  const addNote = () => {
    if (!text.trim()) return;
    const note: Note = {
      id: crypto.randomUUID(),
      path,
      kind,
      text: text.trim(),
      target,
      width: window.innerWidth,
      at: new Date().toISOString(),
    };
    setState((s) => ({ ...s, notes: [...s.notes, note] }));
    setText("");
    setTarget(null);
  };
  const removeNote = (id: string) => setState((s) => ({ ...s, notes: s.notes.filter((n) => n.id !== id) }));
  const nextScreen = () => {
    if (!done) toggleReviewed(path);
    const next = SCREENS[index + 1];
    if (next) go(next.path);
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(markdown);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setTab("export");
    }
  };
  const download = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([markdown], { type: "text/markdown" }));
    a.download = "linersync-ui-review.md";
    a.click();
  };

  const pinned = notesHere.filter((n) => n.target);
  const markers = createPortal(
    <>
      {pinned.map((n) => (
        <button
          key={n.id}
          type="button"
          className={`rv-root rv-marker rv-${n.kind}`}
          style={{ left: n.target!.x, top: n.target!.y }}
          title={n.text}
          onClick={() => {
            setOpen(true);
            setTab("screen");
          }}
        >
          {pinned.indexOf(n) + 1}
        </button>
      ))}
      {box && (
        <div
          className="rv-root rv-highlight"
          style={{ left: box.left, top: box.top, width: box.width, height: box.height }}
        />
      )}
    </>,
    document.body,
  );

  if (!open) {
    return (
      <>
        {markers}
        <button type="button" className="rv-root rv-fab" onClick={() => setOpen(true)}>
          Review · {state.reviewed.length}/{SCREENS.length}
        </button>
      </>
    );
  }

  return (
    <>
      {markers}
      {picking && <div className="rv-root rv-pickbar">Click the part of the page this note is about · Esc to cancel</div>}
      <aside className={`rv-root rv-panel${picking ? " rv-dim" : ""}`} aria-label="UI review">
        <header className="rv-head">
          <strong>UI review</strong>
          <span className="rv-muted">
            {state.reviewed.length}/{SCREENS.length} screens · {state.notes.length} notes
          </span>
          <button type="button" className="rv-icon" onClick={() => setOpen(false)} aria-label="Hide review panel">
            ✕
          </button>
        </header>
        <div className="rv-progress">
          <div style={{ width: `${(100 * state.reviewed.length) / SCREENS.length}%` }} />
        </div>
        <nav className="rv-tabs">
          {(
            [
              ["screen", "This screen"],
              ["all", "All screens"],
              ["export", "Send notes"],
            ] as const
          ).map(([id, label]) => (
            <button key={id} type="button" className={tab === id ? "rv-on" : ""} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </nav>

        <div className="rv-body">
          {!state.introSeen && (
            <div className="rv-intro">
              <p>
                <strong>This is the real LinerSync UI running on demo data.</strong> Nothing you click is saved.
              </p>
              <ol>
                <li>Use the app as normal. Step through screens with <em>Next screen</em>.</li>
                <li>Write a note per screen, or <em>Pin to element</em> to point at something.</li>
                <li>When done, open <em>Send notes</em> and copy them back to Claude.</li>
              </ol>
              <button type="button" className="rv-btn" onClick={() => setState((s) => ({ ...s, introSeen: true }))}>
                Got it
              </button>
            </div>
          )}

          {tab === "screen" && (
            <>
              <div className="rv-screen">
                <div>
                  <div className="rv-muted">{screen.group}</div>
                  <div className="rv-title">{screen.label}</div>
                </div>
                <label className="rv-check">
                  <input type="checkbox" checked={done} onChange={() => toggleReviewed(path)} /> Reviewed
                </label>
              </div>
              <div className="rv-row">
                <button
                  type="button"
                  className="rv-btn rv-ghost"
                  disabled={index <= 0}
                  onClick={() => go(SCREENS[index - 1]!.path)}
                >
                  ← Previous
                </button>
                <span className="rv-muted">{index >= 0 ? `${index + 1} of ${SCREENS.length}` : "off the list"}</span>
                <button
                  type="button"
                  className="rv-btn"
                  disabled={index >= SCREENS.length - 1}
                  onClick={nextScreen}
                >
                  Next screen →
                </button>
              </div>

              <div className="rv-compose">
                <div className="rv-kinds">
                  {KINDS.map((k) => (
                    <button
                      key={k.kind}
                      type="button"
                      className={`rv-chip rv-${k.kind}${kind === k.kind ? " rv-on" : ""}`}
                      onClick={() => setKind(k.kind)}
                    >
                      {k.label}
                    </button>
                  ))}
                </div>
                {target && (
                  <div className="rv-target">
                    📍 {target.label}
                    <button type="button" className="rv-icon" onClick={() => setTarget(null)} aria-label="Unpin">
                      ✕
                    </button>
                  </div>
                )}
                <textarea
                  ref={textRef}
                  value={text}
                  rows={4}
                  placeholder="What's confusing, missing, or nice here?"
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) addNote();
                  }}
                />
                <div className="rv-row">
                  <button type="button" className="rv-btn rv-ghost" onClick={() => setPicking(true)}>
                    📍 Pin to element
                  </button>
                  <button type="button" className="rv-btn" disabled={!text.trim()} onClick={addNote}>
                    Add note
                  </button>
                </div>
              </div>

              <ul className="rv-notes">
                {notesHere.map((n) => (
                  <li key={n.id} className={`rv-note rv-${n.kind}`}>
                    <div className="rv-note-head">
                      <span>
                        {n.target && <b>{pinned.indexOf(n) + 1} · </b>}
                        {KINDS.find((k) => k.kind === n.kind)?.label}
                      </span>
                      <button type="button" className="rv-icon" onClick={() => removeNote(n.id)} aria-label="Delete note">
                        ✕
                      </button>
                    </div>
                    {n.target && <div className="rv-muted">on {n.target.label}</div>}
                    <p>{n.text}</p>
                  </li>
                ))}
              </ul>
            </>
          )}

          {tab === "all" && (
            <div className="rv-all">
              {GROUPS.map((g) => {
                const list = SCREENS.filter((s) => s.group === g);
                if (list.length === 0) return null;
                return (
                  <section key={g}>
                    <h4>{g}</h4>
                    {list.map((s) => {
                      const count = state.notes.filter((n) => n.path === s.path).length;
                      return (
                        <div key={s.path} className={`rv-item${s.path === path ? " rv-here" : ""}`}>
                          <input
                            type="checkbox"
                            checked={state.reviewed.includes(s.path)}
                            onChange={() => toggleReviewed(s.path)}
                            aria-label={`Reviewed ${s.label}`}
                          />
                          <button type="button" onClick={() => go(s.path)}>
                            {s.label}
                          </button>
                          {count > 0 && <span className="rv-count">{count}</span>}
                        </div>
                      );
                    })}
                  </section>
                );
              })}
            </div>
          )}

          {tab === "export" && (
            <div className="rv-export">
              <p className="rv-muted">Copy this and paste it back into the Claude Code session.</p>
              <div className="rv-row">
                <button type="button" className="rv-btn" onClick={copy}>
                  {copied ? "Copied ✓" : "Copy as Markdown"}
                </button>
                <button type="button" className="rv-btn rv-ghost" onClick={download}>
                  Download .md
                </button>
              </div>
              <textarea readOnly value={markdown} rows={18} onFocus={(e) => e.target.select()} />
              <button
                type="button"
                className="rv-btn rv-danger"
                onClick={() => {
                  if (confirm("Delete all review notes and progress?")) setState({ notes: [], reviewed: [], introSeen: true });
                }}
              >
                Clear everything
              </button>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
