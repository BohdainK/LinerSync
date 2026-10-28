import type { SetupGuide } from "@/api/types";
import { LinkList, Paragraphs } from "./steps/InfoStep";

/** The whole guide as one readable page: no inputs, no buttons. */
export function StepsAsText({ guide }: { guide: SetupGuide }) {
  return (
    <section aria-label="All steps as text" className="text-sm">
      <ol className="flex list-decimal flex-col gap-4 pl-5">
        {guide.steps.map((s) => (
          <li key={s.step_id} data-text-step={s.step_id} className="flex flex-col gap-2">
            <h3 className="font-medium">{s.title}</h3>
            <Paragraphs text={s.body} />
            <LinkList links={s.links} />
            {s.copy_values.length > 0 ? (
              <ul className="list-disc pl-5">
                {s.copy_values.map((c, i) => (
                  <li key={i} className="font-mono break-all">{`${c.label}: ${c.value}`}</li>
                ))}
              </ul>
            ) : null}
            {s.fields.length > 0 ? (
              <ul className="list-disc pl-5">
                {s.fields.map((f) => (
                  <li key={f.key}>{f.label}</li>
                ))}
              </ul>
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}
