/** D7: browser sign-in handlers, keyed by service id. */
export type SignInHandler = (config: Record<string, string>) => Promise<Record<string, string>>;

export type MusicKitConfig = { developerToken: string; app: { name: string; build: string } };
export type MusicKitInstance = { authorize(): Promise<string> };
export type MusicKitGlobal = {
  configure(config: MusicKitConfig): MusicKitInstance | Promise<MusicKitInstance>;
};

export const MUSICKIT_URL = "https://js-cdn.music.apple.com/musickit/v3/musickit.js";

type WithMusicKit = { MusicKit?: MusicKitGlobal };

let loading: Promise<MusicKitGlobal> | null = null;

function loadMusicKit(): Promise<MusicKitGlobal> {
  const w = window as Window & WithMusicKit;
  if (w.MusicKit) return Promise.resolve(w.MusicKit);
  loading ??= new Promise<MusicKitGlobal>((resolve, reject) => {
    document.addEventListener(
      "musickitloaded",
      () => (w.MusicKit ? resolve(w.MusicKit) : reject(new Error("Apple's MusicKit didn't start"))),
      { once: true },
    );
    const script = document.createElement("script");
    script.src = MUSICKIT_URL;
    script.async = true;
    script.onerror = () => {
      script.remove();
      reject(new Error("Couldn't load Apple's MusicKit. Check your internet connection."));
    };
    document.head.appendChild(script);
  }).catch((e: unknown) => {
    loading = null;
    throw e;
  });
  return loading;
}

export async function musicKitSignIn(
  config: Record<string, string>,
  load: () => Promise<MusicKitGlobal> = loadMusicKit,
): Promise<Record<string, string>> {
  const kit = await load();
  const instance = await kit.configure({
    developerToken: config["developer_token"] ?? "",
    app: { name: config["app_name"] ?? "LinerSync", build: config["app_build"] ?? "" },
  });
  const token = await instance.authorize();
  if (!token) throw new Error("Apple Music sign-in was cancelled");
  return { music_user_token: token };
}

export const signInHandlers: Record<string, SignInHandler> = {
  apple_music: (config) => musicKitSignIn(config),
};
