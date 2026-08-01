# AssemblyMan App

A sample iOS application demonstrating integration with Meta Wearables Device Access Toolkit. This app showcases streaming video from Meta AI glasses, capturing photos, and managing connection states.

## Features

- Connect to Meta AI glasses
- Stream camera feed from the device
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
   - Capture photos
   - View and save captured photos
   - Disconnect from the device
1. If a firmware update is required, tap "Update firmware" from the connection screen.
1. If session start reports that the app on the glasses is outdated, tap "Update app on glasses" from the connection screen.

## Troubleshooting

For issues related to the Meta Wearables Device Access Toolkit, please refer to the [developer documentation](https://wearables.developer.meta.com/docs/develop/) or visit our [discussions forum](https://github.com/facebook/meta-wearables-dat-ios/discussions)

## License

This source code is licensed under the license found in the LICENSE file in the root directory of this source tree.
