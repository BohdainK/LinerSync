import { Link } from "react-router";

export function NotFoundPage() {
  return (
    <div className="flex flex-col gap-2">
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <p>
        <Link to="/" className="underline">
          Go to Home
        </Link>
      </p>
    </div>
  );
}
