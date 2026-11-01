import { cn } from "@/lib/utils";

/** A service's limitation notes; renders nothing when there are none. */
export function NotesList({ notes, className }: { notes: string[]; className?: string }) {
  if (notes.length === 0) return null;
  return (
    <ul className={cn("list-disc space-y-1 text-sm text-muted-foreground", className)}>
      {notes.map((note) => (
        <li key={note}>{note}</li>
      ))}
    </ul>
  );
}
