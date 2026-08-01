# System Architecture

How this repository is put together and how a Meta Wearables Device Access Toolkit (DAT)
app behaves at runtime. Companion docs: [`product.md`](product.md) (what this is for) and
[`design.md`](design.md) (interface and interaction design).

Version described: **0.8.0** (see [`CHANGELOG.md`](../CHANGELOG.md)).

---

## 1. What this repository is

This repo is a **distribution and reference surface**, not the SDK source tree. It carries
four separable systems:

| System | Location | Consumed by |
|--------|----------|-------------|
| SDK distribution | Git tags resolved over Swift Package Manager | Xcode projects |
| Reference apps | `samples/AssemblyMan`, `samples/DisplayAccess` | Developers reading real integration code |
| AI knowledge base | `plugins/mwdat-ios/skills/` → five published surfaces | Claude Code, Codex, Copilot, Cursor, AGENTS.md readers |
| Installer | `install-skills.sh` | Anyone wiring the knowledge base into their own project |

The Swift sources for `MWDATCore`/`MWDATCamera`/`MWDATDisplay`/`MWDATMockDevice` are not in
the tree; the working copy contains no `Package.swift` or `.xcframework`. Consumers resolve
a tagged release from `https://github.com/facebook/meta-wearables-dat-ios`, which is how the
sample apps themselves depend on it — `samples/AssemblyMan/.../Package.resolved` pins
version `0.8.0` at revision `2e30f125`.

Per [`CONTRIBUTING.md`](../CONTRIBUTING.md), the GitHub repo is **generated from an internal
Meta repository**. That explains the shape of the history: one squashed commit per release
(`Release 0.8.0`, `Release 0.7.0`, …) rather than incremental development commits. Treat the
repo as an export — history is not a review trail, and PRs land internally before reflecting
back out.

---

## 2. Runtime architecture of a DAT app

Four processes/devices participate. The app never talks to the glasses directly.

```
┌─────────────────────────┐        ┌──────────────────────┐
│  Your iOS app           │        │  Meta AI companion   │
│                         │        │  app (same phone)    │
│  MWDATCore              │◀──────▶│                      │
│  MWDATCamera            │  URL   │  - registration UI   │
│  MWDATDisplay           │ scheme │  - permission grants │
│  MWDATMockDevice(DEBUG) │ round  │  - Developer Mode    │
└───────────┬─────────────┘  trip  │  - firmware updates  │
            │                      └──────────┬───────────┘
            │  device sessions,               │
            │  frames, display content        │  BLE / Wi-Fi
            ▼                                 ▼
        ┌───────────────────────────────────────────┐
        │  Meta AI glasses (Ray-Ban Meta, Oakley     │
        │  Meta, Meta Ray-Ban Display, Meta Glasses) │
        └───────────────────────────────────────────┘
```

### 2.1 Module boundaries

- **MWDATCore** — `Wearables` entry point, device discovery, registration, permissions,
  device selectors, `DeviceSession`.
- **MWDATCamera** — `Stream`, `VideoFrame`, `PhotoData`, `StreamConfiguration`.
- **MWDATDisplay** — `Display` capability plus a declarative view DSL (`FlexBox`, `Text`,
  `Button`, `Image`, `Icon`, `VideoPlayer`).
- **MWDATMockDevice** — `MockDeviceKit`, simulated glasses, and an in-process test server.
  Debug-only in the samples.

### 2.2 Startup and the registration round trip

Registration is an out-of-process handshake that leaves and re-enters the app via a custom
URL scheme:

1. `try Wearables.configure()` in the `App` initializer
   (`samples/AssemblyMan/AssemblyMan/AssemblyManApp.swift:37`). Configuration reads the
   `MWDAT` dictionary from `Info.plist`; failures are logged, not fatal.
2. `Wearables.shared.startRegistration()` hands off to the Meta AI app.
3. Meta AI returns control by opening `<scheme>://…?metaWearablesAction=…`.
4. An always-mounted, invisible `RegistrationView` catches it with `.onOpenURL`, filters on
   the `metaWearablesAction` query item, and forwards to
   `Wearables.shared.handleUrl(url)` (`samples/AssemblyMan/AssemblyMan/Views/RegistrationView.swift:27`).
