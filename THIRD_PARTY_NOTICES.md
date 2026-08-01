# Third-party notices

## MobileSAM

AssemblyMan includes Core ML conversions of MobileSAM and prompt-encoding
weights from the MobileSAM project:

- Project: https://github.com/ChaoningZhang/MobileSAM
- Model/runtime distribution: https://github.com/john-rocky/SamKit
- License: Apache License 2.0

The local Swift preprocessing and prompt-encoding implementations are adapted
from SAMKit. Both upstream projects are distributed under the Apache License,
Version 2.0. A copy of the license is included at
`LICENSES/SAMKit-Apache-2.0.txt`.

## Ultralytics YOLO

AssemblyMan integrates the Ultralytics YOLO Swift package and official YOLO26
nano Core ML model assets for object detection, instance segmentation, and
semantic segmentation:

- Project: https://github.com/ultralytics/yolo-ios-app
- Swift package version: 8.9.11
- Model release: https://github.com/ultralytics/yolo-ios-app/releases/tag/v8.3.0
- License: GNU Affero General Public License v3.0 (AGPL-3.0)

Commercial distribution must comply with the AGPL-3.0; Ultralytics offers an
Enterprise license for proprietary distribution. The full AGPL-3.0 license is
distributed with the Swift package dependency and is available at
https://github.com/ultralytics/yolo-ios-app/blob/v8.9.11/LICENSE.
