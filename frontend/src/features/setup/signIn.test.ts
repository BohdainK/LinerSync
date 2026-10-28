import { describe, expect, it, vi } from "vitest";
import { musicKitSignIn, signInHandlers, type MusicKitGlobal } from "./signIn";

const config = { developer_token: "dev", app_name: "LinerSync", app_build: "1.0" };

describe("musicKitSignIn", () => {
  it("configures and returns the user token", async () => {
    const authorize = vi.fn(() => Promise.resolve("mut"));
    const configure = vi.fn(() => Promise.resolve({ authorize }));
    const kit: MusicKitGlobal = { configure };
    await expect(musicKitSignIn(config, () => Promise.resolve(kit))).resolves.toEqual({ music_user_token: "mut" });
    expect(configure).toHaveBeenCalledWith({ developerToken: "dev", app: { name: "LinerSync", build: "1.0" } });
    expect(authorize).toHaveBeenCalledOnce();
  });

  it("rejects when authorize fails", async () => {
    const kit: MusicKitGlobal = {
      configure: () => Promise.resolve({ authorize: () => Promise.reject(new Error("AUTHORIZATION_ERROR")) }),
    };
    await expect(musicKitSignIn(config, () => Promise.resolve(kit))).rejects.toThrow("AUTHORIZATION_ERROR");
  });

  it("is registered for apple_music", () => {
    expect(signInHandlers["apple_music"]).toBeDefined();
  });
});