5. `registrationStateStream()` emits the new `RegistrationState`; the UI switches on it.

The URL handler must be mounted independently of whatever screen is showing — both samples
place `RegistrationView` as a sibling of the main view inside `WindowGroup`, not nested in
the navigation hierarchy. Miss this and registration silently never completes.

### 2.3 Session and capability model

```
Wearables.shared
  └─ createSession(deviceSelector:)  →  DeviceSession   (1 per device)
                                          ├─ addStream(config:)  → Stream    (MWDATCamera)
                                          └─ addDisplay(…)       → Display   (MWDATDisplay)
```

- Device choice is delegated to a **selector**: `AutoDeviceSelector` (optionally filtered,
  e.g. `filter: { $0.supportsDisplay() }`) or `SpecificDeviceSelector`.
- Capabilities are attached to a **started** session, and are managed through it. As of
  0.8.0 there is no `Capability` protocol and no `addCapability(_:)`; `Stream.start()/stop()`
  and `Display.start()/stop()` are synchronous.
- `DeviceSessionState.stopped` is **terminal** — a stopped session is discarded and a new one
  created, never restarted (`DeviceSessionManager.startStateObserver`,
  `samples/AssemblyMan/AssemblyMan/ViewModels/DeviceSessionManager.swift:175`).

### 2.4 Observation

Two idioms coexist and both appear in the samples:

- `AsyncStream` / `for await` — `registrationStateStream()`, `devicesStream()`,
  `session.stateStream()`, `session.errorStream()`, `deviceSelector.activeDeviceStream()`.
- Publisher `.listen { }` returning an `AnyListenerToken` — `statePublisher`,
  `videoFramePublisher`, `errorPublisher`, `photoDataPublisher`,
  `device.addCompatibilityListener`. **The token owns the subscription**: dropping it
  unsubscribes, so tokens are stored as fields and cleared to tear down.

Streams do **not** buffer past events. The session may reach `.started` before a `for await`
loop begins iterating, so correct code checks `session.state` first and only then awaits —
`DeviceSessionManager.getSession()` does this at three separate points
(`DeviceSessionManager.swift:62`, `:75`, `:102`). This race is the single most delicate part
of the integration.

---

## 3. Sample app architecture

Both samples are SwiftUI + `@Observable` MVVM, `@MainActor` throughout, targeting iOS 17.

### AssemblyMan

```
AssemblyManApp                    configure() • MockDeviceKit wiring (DEBUG) • alerts
 ├─ MainAppView                    routes on registrationState
 │   ├─ HomeScreenView             unregistered → connect flow
 │   └─ StreamSessionView          registered → StreamView / NonStreamView
 └─ RegistrationView               invisible URL-callback sink

WearablesViewModel        registration state, device list, per-device compatibility,
                          firmware-update prompts
StreamSessionViewModel    permission gate → session → stream → frames/photos
 └─ DeviceSessionManager  owns DeviceSession lifecycle (1:1 with device)
```

The split between `StreamSessionViewModel` and `DeviceSessionManager` is deliberate: the
manager owns *session* lifetime and readiness, the view model owns *stream* state and UI
concerns. `DeviceSessionManager` also **monitors device availability without creating
sessions** — creation is deferred to `getSession()` to avoid a race between availability
callbacks and session start (`DeviceSessionManager.swift:165`).

Stream startup order in `StreamSessionViewModel.startSession()`: check/request
`Permission.camera` → obtain a `.started` session → build `StreamConfiguration` (raw codec,
low resolution, 24 fps) → `addStream` → attach listeners → `start()`. Listeners are attached
*before* `start()` so the first state transition isn't missed. Frames arrive on a background
thread and are hopped to `@MainActor` via `Task { @MainActor in … }`.

### DisplayAccess

`DisplayViewModel` attaches a `Display` to a display-capable device and sends declarative
views (`CarMaintenanceDisplay` is the worked example). It uses a **pending-action** pattern:
`send(_:)` on a not-yet-attached display stores the send as a closure, kicks off
`attachToDisplay()`, and fires the closure once the display is ready — so a user tap never
has to wait on connection state. Registration transitions to `.available`/`.unavailable`
reset the session and rebuild the selector.

