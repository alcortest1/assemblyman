# AssemblyMan App

A sample iOS application demonstrating integration with Meta Wearables Device Access Toolkit. This app showcases streaming video from Meta AI glasses, capturing photos, and managing connection states.

## Features

- Connect to Meta AI glasses
- Stream camera feed from the device
- Segment the center-reticle object or multiple regions across the full frame with MobileSAM
- Run YOLO object detection, instance segmentation, or combined scene segmentation
- Set on-device vision processing from 0.5 through 15 FPS while streaming
- Toggle the segmentation mask, composition grid, and center reticle while streaming
- Capture photos from glasses
- Share captured photos
- Open firmware and glasses app update flows when required

## Prerequisites

- iOS 17.0+
- Xcode 14.0+
- Swift 5.0+
- Meta Wearables Device Access Toolkit (included as a dependency)
- A Meta AI glasses device for testing (optional for development)

## Building the app

### Using Xcode

1. Clone this repository
1. Open the project in Xcode
1. Select your target device
1. Click the "Build" button or press `Cmd+B` to build the project
1. To run the app, click the "Run" button (▶️) or press `Cmd+R`

## Running the app

1. Turn 'Developer Mode' on in the Meta AI app.
1. Launch the app.
1. Press the "Connect" button to complete app registration.
1. Once connected, the camera stream from the device will be displayed
1. Use the on-screen controls to:
   - Open **Overlays**, toggle the vision overlay, and choose a MobileSAM or YOLO mode
   - Choose MobileSAM **Reticle** or **Full frame** targeting
   - Set the on-device vision processing rate from 0.5 through 15 FPS
   - Toggle **Grid** or **Reticle**
   - Capture photos
   - View and save captured photos
   - Disconnect from the device
1. If a firmware update is required, tap "Update firmware" from the connection screen.
1. If session start reports that the app on the glasses is outdated, tap "Update app on glasses" from the connection screen.

## MobileSAM live overlay

MobileSAM runs entirely on the iPhone using Core ML; streamed frames are not
sent to a server. **Reticle** mode targets the center object. **Full frame**
mode reuses one image encoding with a 3×3 grid of independent point prompts,
then merges their best masks for broader scene coverage. Only one inference is
allowed at a time, so incoming frames are dropped while the model is busy
instead of building a processing backlog. The overlay panel sets a maximum
processing rate from 0.5 through 15 FPS and shows the latest inference time.

The Core ML models and prompt weights are bundled with the app. See the
repository's `THIRD_PARTY_NOTICES.md` for their sources and license.

## YOLO live overlays

The three YOLO modes also run locally with bundled Core ML models:

- **YOLO Objects** draws class-colored instance masks.
- **YOLO Scene** combines street-scene semantic masks with object masks.
- **YOLO Detect** draws lightweight class-colored boxes and confidence labels.

The color map covers common scene, transportation, office, and indoor objects.
Classes outside that curated map remain transparent. Ultralytics YOLO uses the
AGPL-3.0 license; commercial distribution requires either complying with the
AGPL or obtaining an Ultralytics Enterprise license. See the repository's
`THIRD_PARTY_NOTICES.md`.

## Troubleshooting

For issues related to the Meta Wearables Device Access Toolkit, please refer to the [developer documentation](https://wearables.developer.meta.com/docs/develop/) or visit our [discussions forum](https://github.com/facebook/meta-wearables-dat-ios/discussions)

## License

This source code is licensed under the license found in the LICENSE file in the root directory of this source tree.
