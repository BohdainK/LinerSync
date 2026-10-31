/** Axis ticks from 0 up past `max` in a 1/2/5 step, at most five of them. */
export function niceTicks(max: number): number[] {
  const top = Math.max(max, 1);
  let step = 1;
  for (let scale = 1; ; scale *= 10) {
    const found = [1, 2, 5].map((s) => s * scale).find((s) => Math.ceil(top / s) <= 4);
    if (found !== undefined) {
      step = found;
      break;
    }
  }
  const count = Math.ceil(top / step);
  return Array.from({ length: count + 1 }, (_, i) => i * step);
}