### Ownership and teardown rules observed throughout

- Tasks are held in `@ObservationIgnored` fields and cancelled in `isolated deinit`.
- Listener tokens are nil'd to unsubscribe.
- Closures capture `[weak self]`; device names are captured *before* the closure to dodge
  `Sendable` issues (`WearablesViewModel.swift:94`).
- `cleanup()` vs `stopCurrentSession()` are distinct: the former also cancels device
  monitoring and is for release/teardown.

---

## 4. Test system

Testing without hardware is a first-class path, not an afterthought.

- **Unit/in-process**: `MockDeviceKit.shared.enable()`, `pairGlasses(model:)`, and mock
  camera feeds backed by `TestResources/plant.mp4` / `plant.png`.
- **UI tests**: the app hosts an **in-process HTTP test server** so the test process can
  drive mock devices across the process boundary. On launch with `--ui-testing`, the app
  enables MockDeviceKit and calls `MockDeviceKit.shared.startTestServer(portFilePath:)`
  (`AssemblyManApp.swift:52`). The server writes its port to the file named by
  `MWDAT_TEST_SERVER_PORT_FILE`; `MockDeviceTestClient` in the test target polls that file
  and issues commands (`AssemblyManUITests.swift:34`).
  Stale port files are deleted in `setUp` so the client can't latch onto a previous run.
- **Debug menu**: a shake/overlay-driven `MockDeviceKitView` for manual simulation, compiled
  out of release builds by `#if DEBUG`.

All MockDeviceKit usage in the samples is `#if DEBUG`-guarded, keeping the mock module out
of shipping binaries.

---

## 5. AI knowledge-base distribution

One body of guidance, nine topics, published in five formats plus two remote endpoints.

```
plugins/mwdat-ios/skills/<topic>/SKILL.md     ← single source of content
      │
      ├─ plugins/mwdat-ios/.claude-plugin/plugin.json   → Claude Code plugin ("skills": ["./skills/"])
      ├─ plugins/mwdat-ios/.codex-plugin/plugin.json    → Codex plugin (adds interface metadata)
      ├─ .cursor/rules/<topic>.mdc                      → same body + glob triggers
      ├─ .github/copilot-instructions.md                → all topics concatenated
      └─ AGENTS.md                                      → all topics concatenated (portable fallback)

.claude-plugin/marketplace.json  → makes the repo itself a Claude plugin marketplace
```

Topics: `getting-started`, `dat-conventions`, `camera-streaming`, `display-access`,
`session-lifecycle`, `permissions-registration`, `mockdevice-testing`, `debugging`,
`sample-app-guide`.

The bodies are byte-identical across surfaces — only the front matter differs. A skill
declares `name` + `description`; the Cursor `.mdc` swaps in `description` + `globs`
(e.g. `**/*Stream*, **/*Camera*, **/*Video*` for camera-streaming) so rules auto-attach by
file path; `AGENTS.md` and `copilot-instructions.md` prepend a links header and inline
everything.

**This duplication is the maintenance hazard of the repo.** Editing a `SKILL.md` without
regenerating the four derived surfaces leaves them silently divergent, and nothing in the
tree enforces the invariant — there is no generator script or CI check present here.

Two remote endpoints complement the static files: `https://mcp.developer.meta.com/wearables`
(remote HTTP MCP, `search_dat_docs`, **no authentication** — do not configure tokens or auth
headers) and `https://wearables.developer.meta.com/llms.txt?full=true` for tools that only
accept static context.

`install-skills.sh` installs any subset (`claude`, `codex`, `copilot`, `cursor`, `agents`,
`all`). It downloads a `main` tarball, copies file surfaces into the target project, and
shells out to `claude plugin install` / `codex plugin install` for the plugin payloads.
Interactive when a tty is present; defaults to `all` when piped through `curl`. Cleanup is
guarded by a prefix check on the extract directory before any `rm -rf`.

Plugin versions in both `plugin.json` files and `marketplace.json` are pinned to the SDK
version (`0.8.0`) — a release bump has to touch all three.

---

## 6. Configuration contract

An app that skips any of this fails at runtime, usually with an unhelpful symptom.

**`Info.plist` → `MWDAT` dictionary**

