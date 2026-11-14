# Bundled models

## face_detection_yunet_2023mar.onnx

YuNet face detector, loaded through `cv2.FaceDetectorYN` in
`src/media/face_detection.py`.

- Source: https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
  (downloaded from
  https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx)
- SHA-256: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`
- License: MIT, Copyright (c) 2020 Shiqi Yu (see `LICENSE-yunet`)

The model lives under `src/` so it ships in the Docker image (`COPY src/`) and
in the docker-compose bind mount (`./backend/src:/app/src`).

## human_segmentation_pphumanseg_2023mar.onnx

PP-HumanSeg person segmentation, loaded through `cv2.dnn.readNetFromONNX` in
`src/media/fx_analysis.py`. Dynamic edits use it for the person matte behind
"behind the head" text: it runs only inside those accents' windows (and on the
studio's base video when a draft has one), never on ordinary renders. Input
192x192 RGB normalised to [-1, 1]; output 1x2x192x192 softmax (channel 1 =
person).

- Source: https://github.com/opencv/opencv_zoo/tree/main/models/human_segmentation_pphumanseg
  (downloaded from
  https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/human_segmentation_pphumanseg/human_segmentation_pphumanseg_2023mar.onnx)
- SHA-256: `552d8a984054e59b5d773d24b9b12022b22046ceb2bbc4c9aaeaceb36a9ddf24`
- License: Apache-2.0, Copyright (c) 2021 PaddlePaddle Authors (see `LICENSE-pphumanseg`)
