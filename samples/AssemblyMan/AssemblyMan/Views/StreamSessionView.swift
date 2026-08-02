/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// StreamSessionView.swift
//
// Routes between the ready and live screens once the app is registered, and owns the toast
// used for non-blocking feedback.
//
// Settings is layered over the current screen rather than replacing it. Swapping the live
// screen out would unmount it mid-session and tear the stream down, so the stream stays
// mounted underneath while settings is open.
//

import MWDATCore
import SwiftUI

struct StreamSessionView: View {
  let wearables: WearablesInterface
  var wearablesViewModel: WearablesViewModel
  var settings: AppSettings
  var openSettings: () -> Void
  @State private var viewModel: StreamSessionViewModel

  @State private var toast: String?

  init(
    wearables: WearablesInterface,
    wearablesVM: WearablesViewModel,
    settings: AppSettings,
    openSettings: @escaping () -> Void
  ) {
    self.wearables = wearables
    self.wearablesViewModel = wearablesVM
    self.settings = settings
    self.openSettings = openSettings
    self._viewModel = State(
      wrappedValue: StreamSessionViewModel(wearables: wearables, settings: settings)
    )
  }

  var body: some View {
    ZStack {
      // The base screen follows the view model rather than a separate navigation flag, so
      // the two can never disagree about whether a session is live.
      if viewModel.isStreaming {
        StreamView(
          viewModel: viewModel,
          wearablesVM: wearablesViewModel,
          settings: settings,
          openSettings: openSettings
        )
      } else {
        NonStreamView(
          viewModel: viewModel,
          wearablesVM: wearablesViewModel,
          settings: settings,
          openSettings: openSettings
        )
      }

      if let toast {
        VStack {
          Spacer()
          ToastView(message: toast)
            .padding(.bottom, 96)
        }
        .allowsHitTesting(false)
        .zIndex(2)
      }
    }
    .animation(.easeOut(duration: 0.2), value: toast)
    // Only fires when leaving the registered flow entirely, so it is safe to end the
    // session here — navigating to settings no longer unmounts this view.
    .onDisappear { viewModel.endSession() }
    // Quality and frame rate are baked into the stream at creation, so a change mid-session
    // only takes effect once the stream is rebuilt.
    .onChange(of: settings.quality) { _, _ in applySessionSettings() }
    .onChange(of: settings.frameRate) { _, _ in applySessionSettings() }
    // The relay is not part of StreamConfiguration, so this deliberately does not go through
    // applySessionSettings() — restarting the glasses stream to turn a room on or off would
    // interrupt the very feed being relayed.
    .onChange(of: settings.relaysToLiveKit) { _, relays in applyRelaySetting(relays) }
    .alert("Error", isPresented: $viewModel.showError) {
      Button("OK") {
        viewModel.dismissError()
      }
    } message: {
      Text(viewModel.errorMessage)
    }
    .alert("Photo capture failed", isPresented: $viewModel.showPhotoCaptureError) {
      Button("OK") {
        viewModel.dismissPhotoCaptureError()
      }
    } message: {
      Text("Unable to capture photo. This may be due to low storage on device or another capture already in progress. Please try again in a few moments.")
    }
  }

  private func applySessionSettings() {
    guard viewModel.isStreaming else { return }
    viewModel.restartStream()
    show(toast: "Restarting at \(settings.quality.label) · \(settings.frameRate.label)")
  }

  private func applyRelaySetting(_ relays: Bool) {
    guard viewModel.isStreaming else { return }
    if relays {
      viewModel.relay.start(agent: settings.agent)
      show(toast: "Relaying to a LiveKit room")
    } else {
      viewModel.relay.stop()
      show(toast: "Relay stopped")
    }
  }

  /// Shows a transient message at the bottom of the screen.
  private func show(toast message: String) {
    toast = message
    Task {
      try? await Task.sleep(nanoseconds: 2_200_000_000)
      if toast == message { toast = nil }
    }
  }
}
