# Change list

Running log of changes, newest first.

---

## Second design import — Developer section, SAM overlay, settings from Connect

The Claude Design source had moved on since the first import. Re-read
`AssemblyMan.dc.html` and picked up four additions:

- **Developer / MockDeviceKit section in Settings** (`Views/DeveloperSection.swift`, DEBUG
  only). Enable toggle with a paired count, "Pair Ray-Ban Meta" capped at three devices, and
  a plate per device: identity, Unpair, Power / Donned / Unfolded toggles, Captouch tap and
  tap-and-hold, and a Front / Back / Video file camera-source picker. Wired to the existing
  `MockDeviceKitView.ViewModel` and `MockDeviceCardView.ViewModel` — this is the
  Industry-styled home for what previously lived behind the floating debug button.
- **Segment Anything overlay** (`Views/DesignSystem/SegmentMaskOverlay.swift`) — dashed
  masks with confidence chips over the live feed, plus its Overlays toggle. The SDK exposes
  no segmentation, so the masks are the design's indicative shapes normalised to the
  viewport; swapping in a real segmenter means replacing the mask data only.
- **Settings reachable from the Connect screen** — a settings button now sits beside the
  masthead. This required hoisting settings ownership from `StreamSessionView` up to
  `MainAppView`, which also keeps it above both branches so opening it never unmounts the
  screen underneath.
- **Disconnect is hidden** when settings is opened before the glasses are linked.

Also read the remaining imported files: `ios-frame.jsx` is the prototype's device-frame
scaffold, `support.js` is the Claude Design template runtime, and `_ds_bundle.js` is empty
(the Industry system is pure CSS). None carry app content.

---

## Industry redesign of the AssemblyMan sample app

Branch: `industry-redesign` — in progress, not yet committed.

Implements the `AssemblyMan.dc.html` prototype from the Claude Design project
`443f7de7-8ca7-43ac-a961-081ad26fd0d3` onto the existing SwiftUI codebase. The app flow is
unchanged (connect → ready → session → capture); this is a reskin, a copy rewrite, and one
new screen.

### New design-system layer — `AssemblyMan/Views/DesignSystem/`

| File | Purpose |
|------|---------|
| `Theme.swift` | Colour ramps, type scale, metrics. Corner radius is 0 system-wide. |
| `OperatorGlyph.swift` | New brand mark (hard hat over glasses) as a `Shape`, plus wordmark lockup. Replaces the `assemblyManIcon` PNG in-app. |
| `BlueprintFrame.swift` | The framing device: hairline square border with `+` registration marks straddling the corners. |
| `VectorPath.swift` | SVG path-data → `Path` parser so icon geometry is copied verbatim from the design source rather than re-approximated. |
| `Icon.swift` | The twelve Lucide icons the design calls for, as their original path data. |
| `IndustryControls.swift` | `PrimaryButton`, `OutlineButton`, `IconButton`, `Tag`, `SpecRow`, `SquareCheckbox`, `SegmentedPicker`, `ToastView`, `Spinner`, press/blink modifiers. |

### Screens

- **`HomeScreenView.swift` (Connect)** — rebuilt: overline row, 38pt condensed masthead,
  blueprint figure plate carrying the Operator glyph, three numbered hairline-separated
  feature rows, footnote, full-width square primary button with busy spinner.
- **`NonStreamView.swift` (Ready)** — moved from a black ground to the light ground. Header
  lockup + `LINKED` tag + settings button; a four-row blueprint spec plate (device, link,
  session, agent); centred ready block; the update-required card restyled from the yellow
  banner to an accent-tinted blueprint card; square primary button.
- **`StreamView.swift` (Live session)** — dark accent-900 ground, full-bleed feed, top and
  bottom gradient scrims, viewfinder registration marks, optional thirds grid, live status
  chip with blinking indicator, tabular elapsed clock, outlined stop button and square white
  shutter. White capture flash on shutter.
