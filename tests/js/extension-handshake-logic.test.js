// The bridge extension's pure decisions (extension/jlc-bridge/handshake-logic.js):
// which handshake a pasted code starts, and how a DigiKey sign-in probe is read.
// The load-bearing property is that nothing short of a clean signed-in answer
// ends the DigiKey wait — a Cloudflare page or an error must never pass.
import { describe, expect, it } from "vitest";
import {
  classifyDigikeyProbe,
  describeRejection,
  isCloudflareInterstitial,
  routeForCode,
  toPushedCookie,
} from "../../extension/jlc-bridge/handshake-logic.js";

const ACCOUNT = "https://www.digikey.com/MyDigiKey/Account";

describe("routeForCode", () => {
  it("sends DK- codes to DigiKey", () => {
    expect(routeForCode("DK-3kf9abc")).toBe("digikey");
  });
  it("sends everything else to JLC, unchanged", () => {
    expect(routeForCode("3kf9abc")).toBe("jlc");
    expect(routeForCode("dk-3kf9")).toBe("jlc"); // the prefix is literal
    expect(routeForCode("xDK-3kf9")).toBe("jlc");
    expect(routeForCode("")).toBe("jlc");
    expect(routeForCode(undefined)).toBe("jlc");
  });
});

describe("isCloudflareInterstitial", () => {
  it("spots the challenge title and text", () => {
    expect(isCloudflareInterstitial("<html><title>Just a moment...</title></html>")).toBe(true);
    expect(isCloudflareInterstitial("<p>Performing security verification</p>")).toBe(true);
  });
  it("does not flag an ordinary page", () => {
    expect(isCloudflareInterstitial("<title>My DigiKey</title><p>Just a moment please</p>")).toBe(false);
    expect(isCloudflareInterstitial("")).toBe(false);
  });
});

describe("classifyDigikeyProbe", () => {
  const page = "<title>My DigiKey | Account</title>";

  it("signed in: 2xx at a non-login URL with a real page", () => {
    expect(classifyDigikeyProbe({ ok: true, status: 200, url: ACCOUNT, body: page }).state).toBe("signed_in");
  });

  it("signed out: redirected to a login or signin URL, any case", () => {
    for (const url of [
      "https://www.digikey.com/MyDigiKey/Login?ReturnUrl=%2FMyDigiKey%2FAccount",
      "https://auth.digikey.com/as/SignIn?x=1",
    ]) {
      expect(classifyDigikeyProbe({ ok: true, status: 200, url, body: page }).state).toBe("signed_out");
    }
  });

  it("signed out: the real redirect, an OAuth authorize URL on auth.digikey.com", () => {
    // Observed live on 2026-09-29. Neither "/login" nor "/signin" appears in
    // it, which is exactly the case the first version of this rule missed.
    const url =
      "https://auth.digikey.com/as/authorization.oauth2?response_type=code&client_id=pa_wam" +
      "&vnd_pi_requested_resource=https%3A%2F%2Fwww.digikey.com%2FMyDigiKey%2FAccount";
    expect(classifyDigikeyProbe({ ok: true, status: 200, url, body: "<title>Login</title>" }).state)
      .toBe("signed_out");
  });

  it("signed out: a 401 from the account page itself", () => {
    expect(classifyDigikeyProbe({ ok: false, status: 401, url: ACCOUNT, body: "" }).state).toBe("signed_out");
  });

  it("inconclusive: a Cloudflare interstitial, even with 200 at the account URL", () => {
    const body = "<html><title>Just a moment...</title></html>";
    expect(classifyDigikeyProbe({ ok: true, status: 200, url: ACCOUNT, body }).state).toBe("inconclusive");
    expect(classifyDigikeyProbe({ ok: false, status: 403, url: ACCOUNT, body }).state).toBe("inconclusive");
  });

  it("inconclusive: 403 / 5xx without a login redirect", () => {
    expect(classifyDigikeyProbe({ ok: false, status: 403, url: ACCOUNT, body: "" }).state).toBe("inconclusive");
    expect(classifyDigikeyProbe({ ok: false, status: 503, url: ACCOUNT, body: "" }).state).toBe("inconclusive");
  });

  it("inconclusive: no final URL to judge", () => {
    expect(classifyDigikeyProbe({ ok: true, status: 200, url: "", body: page }).state).toBe("inconclusive");
  });
});

describe("toPushedCookie", () => {
  it("keeps the field shape and drops expirationDate only when absent", () => {
    const base = { name: "a", value: "v", domain: ".digikey.com", path: "/", secure: 1, httpOnly: 0 };
    expect(toPushedCookie({ ...base, sameSite: "lax", storeId: "0" })).toEqual({
      name: "a", value: "v", domain: ".digikey.com", path: "/", secure: true, httpOnly: false,
    });
    expect(toPushedCookie({ ...base, expirationDate: 123 }).expirationDate).toBe(123);
  });
});

describe("describeRejection", () => {
  it("shows dubIS's own error sentence", () => {
    const text = JSON.stringify({ error: "Pairing code expired", code: "nonce_expired" });
    expect(describeRejection(401, text)).toBe(
      "dubIS rejected the DigiKey session (HTTP 401): Pairing code expired (nonce_expired)"
    );
  });
  it("falls back to the raw body when it is not the error shape", () => {
    expect(describeRejection(500, "boom")).toBe("dubIS rejected the DigiKey session (HTTP 500): boom");
    expect(describeRejection(502, "")).toBe("dubIS rejected the DigiKey session (HTTP 502)");
  });
});
