import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router";
import type { ConnectionOut, SetupGuide, SetupStep } from "@/api/types";
import { Toaster } from "@/components/ui/sonner";
import { HIDE_TEXT, INSECURE_CONTEXT, SECRET_SAVED, SHOW_TEXT } from "@/copy";
import { connectionOut, setupGuide } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { SetupStepper } from "./SetupStepper";

function step(o: Partial<SetupStep> & Pick<SetupStep, "step_id" | "kind">): SetupStep {
  return { title: o.step_id, body: "", copy_values: [], fields: [], links: [], screenshots: [], ...o };
}

const infoStep = step({
  step_id: "app",
  kind: "info",
  title: "Create an app",
  body: "<b>x</b>\n\nThen come back here.",
  links: [
    { label: "Developer dashboard", url: "https://developer.example.com/" },
    { label: "Evil link", url: "javascript:alert(1)" },
  ],
  copy_values: [{ label: "Redirect URI", value: "http://127.0.0.1:8000/api/connections/oauth/callback" }],
  screenshots: [
    {
      resource: "shots/dashboard.png",
      caption: "The dashboard",
      annotations: [{ label: "Click here", x: 0.1, y: 0.2, width: 0.3, height: 0.25 }],
    },
  ],
});

const formStep = step({
  step_id: "keys",
  kind: "form",
  title: "Enter the app keys",
  fields: [
    {
      key: "client_id",
      label: "Client ID",
      kind: "text",
      help: "Shown on the app page",
      placeholder: "tok-abcd",
      pattern: "tok-[a-z]{4}",
      pattern_hint: "Starts with tok- and four letters",
      required: true,
    },
    {
      key: "client_secret",
      label: "Client secret",
      kind: "secret",
      help: "",
      placeholder: "",
      pattern_hint: "",
      required: true,
    },
    {
      key: "private_key",
      label: "Private key",
      kind: "multiline_secret",
      help: "",
      placeholder: "",
      pattern_hint: "",
      required: false,
    },
  ],
});

const oauthStep = step({ step_id: "auth", kind: "oauth", title: "Sign in", body: "Allow LinerSync access." });
const testStep = step({ step_id: "test", kind: "test", title: "Test the connection" });

const fullGuide = setupGuide({
  prerequisites: ["A Spotify Premium account"],
  steps: [infoStep, formStep, oauthStep, testStep],
});
const formOnlyGuide = setupGuide({ steps: [formStep, testStep] });

function LocationProbe() {
  const l = useLocation();
  return <output aria-label="location">{l.pathname + l.search}</output>;
}

type Handlers = {
  guide?: SetupGuide;
  conn?: ConnectionOut;
  service?: string;
};

let tests = 0;

function renderStepper({ guide = fullGuide, conn, service = "spotify" }: Handlers = {}, route?: string) {
  const info = conn ?? connectionOut({ service, display_name: guide.display_name, status: "not_set_up", account_name: null });
  server.use(
    http.get(`/api/connections/${service}/guide`, () => HttpResponse.json(guide)),
    http.get(`/api/connections/${service}`, () => HttpResponse.json(info)),
    http.get("/api/connections", () => HttpResponse.json({ services: [info], load_errors: [] })),
    http.post(`/api/connections/${service}/test`, () => {
      tests += 1;
      return HttpResponse.json({ ok: true, message: "Connected", account_name: "Sam" });
    }),
  );
  return renderWithProviders(
    <>
      <Routes>
        <Route path="/connections/:service" element={<SetupStepper service={service} />} />
      </Routes>
      <LocationProbe />
      <Toaster />
    </>,
    { route: route ?? `/connections/${service}` },
  );
}

function setSecure(value: boolean) {
  Object.defineProperty(window, "isSecureContext", { value, configurable: true });
}

beforeEach(() => {
  tests = 0;
  setSecure(true);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as { MusicKit?: unknown }).MusicKit;
});

