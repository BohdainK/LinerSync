type Listener = (event: MessageEvent<string>) => void;

/** A controllable stand-in for EventSource. Tests drive it with open/emit/fail. */
export class FakeEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  static last: FakeEventSource | null = null;
  static instances: FakeEventSource[] = [];

  url: string;
  readyState = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private listeners = new Map<string, Set<Listener>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.last = this;
    FakeEventSource.instances.push(this);
  }

  static reset(): void {
    FakeEventSource.last = null;
    FakeEventSource.instances = [];
  }

  addEventListener(type: string, fn: Listener): void {
    const set = this.listeners.get(type) ?? new Set<Listener>();
    set.add(fn);
    this.listeners.set(type, set);
  }

  removeEventListener(type: string, fn: Listener): void {
    this.listeners.get(type)?.delete(fn);
  }

  open(): void {
    this.readyState = 1;
    this.onopen?.();
  }

  emit(type: "job" | "resync", data: unknown): void {
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    this.listeners.get(type)?.forEach((fn) => fn(event));
  }

  /** The connection dropped. By default the browser retries (CONNECTING); `closed` means it gave up. */
  fail(opts: { closed?: boolean } = {}): void {
    this.readyState = opts.closed ? 2 : 0;
    this.onerror?.();
  }

  close(): void {
    this.readyState = 2;
  }
}
