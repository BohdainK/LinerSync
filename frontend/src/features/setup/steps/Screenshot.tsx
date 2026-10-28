import type { Screenshot as ScreenshotData } from "@/api/types";

const pct = (v: number) => `${Number((v * 100).toFixed(4))}%`;

function resourceUrl(service: string, resource: string): string {
  const path = resource.split("/").map(encodeURIComponent).join("/");
  return `/api/connections/${encodeURIComponent(service)}/resources/${path}`;
}

/** A guide screenshot with outlined boxes placed by fractions of the image size. */
export function Screenshot({ service, shot }: { service: string; shot: ScreenshotData }) {
  return (
    <figure className="flex max-w-2xl flex-col gap-1">
      <div className="relative">
        <img src={resourceUrl(service, shot.resource)} alt={shot.caption} className="block w-full rounded-md border" />
        {shot.annotations.map((a, i) => (
          <div
            key={i}
            data-annotation
            className="pointer-events-none absolute rounded-sm border-2 border-red-600"
            style={{ left: pct(a.x), top: pct(a.y), width: pct(a.width), height: pct(a.height) }}
          >
            <span className="absolute bottom-full left-0 mb-0.5 rounded-sm bg-red-600 px-1 text-xs whitespace-nowrap text-white">
              {a.label}
            </span>
          </div>
        ))}
      </div>
      <figcaption className="text-sm text-muted-foreground">{shot.caption}</figcaption>
    </figure>
  );
}
