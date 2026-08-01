/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// PhotoPreviewView.swift
//
// Preview for a still captured with Stream.capturePhoto(). Presents the photo as a framed
// plate on the dark ground and hands off to the iOS share sheet.
//

import SwiftUI

struct PhotoPreviewView: View {
  let photo: UIImage
  let onDismiss: () -> Void

  @State private var showShareSheet = false
  @State private var dragOffset = CGSize.zero

  var body: some View {
    ZStack {
      Theme.accent900.opacity(0.88)
        .ignoresSafeArea()
        .onTapGesture { dismissWithAnimation() }

      VStack(spacing: 20) {
        plate
        actions
      }
      .frame(maxWidth: 300)
      .padding(24)
      .offset(dragOffset)
      .animation(.spring(response: 0.6, dampingFraction: 0.8), value: dragOffset)
      .gesture(
        DragGesture()
          .onChanged { dragOffset = $0.translation }
          .onEnded { value in
            if abs(value.translation.height) > 100 {
              dismissWithAnimation()
            } else {
              withAnimation(.spring()) { dragOffset = .zero }
            }
          }
      )

      VStack {
        HStack {
          Spacer()
          IconButton(
            glyph: .close,
            accessibilityLabel: "Close preview",
            edge: 38,
            iconSize: 16,
            tint: .white,
            border: .white.opacity(0.5)
          ) {
            dismissWithAnimation()
          }
          .accessibilityIdentifier("close_preview_button")
        }
        Spacer()
      }
      .padding(.horizontal, Theme.screenPadding)
      .padding(.top, 12)
    }
    .sheet(isPresented: $showShareSheet) {
      ShareSheet(photo: photo)
    }
  }

  // MARK: - Sections

  private var plate: some View {
    VStack(spacing: 0) {
      Image(uiImage: photo)
        .resizable()
        .aspectRatio(contentMode: .fill)
        .frame(height: 340)
        .clipped()

      Text("Capture 001 — JPEG · from glasses")
        .overlineStyle(size: 9, color: .white.opacity(0.75))
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .overlay(alignment: .top) {
          Rectangle().fill(.white.opacity(0.35)).frame(height: Theme.hairline)
        }
    }
    .blueprintFrame(border: .white.opacity(0.35), mark: .white.opacity(0.85))
  }

  private var actions: some View {
    HStack(spacing: 12) {
      Button {
        showShareSheet = true
      } label: {
        HStack(spacing: 8) {
          Icon(glyph: .share, size: 16, color: Theme.accent900)
          Text("Share")
            .font(Theme.body(13, weight: .semibold))
            .tracking(13 * 0.06)
            .textCase(.uppercase)
        }
        .foregroundStyle(Theme.accent900)
        .frame(maxWidth: .infinity)
        .frame(height: 48)
        .background(.white)
      }
      .buttonStyle(PressableStyle(pressedOverlay: Theme.accent100))

      Button {
        dismissWithAnimation()
      } label: {
        Text("Close")
          .font(Theme.body(13, weight: .semibold))
          .tracking(13 * 0.06)
          .textCase(.uppercase)
          .foregroundStyle(.white)
          .frame(maxWidth: .infinity)
          .frame(height: 48)
          .overlay { Rectangle().strokeBorder(.white.opacity(0.6), lineWidth: Theme.hairline) }
      }
      .buttonStyle(PressableStyle(pressedOverlay: .white.opacity(0.12)))
    }
  }

  private func dismissWithAnimation() {
    withAnimation(.easeInOut(duration: 0.3)) {
      dragOffset = CGSize(width: 0, height: UIScreen.main.bounds.height)
    }
    Task {
      try? await Task.sleep(nanoseconds: 300_000_000)
      onDismiss()
    }
  }
}

struct ShareSheet: UIViewControllerRepresentable {
  let photo: UIImage

  func makeUIViewController(context: Context) -> UIActivityViewController {
    let activityViewController = UIActivityViewController(
      activityItems: [photo],
      applicationActivities: nil
    )

    // Exclude certain activity types if needed
    activityViewController.excludedActivityTypes = [
      .assignToContact,
      .addToReadingList,
    ]

    return activityViewController
  }

  func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {
    // No updates needed
  }
}
