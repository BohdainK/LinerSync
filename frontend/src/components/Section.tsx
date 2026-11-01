import { useId, type ReactNode } from "react";

/** A page section labelled by its h2. */
export function Section({ title, children }: { title: ReactNode; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="space-y-3">
      <h2 id={id} className="text-lg font-semibold">
        {title}
      </h2>
      {children}
    </section>
  );
}
