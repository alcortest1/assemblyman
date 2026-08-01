# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.0] - 2026-06-25

### Added

- Meta Glasses support.
- [API] **Unified error model.** Introduced the `DatError` protocol (conforming to `LocalizedError`); all SDK error types now conform to it and expose a consistent `description`. This brings the iOS error model in line with Android.
- [API] `CaptureError` enum for photo capture, with timeout and failure cases.
- [API] `RegistrationError.timeout` and `UnregistrationError.timeout` cases.
- [API] `DeviceType.metaGlasses` value.
- [API] `Display.clearDisplay()` to clear rendered display content (async throwing; Obj-C `clearDisplayWithCompletion:`).
- [Feature] **MockDeviceKit multi-glasses support.** `MockDeviceKit.pairGlasses(model:)` is a single factory for all supported glasses models, replacing `pairRaybanMeta()`. It throws `MockDeviceKitError` (`.notEnabled`) instead of returning an optional.
  - `GlassesModel` enum (`.rayBanMeta`, `.oakleyMetaHSTN`, `.oakleyMetaVanguard`, `.rayBanMetaOptics`, `.metaGlasses`).
  - `MockDeviceKitError` typed error.
  - Obj-C: `pairGlasses(modelRawValue:)` replaces `pairRaybanMeta`.
- [Feature] WiFi transport.

### Changed

- [API] Camera and Display lifecycle methods are now synchronous: `Stream.start()` / `Stream.stop()` and `Display.start()` / `Display.stop()` are no longer `async`. The Obj-C completion-handler variants (`startWithCompletion:` / `stopWithCompletion:`) are removed accordingly.
- [API] Capabilities (`Stream`, `Display`) no longer conform to a `Capability` protocol; they are managed directly through their `DeviceSession` via `addStream` / `addDisplay` (and the corresponding removal methods). `DeviceSession.addCapability(_:)` / `removeCapability(_:)` are removed.
- [API] Renamed `MockDisplaylessGlasses` protocol to `MockGlasses` and `MockDisplaylessGlassesServices` to `MockGlassesServices`.

### Removed

- [API] `MockRaybanMeta` protocol (use `MockGlasses`).
- [API] `pairRaybanMeta()` (use `pairGlasses(model: .rayBanMeta)`).

## [0.7.0] - 2026-05-14

### Added

- [Feature] Display capability — `MWDATDisplay` brings visual experiences to Meta Ray-Ban Display glasses, with content rendering (FlexBox, Text, Button, Image, Icon) and MP4 video playback.
  - Added `Display` class (conforming to `Capability`), attached via `DeviceSession.addDisplay(...)`. `display.send(_:)` submits a single root view (`FlexBox` for UI, `VideoPlayer` for video); each call replaces the previous content.
  - Added view types: `FlexBox`, `Text`, `Button`, `Image`, `Icon`, `VideoPlayer` (conforming to `DisplayableView`), plus typed `DisplayState`, `DisplayError`, `VideoError` / `VideoErrorType`, `VideoCodec`, `VideoPlaybackEvent` / `VideoPlaybackEventType` for observation.
  - Added styling primitives: `Direction`, `Alignment`, `ButtonStyle`, `CornerRadius`, `IconName`, `IconStyle`, `ImageSize`, `TextColor`, `TextStyle`, `Background`, `Edge`, `EdgeInsets`.
  - Added `ComponentBuilder` result builder enabling SwiftUI-style `display.send { ... }` content blocks composed of `ViewComponent` views.
  - Added Objective-C bridge for the full Display surface (`MWDATDisplay`, `MWDATFlexBox`, `MWDATText`, `MWDATButton`, `MWDATImage`, `MWDATIcon`, plus the corresponding `MWDAT*` styling enums) and a `displayStateChanged` Obj-C notification name.
  - Note: if a file imports both `MWDATDisplay` and `SwiftUI`, names like `Text`, `Button`, and `Image` can be ambiguous; qualify as `MWDATDisplay.Text` etc., or keep Display builders in files that don't import SwiftUI.
