/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// MainAppView.swift
//
// Central navigation hub. Shows the connect flow until the app is registered with the DAT
// SDK, and the session flow afterwards.
//
// Settings is owned here rather than inside either branch: it is reachable from both, and
// hosting it above them means opening it never unmounts the screen underneath — which
// would tear down a live session.
//

import MWDATCore
import SwiftUI

struct MainAppView: View {
  let wearables: WearablesInterface
  var viewModel: WearablesViewModel

  #if DEBUG
  var mockKit: MockDeviceKitView.ViewModel?
  #endif

  /// Session and overlay preferences, held above the routed screens so they survive
  /// navigation between connect, ready, live, and settings.
  @State private var settings = AppSettings()
  /// Held at the same level as `settings` and for the same reason: its weights are a 3.32 GB
  /// download, Settings is where that download is started and watched, and Settings is
  /// reachable before there is a session to hang it off.
  @State private var localGrader = LocalGrader()
  @State private var showingSettings = false

  private var isRegistered: Bool {
    viewModel.registrationState == .registered
  }

  var body: some View {
    ZStack {
      if isRegistered {
        StreamSessionView(
          wearables: wearables,
          wearablesVM: viewModel,
          settings: settings,
          localGrader: localGrader,
          openSettings: { showingSettings = true }
        )
      } else {
        // User not registered - show registration/onboarding flow
        HomeScreenView(
          viewModel: viewModel,
          openSettings: { showingSettings = true }
        )
      }

      if showingSettings {
        settingsScreen
          .transition(.opacity)
          .zIndex(1)
      }
    }
    .animation(.easeOut(duration: 0.2), value: showingSettings)
    // Disconnecting from within settings drops registration; close settings so the connect
    // screen is not left hidden behind it.
    .onChange(of: isRegistered) { _, registered in
      if !registered { showingSettings = false }
    }
  }

  private var settingsScreen: some View {
    #if DEBUG
    SettingsView(
      settings: settings,
      localGrader: localGrader,
      showsDisconnect: isRegistered,
      onBack: { showingSettings = false },
      onDisconnect: {
        showingSettings = false
        viewModel.disconnectGlasses()
      },
      mockKit: mockKit
    )
    #else
    SettingsView(
      settings: settings,
      localGrader: localGrader,
      showsDisconnect: isRegistered,
      onBack: { showingSettings = false },
      onDisconnect: {
        showingSettings = false
        viewModel.disconnectGlasses()
      }
    )
    #endif
  }
}