describe("SetupStepper", () => {
  it("renders each step kind", async () => {
    renderStepper();
    const prereq = await screen.findByRole("region", { name: "Before you start" });
    expect(within(prereq).getByText("A Spotify Premium account")).toBeInTheDocument();

    const items = screen.getAllByRole("listitem").filter((li) => li.dataset["step"]);
    expect(items).toHaveLength(4);
    expect(items[0]).toHaveAttribute("aria-current", "step");
    expect(items[1]).not.toHaveAttribute("aria-current");

    expect(screen.getByText("Create an app")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Developer dashboard" })).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("img", { name: "The dashboard" })).toHaveAttribute(
      "src",
      "/api/connections/spotify/resources/shots/dashboard.png",
    );
    expect(screen.getByRole("button", { name: "I've done this" })).toBeInTheDocument();

    expect(screen.getByRole("textbox", { name: "Client ID" })).toHaveAttribute("placeholder", "tok-abcd");
    expect(screen.getByRole("textbox", { name: "Client ID" })).toHaveAccessibleDescription("Shown on the app page");
    expect(screen.getByLabelText("Client secret")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("Private key").tagName).toBe("TEXTAREA");
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();

    expect(screen.getByText("Allow LinerSync access.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in to Spotify" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeInTheDocument();
  });

  it("acknowledging an info step moves on", async () => {
    renderStepper();
    await userEvent.click(await screen.findByRole("button", { name: "I've done this" }));
    const items = screen.getAllByRole("listitem").filter((li) => li.dataset["step"]);
    expect(items[1]).toHaveAttribute("aria-current", "step");
    expect(within(items[0]!).getByText("Done")).toBeInTheDocument();
  });

  it("prefills text fields and marks saved secrets", async () => {
    renderStepper({
      guide: formOnlyGuide,
      conn: connectionOut({ status: "untested", values: { client_id: "tok-abcd" }, secrets_set: ["private_key"] }),
    });
    expect(await screen.findByRole("textbox", { name: "Client ID" })).toHaveValue("tok-abcd");
    expect(screen.getByLabelText("Private key")).toHaveAttribute("placeholder", SECRET_SAVED);
    expect(screen.getByLabelText("Private key")).toHaveValue("");
    expect(screen.getByLabelText("Client secret")).not.toHaveAttribute("placeholder", SECRET_SAVED);
  });

  it("validates as you type", async () => {
    renderStepper({ guide: formOnlyGuide });
    const input = await screen.findByRole("textbox", { name: "Client ID" });
    await userEvent.type(input, "abc");
    expect(screen.getByText("Starts with tok- and four letters")).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-invalid", "true");
    await userEvent.clear(input);
    await userEvent.type(input, "tok-abcd");
    expect(screen.queryByText("Starts with tok- and four letters")).not.toBeInTheDocument();
    expect(input).not.toHaveAttribute("aria-invalid", "true");
  });

  it("saves the form and runs the test when no sign-in step remains", async () => {
    let body: unknown;
    server.use(
      http.put("/api/connections/spotify/credentials", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(connectionOut({ status: "untested", values: { client_id: "tok-abcd" }, secrets_set: ["client_secret"] }));
      }),
    );
    renderStepper({ guide: formOnlyGuide });
    await userEvent.type(await screen.findByRole("textbox", { name: "Client ID" }), "tok-abcd");
    await userEvent.type(screen.getByLabelText("Client secret"), "shh");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Connected/)).toBeInTheDocument();
    expect(screen.getByText(/Sam/)).toBeInTheDocument();
    expect(body).toEqual({ values: { client_id: "tok-abcd", client_secret: "shh", private_key: "" } });
    expect(tests).toBe(1);
  });

  it("does not auto-test while a sign-in step is pending", async () => {
    let saved = false;
    server.use(
      http.put("/api/connections/spotify/credentials", () => {
        saved = true;
        return HttpResponse.json(connectionOut({ status: "untested", values: { client_id: "tok-abcd" }, secrets_set: ["client_secret"] }));
      }),
    );
    renderStepper();
    await userEvent.type(await screen.findByRole("textbox", { name: "Client ID" }), "tok-abcd");
    await userEvent.type(screen.getByLabelText("Client secret"), "shh");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saved).toBe(true));
    expect(await screen.findByRole("button", { name: "Show step: Enter the app keys" })).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 20));
    expect(tests).toBe(0);
  });

  it("shows field problems from the server", async () => {
    server.use(
      http.put("/api/connections/spotify/credentials", () =>
        HttpResponse.json({ detail: { problems: { client_id: "Starts with tok-" } } }, { status: 422 }),
      ),
    );
    renderStepper({ guide: formOnlyGuide });
    const input = await screen.findByRole("textbox", { name: "Client ID" });
    await userEvent.type(input, "tok-abcd");
    await userEvent.type(screen.getByLabelText("Client secret"), "shh");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Starts with tok-")).toBeInTheDocument();
    expect(input).toHaveAccessibleDescription(expect.stringContaining("Starts with tok-"));
    expect(tests).toBe(0);
  });

  it("starts OAuth by navigating to the returned url", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign, origin: window.location.origin });
    server.use(
      http.post("/api/connections/spotify/oauth/start", () =>
        HttpResponse.json({ url: "https://accounts.example.com/authorize?x=1" }),
      ),
    );
    renderStepper();
    await userEvent.click(await screen.findByRole("button", { name: "Sign in to Spotify" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://accounts.example.com/authorize?x=1"));
  });

  it("shows a 409 from OAuth start", async () => {
    server.use(
      http.post("/api/connections/spotify/oauth/start", () =>
        HttpResponse.json({ detail: "Save the app keys first" }, { status: 409 }),
      ),
    );
    renderStepper();
    await userEvent.click(await screen.findByRole("button", { name: "Sign in to Spotify" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Save the app keys first");
  });

  it("signed_in=1 marks sign-in done, tests and cleans the URL", async () => {
    renderStepper({}, "/connections/spotify?signed_in=1&tab=x");
    expect(await screen.findByText("Signed in to Spotify")).toBeInTheDocument();
    await waitFor(() => expect(tests).toBe(1));
    await waitFor(() => expect(screen.getByLabelText("location")).toHaveTextContent(/^\/connections\/spotify\?tab=x$/));
    const items = screen.getAllByRole("listitem").filter((li) => li.dataset["step"]);
    expect(within(items[2]!).getByText("Done")).toBeInTheDocument();
  });

  it("sign_in_error is shown on the oauth step", async () => {
    renderStepper({}, `/connections/spotify?sign_in_error=${encodeURIComponent("Access was denied")}`);
    await screen.findByRole("button", { name: "Sign in to Spotify" });
    const items = screen.getAllByRole("listitem").filter((li) => li.dataset["step"]);
    expect(within(items[2]!).getByRole("alert")).toHaveTextContent("Access was denied");
    await waitFor(() => expect(screen.getByLabelText("location")).toHaveTextContent(/^\/connections\/spotify$/));
    expect(tests).toBe(0);
  });

  it("browser sign-in without a handler shows NO_SIGN_IN_HANDLER", async () => {
    renderStepper({
      service: "fakemusic",
      guide: setupGuide({
        service: "fakemusic",
        display_name: "Fake Music",
        steps: [step({ step_id: "signin", kind: "browser_sign_in", title: "Sign in" }), testStep],
      }),
    });
    expect(await screen.findByText("This version of LinerSync can't sign in to Fake Music from the browser")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in to Fake Music" })).toBeDisabled();
  });

  it("browser sign-in is disabled in an insecure context", async () => {
    setSecure(false);
    renderStepper({
      service: "apple_music",
      guide: setupGuide({
        service: "apple_music",
        display_name: "Apple Music",
        steps: [step({ step_id: "signin", kind: "browser_sign_in", title: "Sign in" }), testStep],
      }),
    });
    expect(await screen.findByText(INSECURE_CONTEXT)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in to Apple Music" })).toBeDisabled();
  });

  it("browser sign-in posts the result and runs the test", async () => {
    const configure = vi.fn(() => Promise.resolve({ authorize: () => Promise.resolve("mut") }));
    (window as { MusicKit?: unknown }).MusicKit = { configure };
    let posted: unknown;
    server.use(
      http.get("/api/connections/apple_music/browser-sign-in", () =>
        HttpResponse.json({ config: { developer_token: "dev", app_name: "LinerSync", app_build: "1.0" } }),
      ),
      http.post("/api/connections/apple_music/browser-sign-in", async ({ request }) => {
        posted = await request.json();
        return HttpResponse.json(connectionOut({ service: "apple_music", display_name: "Apple Music", status: "untested" }));
      }),
    );
    renderStepper({
      service: "apple_music",
      guide: setupGuide({
        service: "apple_music",
        display_name: "Apple Music",
        steps: [step({ step_id: "signin", kind: "browser_sign_in", title: "Sign in" }), testStep],
      }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "Sign in to Apple Music" }));
    await waitFor(() => expect(tests).toBe(1));
    expect(posted).toEqual({ result: { music_user_token: "mut" } });
    expect(configure).toHaveBeenCalledWith({ developerToken: "dev", app: { name: "LinerSync", build: "1.0" } });
  });

  describe("device code", () => {
    const deviceGuide = setupGuide({
      service: "youtube_music",
      display_name: "YouTube Music",
      steps: [step({ step_id: "signin", kind: "device_code", title: "Sign in", body: "Enter the code." }), testStep],
    });

    function deviceServer(polls: string[]) {
      let starts = 0;
      server.use(
        http.post("/api/connections/youtube_music/device-code/start", () => {
          starts += 1;
          return HttpResponse.json({
            user_code: `ABCD-EFG${starts}`,
            verification_url: "https://www.google.com/device",
            interval_s: 0,
            expires_in_s: 600,
          });
        }),
        http.post("/api/connections/youtube_music/device-code/poll", () => {
          const status = polls.length > 1 ? polls.shift() : polls[0];
          const message = status === "expired" ? "This code expired. Get a new one." : "";
          return HttpResponse.json({ status, message });
        }),
      );
    }

    it("shows the code and link, then runs the test once approved", async () => {
      deviceServer(["pending", "pending", "done"]);
      renderStepper({ service: "youtube_music", guide: deviceGuide });
      await userEvent.click(await screen.findByRole("button", { name: "Get a code" }));
      expect(await screen.findByText("ABCD-EFG1")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: /google\.com\/device/ })).toHaveAttribute(
        "href",
        "https://www.google.com/device",
      );
      await waitFor(() => expect(tests).toBe(1));
    });

    it("an expired code shows the message and offers a new one", async () => {
      deviceServer(["expired"]);
      renderStepper({ service: "youtube_music", guide: deviceGuide });
      await userEvent.click(await screen.findByRole("button", { name: "Get a code" }));
      expect(await screen.findByText("This code expired. Get a new one.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Get a code" })).toBeEnabled();
      expect(tests).toBe(0);
    });

    it("a failed poll doesn't end the flow", async () => {
      deviceServer(["pending", "done"]);
      let failures = 1;
      server.use(
        http.post("/api/connections/youtube_music/device-code/poll", () => {
          if (failures > 0) {
            failures -= 1;
            return HttpResponse.json({ detail: "Bad gateway" }, { status: 502 });
          }
          return undefined;
        }),
      );
      renderStepper({ service: "youtube_music", guide: deviceGuide });
      await userEvent.click(await screen.findByRole("button", { name: "Get a code" }));
      await waitFor(() => expect(tests).toBe(1));
    });

    it("polls that keep failing end the flow", async () => {
      deviceServer(["pending"]);
      server.use(
        http.post("/api/connections/youtube_music/device-code/poll", () =>
          HttpResponse.json({ detail: "Bad gateway" }, { status: 502 }),
        ),
      );
      renderStepper({ service: "youtube_music", guide: deviceGuide });
      await userEvent.click(await screen.findByRole("button", { name: "Get a code" }));
      expect(await screen.findByRole("alert")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Get a code" })).toBeEnabled();
    });

    it("slows down when the server asks", async () => {
      deviceServer(["pending"]);
      let polls = 0;
      server.use(
        http.post("/api/connections/youtube_music/device-code/poll", () => {
          polls += 1;
          return HttpResponse.json({ status: "pending", message: "", interval_s: 3600 });
        }),
      );
      renderStepper({ service: "youtube_music", guide: deviceGuide });
      await userEvent.click(await screen.findByRole("button", { name: "Get a code" }));
      await waitFor(() => expect(polls).toBe(1));
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(polls).toBe(1);
    });
  });

  it("text version lists every step and has no inputs", async () => {
    renderStepper();
    await userEvent.click(await screen.findByRole("button", { name: SHOW_TEXT }));
    const text = screen.getByRole("region", { name: "All steps as text" });
    const steps = within(text).getAllByRole("listitem").filter((li) => li.dataset["textStep"]);
    expect(steps).toHaveLength(4);
    expect(within(text).getByText("Create an app")).toBeInTheDocument();
    expect(within(text).getByText("Redirect URI: http://127.0.0.1:8000/api/connections/oauth/callback")).toBeInTheDocument();
    expect(within(text).getByText("Client ID")).toBeInTheDocument();
    expect(within(text).getByRole("link", { name: "Developer dashboard" })).toBeInTheDocument();
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual([HIDE_TEXT]);
    await userEvent.click(screen.getByRole("button", { name: HIDE_TEXT }));
    expect(screen.getByRole("textbox", { name: "Client ID" })).toBeInTheDocument();
  });

  it("renders guide text and unsafe links as plain text", async () => {
    const { container } = renderStepper();
    expect(await screen.findByText("<b>x</b>")).toBeInTheDocument();
    expect(screen.getByText("Then come back here.").tagName).toBe("P");
    expect(container.querySelector("b")).toBeNull();
    const evil = screen.getByText("Evil link");
    expect(evil.closest("a")).toBeNull();
    expect(screen.queryByRole("link", { name: "Evil link" })).not.toBeInTheDocument();
    expect(container.querySelector('[href^="javascript"]')).toBeNull();
  });

  it("copy button copies the redirect URI", async () => {
    const user = userEvent.setup();
    renderStepper();
    expect(await screen.findByRole("textbox", { name: "Redirect URI" })).toHaveAttribute("readonly");
    await user.click(screen.getByRole("button", { name: "Copy Redirect URI" }));
    await expect(navigator.clipboard.readText()).resolves.toBe(
      "http://127.0.0.1:8000/api/connections/oauth/callback",
    );
  });

  it("annotations are placed by fraction", async () => {
    renderStepper();
    const label = await screen.findByText("Click here");
    const box = label.closest("[data-annotation]");
    expect(box).toHaveStyle({ left: "10%", top: "20%", width: "30%", height: "25%" });
  });

  it("collapses done steps when connected and can expand them", async () => {
    renderStepper({ guide: formOnlyGuide, conn: connectionOut({ status: "connected" }) });
    const expand = await screen.findByRole("button", { name: "Show step: Enter the app keys" });
    expect(screen.queryByRole("textbox", { name: "Client ID" })).not.toBeInTheDocument();
    await userEvent.click(expand);
    expect(screen.getByRole("textbox", { name: "Client ID" })).toBeInTheDocument();
  });
});