- [Feature] Device Access Toolkit App Model (DAM) — a new architecture model for the SDK. Apps opt in via the corresponding Info.plist key. DAM is required for the new Display capability; both the App Model flow and the older flow continue to be supported for camera functionality on Meta AI glasses.
  - Added `Wearables.openDATGlassesAppUpdate()` (and Obj-C `MWDATWearables.openDATGlassesAppUpdate`) to open the Meta AI DAT app update destination for the configured app.
- [Feature] Captouch simulation — `MockDeviceKit` now allows simulating `tap` and `tapAndHold` captouch gestures via the new `MockCaptouchKit` protocol (with `MockCaptouchKit` Obj-C bridge), accessed via `MockDisplaylessGlasses.services.captouch`.
- [Feature] `MockDeviceTestClient` module enables controlling mock devices from the UI test process.
- [API] Added `Wearables.openFirmwareUpdate()` (and Obj-C `MWDATWearables.openFirmwareUpdate`) to open the Meta AI firmware update screen for the connected device.
- [API] Added `Wearables.deviceStateStream(for:)` exposing live per-device state via `AsyncStream<DeviceState>`, including current `ThermalLevel`.
- [API] Added `DeviceState` struct with a `thermalLevel` property.
- [API] Added `ThermalLevel` enum exposing per-device thermal state.
- [API] Added `NavigationError` enum: typed error for the new `openFirmwareUpdate` / `openDATGlassesAppUpdate` APIs.
- [API] Added new `DeviceSessionError` cases for thermal, battery, and peak-power conditions: `.thermalCritical`, `.thermalEmergency`, `.peakPowerShutdown`, `.batteryCritical`, plus `.datAppOnTheGlassesUpdateRequired` for surfacing required app updates.
- [API] Added new `StreamError` cases for thermal, battery, and peak-power conditions: `.thermalEmergency`, `.peakPowerShutdown`, `.batteryCritical`. (Pre-existing cases `.timeout`, `.permissionDenied`, `.hingesClosed`, `.thermalCritical` carry over from `StreamSessionError` under the new type name — see *Changed*.)
- [API] Added `DeviceFilter` typealias and an optional `filter:` parameter on `AutoDeviceSelector(wearables:filter:)` (default `nil`) to constrain auto-selection — e.g., `filter: { $0.supportsDisplay() }`.
- [API] Added `Device.supportsDisplay()` and `DeviceType.supportsDisplay` for capability-aware device filtering.
- [API] Added Objective-C bridge expansion: `MWDATDeviceSession` now exposes `state` + `addStateListener`. New `MWDATDeviceSessionState`, `MWDATLinkState`, `MWDATCompatibility` Obj-C enums. `MWDATDevice` adds `linkState`, `compatibility`, `addLinkStateListener`, and `addCompatibilityListener` — gives Obj-C consumers parity with the Swift accessors previously shipped.
- [API] Added `MockDeviceKitInterface.startTestServer(portFilePath:)` and `stopTestServer()` for managing the in-process MockDevice test server.

### Changed

- [API] Renamed camera capability types for cross-platform naming parity:
  - `StreamSession` is now `Stream` (returned from `DeviceSession.addStream(config:)`).
  - `StreamSessionConfig` is now `StreamConfiguration`.
  - `StreamSessionState` is now `StreamState`.
  - `StreamSessionError` is now `StreamError` (with additional cases — see *Added*).
  - `MWDATStreamSession` / `MWDATStreamSessionConfig` Obj-C types replaced by `MWDATStream` / `MWDATStreamConfiguration`.
  - Notification names: `streamSessionStateChanged` → `streamStateChanged`, `streamSessionFrameReceived` → `streamFrameReceived`, `streamSessionPhotoCaptured` → `streamPhotoCaptured`, `streamSessionErrorOccurred` → `streamErrorOccurred`. `MWDATStreamConfiguration`-based `addStream(config:error:)` Obj-C selector added alongside the existing zero-arg `addStream(error:)`.
- [API] Consolidated `RegistrationManager` and `PermissionsManager` URL handlers. `handleFinishRegistrationUrl(_:)` / `handleDeleteRegistrationUrl(_:)` are replaced by `handleFinishRegistration(params:)` / `handleDeleteRegistration()`; `handlePermissionUrl(_:)` is replaced by `handlePermission(params:)`. URL parsing now goes through `Wearables.handleUrl(_:)` upstream.
- [API] `FakeRegistrationHandling.handleUnregisterUrl(_:)` is replaced by `handleUnregister()`.

