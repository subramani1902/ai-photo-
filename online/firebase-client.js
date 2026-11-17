// Browser authentication shared by account pages; guest photo search stays independent.
const SDK_VERSION = "13.4.0";

async function loadBrowserSdk() {
  const [app, auth] = await Promise.all([
    import(`https://www.gstatic.com/firebasejs/${SDK_VERSION}/firebase-app.js`),
    import(`https://www.gstatic.com/firebasejs/${SDK_VERSION}/firebase-auth.js`),
  ]);
  return { ...app, ...auth };
}

export function createFirebaseClient({ configuration, context = "client", loadSdk = loadBrowserSdk } = {}) {
  if (!["client", "admin"].includes(context)) throw new Error("Invalid Firebase authentication context.");
  let pending;
  async function ready() {
    if (!pending) {
      pending = (async () => {
        const config = configuration ?? (await import("./firebase-config.js")).default;
        if (config?.enabled !== true) throw new Error("Firebase authentication is not enabled.");
        const options = config.firebase;
        if (!options || !["apiKey", "projectId", "authDomain", "appId"].every(key => typeof options[key] === "string" && options[key])) {
          throw new Error("Firebase web-app configuration is incomplete.");
        }
        if (config.emulatorMode) {
          const url = new URL(config.authEmulatorUrl);
          if (!options.projectId.startsWith("demo-") || typeof location === "undefined"
              || !["localhost", "127.0.0.1", "[::1]"].includes(location.hostname)
              || !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
              || url.protocol !== "http:" || !url.port || url.username || url.password
              || url.pathname !== "/" || url.search || url.hash) throw new Error("Auth emulators are restricted to local demo projects.");
        } else if (options.projectId.startsWith("demo-") || config.authEmulatorUrl) throw new Error("Emulator configuration is forbidden in production mode.");
        const sdk = await loadSdk();
        const name = `photo-finder-${context}-${options.projectId}`;
        const existing = sdk.getApps().find(app => app.name === name);
        if (existing && ["apiKey", "projectId", "authDomain", "appId"].some(key => existing.options[key] !== options[key])) throw new Error("Firebase web configuration changed; reload the page.");
        const app = existing ?? sdk.initializeApp(options, name);
        const auth = sdk.getAuth(app);
        if (config.emulatorMode && !existing) sdk.connectAuthEmulator(auth, config.authEmulatorUrl, { disableWarnings: false });
        await sdk.setPersistence(auth, sdk.inMemoryPersistence);
        return { auth, sdk };
      })();
      pending.catch(() => { pending = undefined; });
    }
    return pending;
  }
  return {
    async signInWithCustomToken(token) {
      const { auth, sdk } = await ready();
      await sdk.signInWithCustomToken(auth, token);
    },
    async signInWithPassword(email, password) {
      const { auth, sdk } = await ready();
      await sdk.signInWithEmailAndPassword(auth, email, password);
    },
    async getCurrentUser() {
      const { auth } = await ready();
      if (typeof auth.authStateReady === "function") await auth.authStateReady();
      const user = auth.currentUser;
      return user ? Object.freeze({ uid: user.uid, email: user.email, emailVerified: user.emailVerified === true }) : null;
    },
    async sendPasswordReset(email) {
      const { auth, sdk } = await ready();
      await sdk.sendPasswordResetEmail(auth, email);
    },
    async getIdToken(forceRefresh = false) {
      const { auth } = await ready();
      if (!auth.currentUser) throw new Error("Sign in before requesting a Firebase ID token.");
      return auth.currentUser.getIdToken(forceRefresh);
    },
    async signOut() {
      const { auth, sdk } = await ready();
      await sdk.signOut(auth);
    },
  };
}