| Key | Purpose |
|-----|---------|
| `AppLinkURLScheme` | Scheme Meta AI calls back on, e.g. `assemblyman://`. Must match `CFBundleURLTypes`. |
| `MetaAppID` | App ID from Wearables Developer Center. Required **without** Developer Mode. |
| `ClientToken` | Client token from the same. Required without Developer Mode. |
| `TeamID` | Apple Developer Team ID (`$(DEVELOPMENT_TEAM)`). |
| `Analytics.OptOut` | `YES` disables Meta's data collection. Absent or `NO` = analytics enabled. |

**Also required**: `UIBackgroundModes` = `processing`, `bluetooth-central`,
`bluetooth-peripheral`; `NSBluetoothAlwaysUsageDescription`;
`NSLocalNetworkUsageDescription` + `NSBonjourServices` (`_bonjour._tcp`) for the Wi-Fi
transport added in 0.8.0.

**Entitlements**: keychain access group; AssemblyMan additionally declares
`com.apple.developer.networking.HotspotConfiguration` and
`com.apple.developer.networking.wifi-info`.

**Developer Mode** (Meta AI app → Settings → Your glasses) substitutes for
`MetaAppID`/`ClientToken` during development. Registration failing while these keys are
unset is the classic "forgot Developer Mode" symptom.

---

## 7. Versioning and compatibility

- Semantic versioning, Keep-a-Changelog format, one release per commit.
- The SDK is pre-1.0 and in developer preview: **minor versions carry breaking changes.**
  0.8.0 alone made capability lifecycle methods synchronous, removed the `Capability`
  protocol, renamed `MockDisplaylessGlasses` → `MockGlasses`, and replaced `pairRaybanMeta()`
  with a throwing `pairGlasses(model:)`.
- Three independent compatibility axes exist at runtime and each has its own surfaced state:
  device firmware (`Compatibility.deviceUpdateRequired` → `Wearables.openFirmwareUpdate()`),
  the DAT app on the glasses (`DeviceSessionError.datAppOnTheGlassesUpdateRequired` →
  `openDATGlassesAppUpdate()`), and the SDK version itself. Both samples surface all of them.
- Sample apps target iOS 17.0 / Swift 5.0; the SDK's stated floor is iOS 16.0.

---

## 8. Build and test

```bash
# Samples
open samples/AssemblyMan/AssemblyMan.xcodeproj
xcodebuild -scheme AssemblyMan -destination 'platform=iOS Simulator,name=iPhone 16'
xcodebuild test -scheme AssemblyMan -destination 'platform=iOS Simulator,name=iPhone 16'

# Knowledge base into another project
./install-skills.sh all
```

Simulator builds work end to end via MockDeviceKit — no glasses required. Physical-device
testing additionally needs the Meta AI app installed, Developer Mode on, and paired glasses.

Note: `AGENTS.md` §"Build and Test" references internal paths (`ExternalSampleApps/…`,
`-scheme MWDATCore`) that don't exist in this public tree — an artifact of the internal→
GitHub export. Use the commands above.

---

## 9. Known structural constraints

1. **No CI in the repo.** `.github/` holds only `copilot-instructions.md`; there are no
   workflows, so nothing verifies builds, sample tests, or knowledge-base consistency here.
2. **Five copies of the same guidance** with no generator — see §5.
3. **Version pinned in four places** — `marketplace.json`, two `plugin.json` files, and the
   docs links that hardcode `/dat/0.8`.
4. **PRs cannot be merged directly**; changes flow through Meta's internal repo.
5. **Xcode user state is not ignored** — there is no `.gitignore` at the repo root, and
   `project.xcworkspace/xcuserdata/…/UserInterfaceState.xcuserstate` currently shows as
   untracked in the working copy. Per-sample `.gitignore` files exist but don't cover it.

---

## References

- [`README.md`](../README.md) — installation, developer terms, analytics opt-out
- [`AGENTS.md`](../AGENTS.md) — full portable knowledge base
- [`CHANGELOG.md`](../CHANGELOG.md) — API-level release history
- [Developer documentation](https://wearables.developer.meta.com/docs/develop/)
- [iOS API reference (0.8)](https://wearables.developer.meta.com/docs/reference/ios_swift/dat/0.8)