### Removed

- [API] `WearablesInterface.addDeviceSessionStateListener(forDeviceId:listener:)` and `MWDATWearables.addDeviceSessionStateListener` in favor of observing the `DeviceSession` directly via `stateStream()`.
- [API] `DeviceStateSession` class; observe device state via `Wearables.deviceStateStream(for:)` instead.

### Fixed

- `MockDeviceKit`: streaming now correctly resumes when the device transitions to donned-disabled and back via fold/unfold cycle.
- `MWDATCore`: fixed a latent typed-throws crash in `FWALinkedAppManagerRegistrationProvider`.
- `DeviceSession`: stop transition is now correctly propagated on the session channel; display and device-health surfaces moved onto that channel.

## [0.6.0] - 2026-04-15

### Added

- Ray-Ban Meta Optics glasses support.
- [Feature] `MockCameraKit` can use the phone camera (front and back) to simulate streaming with `MockCameraKit.setCameraFeed(cameraFacing)`.
- [Feature] `MockDeviceKit` now supports configuration to simulate device registration and permissions.
  - `MockDeviceKitConfig` struct to configure `MockDeviceKit` with `initiallyRegistered` and `initialPermissionsGranted` options.
  - `MockPermissions` protocol with `set` and `setRequestResult` to simulate permission states in tests.
  - `MockDeviceKitInterface`: added `enable(config:)`, `disable`, `isEnabled`, `pairedDevices`, and `permissions` for controlling MockDeviceKit lifecycle and permissions.
- [API] Session-based device management. Device interactions are now scoped to a `DeviceSession` with explicit lifecycle control.
  - `Wearables.createSession(deviceSelector:)` to create a `DeviceSession` for a given `DeviceSelector`.
  - `DeviceSession` class with `start`, `stop`, state observation via `statePublisher` / `stateStream()`, and error observation via `errorPublisher` / `errorStream()`.
  - `DeviceSessionState` enum with values `idle`, `starting`, `started`, `paused`, `stopping`, `stopped`.
  - `DeviceSessionError` enum with typed error cases including `noEligibleDevice`, `sessionAlreadyExists`, `capabilityAlreadyActive`, and more.
  - `Capability` protocol and `CapabilityState` enum for extending sessions with additional features such as camera streaming.
  - `DeviceSession.addStream(config:)` to add a camera `StreamSession` as a capability to a device session.
- [API] `MockDisplaylessGlassesServices` protocol grouping mock services, accessible via `MockDisplaylessGlasses.services`.
- [Feature] Objective-C camera API. `MWDATCamera` is now fully usable from Objective-C via `MWDATStreamSession` and related types.
  - Objective-C `MWDATStreamSession` with listener-based callbacks for state, video frames, photos, and errors, plus `NSNotification` names for each event.
  - Objective-C configuration and type wrappers: `MWDATStreamSessionConfig`, `MWDATVideoFrame`, `MWDATPhotoData`, `MWDATStreamSessionState`, `MWDATStreamSessionError`, `MWDATStreamingResolution`, `MWDATVideoCodec`, `MWDATPhotoCaptureFormat`.
  - Objective-C device selectors: `MWDATSpecificDeviceSelector` and `MWDATAutoDeviceSelector`.

### Changed

- [API] `MockDeviceKitInterface`, `MockDevice`, `MockCameraKit`, and related protocols no longer require `@MainActor` and now conform to `Sendable`, making them safe to use from any thread.
- [API] `MockCameraKit.setCameraFeed(fileURL:)` and `setCapturedImage(fileURL:)` are no longer `async`.
- Improved the AssemblyMan App MockDevice UI.

### Fixed

- `MockDevice` better simulates state when a device is powered off or doffed.

### Removed

- [API] `MockDeviceKitError` enum.
- [API] `MockDisplaylessGlasses.getCameraKit` has been removed. The functionality is accessible through `MockDisplaylessGlasses.services`.

## [0.5.0] - 2026-03-11

### Added