- **`PhotoPreviewView.swift`** — preview restyled as a framed plate with a caption bar on
  the dark ground; solid white Share and outlined Close actions. Swipe-to-dismiss kept.
- **`SettingsView.swift` (new)** — agent picker, overlay toggles, session (Wi-Fi, quality,
  frame rate), capture preferences, disconnect. Reachable from the ready header and the
  in-session controls.
- **`StreamSessionView.swift`** — now routes between ready / live / settings and owns the
  toast used for non-blocking feedback. Hard errors still use real alerts.
- **`MainAppView.swift`** — owns the `AppSettings` instance so preferences survive routing.

### Behaviour

- **`ViewModels/AppSettings.swift` (new)** — session and overlay preferences.
- **`StreamSessionViewModel`** — takes `AppSettings`; `StreamConfiguration` now uses the
  selected quality and frame rate instead of hardcoded `.low` / 24fps. Added a session
  clock (`elapsedSeconds` / `elapsedText`) started and stopped by the stream state.

### App icon

Regenerated from the Operator glyph per the handoff: glyph centred on the accent-900 ground
in the dark-variant colours (hat accent-300, lenses white). Replaces the previous mascot
artwork.

### Notes / open points

- **Fonts.** The design specifies Barlow Condensed SemiBold for headings and Barlow for
  body. The font files are not bundled, so `Theme` falls back to the system face at a
  condensed width. Dropping the OFL TTFs into the target and listing them under
  `UIAppFonts` activates them with no code change.
- **Agent picker is presentation-only.** The DAT SDK exposes no agent API, so the selection
  is stored in `AppSettings` and shown on the ready plate but does not yet drive anything.
- **`Save to Photos` and `Shutter cue` are likewise inert** — stored but not yet wired.
- **`--design-preview` launch flag** (DEBUG only, `AssemblyManApp.swift`) starts the app in
  a registered state via MockDeviceKit so the gated screens can be reviewed without
  glasses. Added for this review pass; say the word and it comes out.
- The old `CustomButton`, `CircleButton`, `CardView`, and `StatusText` components and the
  `appPrimaryColor` / `destructive*` colorsets are now unused by the redesigned screens but
  are still referenced by the MockDeviceKit debug UI, so they remain for now.

---

## Rename the CameraAccess sample app to AssemblyMan

Commit `629e714`, merged to `main` via PR #1.

Renamed `samples/CameraAccess` → `samples/AssemblyMan` across the directory layout, Xcode
project, scheme, bundle metadata, URL scheme, asset catalog, and user-facing strings, and
updated the docs and knowledge base to match.

Fixes applied on top of the mechanical rename:

- Regenerated the four derived knowledge-base surfaces (`AGENTS.md`,
  `copilot-instructions.md`, and three Cursor rules). Only the `SKILL.md` sources had been
  updated, leaving the published surfaces pointing at CameraAccess.
- Restored the `Camera Access Required` alert title in `MockDeviceCardView` — it labels the
  iOS camera permission prompt, not the app.
- Dropped the leftover `.cameraaccess` suffix from `PRODUCT_BUNDLE_IDENTIFIER`.
- Fixed the stale `projectContext` in the sample's `.claude/settings.json`.
- Added `xcuserdata/` to the sample's `.gitignore`.

Tests added:

- `AssemblyManTests/AppConfigurationTests.swift` — pins the Info.plist configuration
  contract, most importantly that `MWDAT.AppLinkURLScheme` is also declared in
  `CFBundleURLTypes`, plus bundle identity and asset-catalog resolution. 9 tests, passing.
- `scripts/check_repo_consistency.py` — checks the skill-to-surface sync invariant, that
  every sample app named in docs exists under `samples/`, and that each sample's directory,
  scheme, bundle name, and asset-catalog manifests agree. Fourteen surface pairs that were
  already divergent before this change are baselined as warnings so new drift fails.