- [Feature] `VideoCodec.hvc1` to `StreamSessionConfig` for compressed HEVC streaming that continues in the background. The default `VideoCodec.raw` pauses streaming when app is backgrounded.
- [Feature] Support for app attestation.
- [API] `thermalCritical` to `StreamSessionError` to indicate that the device's thermal state has reached a critical level that may affect streaming performance.
- AI coding agents config files: AGENTS.md, Claude skills, Cursor rules, Copilot instructions.

### Removed

- [API] `@MainActor` requirement from MWDATCamera to enable safely calling this from any thread.
- [API] `HingeState` enum.
- [API] `DeviceState` struct.
- [Dependency] `nanopb` library dependency which was blocking Apple review for iOS apps.
- [AssemblyMan] Removed timer functionality.

### Fixed

- High resolution (720x1280) video can now be requested.

### Changed

- [AssemblyMan] Improved photo capture flow.

## [0.4.0] - 2026-02-03

> **Note:** This version requires updated configuration values from Wearables Developer Center for release channel functionality.

### Added

- Meta Ray-Ban Display glasses support.
- [API] `hingesClosed` value in `StreamSessionError`.
- [API] `UnregistrationError`, and moved some values from `RegistrationError` to it.
- [API] `networkUnavailable` value in `RegistrationError`.
- [API] `WearablesHandleURLError`.

### Changed

- `MWDATCore` types are now `Sendable`, making the SDK thread-safe.

### Fixed

- Fixed streaming status when switching between devices.
- Fixed streaming status failing to reach `Streaming` state. A race condition caused this issue.

## [0.3.0] - 2025-12-16

### Changed

- [API] In `PermissionError`, `companionAppNotInstalled` has been renamed to `metaAINotInstalled`.
- Relaxed constraints to API methods, allowing some to run outside `@MainActor`.
- The AssemblyMan app streaming UI reflects device availability.
- The AssemblyMan app shows errors when incompatible glasses are found.
- The AssemblyMan app can now run in background mode, without interrupting streaming (but stopping video decoding).

### Fixed

- Streaming status is set to `stopped` if permission is not granted.
- Fixed UI issues in the AssemblyMan app.

## [0.2.1] - 2025-12-04

### Added

- [API] Raw `CMSampleBuffer` to `VideoFrame`.

### Changed

- The SDK does not require setting `CFBundleDisplayName` in the app's `Info.plist` during development.

### Fixed

- Streaming can now continue when the app is in background mode.

## [0.2.0] - 2025-11-18

### Added

- [API] New `compatibility` method in `Device`.
- [API] `addCompatibilityListener` to react to compatibility changes.
- [API] Convenience initializer on `StreamSession` enabling user provided `StreamSessionConfig`.
- Description to enum types and made them `CustomStringConvertible` for easier printing.

### Changed

- [API] The SDK is now split into separate components, allowing independent inclusion in projects as needed.
- [API] Obj-C functions no longer use typed throws; they now throw only `Error`.
- [API] Permission API updated for better consistency with Android:
  - `isPermissionGranted` renamed to `checkPermissionStatus`, returning `PermissionStatus` instead of `Bool`.
  - `requestPermission` now returns `PermissionStatus` instead of `Bool`.
  - Added `PermissionStatus` with values `granted` and `denied`, instead of the `Bool` used before.
  - Updated `PermissionError` values.
- [API] `RegistrationError` now holds different errors, aligning more closely with the Android SDK.
- [API] Renamed `DeviceType` enum values.
- [API] Replaced `MockDevice` `UUID` with `DeviceIdentifier`.
- Updated `StreamingResolution.Medium` from 540x960 to 504x896 to match Android.
- `AutoDeviceSelector` now selects or drops devices based on connectivity state.
- Adaptive Bit Rate (streaming) now works with the provided resolution and frame rate hints.
- AssemblyMan app redesigned and updated to the current SDK version.

### Removed

- [API] `androidPermission` property from `Permission`.
- [API] `prepare` method from `StreamSession`.

### Fixed

- Fixed issue where sessions sometimes failed to close when connection with glasses was lost.

## [0.1.0] - 2025-10-30

### Added

- First version of the Wearables Device Access Toolkit for iOS.
