# VirtIO GPU Windows ARM64 driver package 20.15.1.1220

Cut 2026-10-03 from `gpu-driver-1219` by `new-gpu-driver.sh`.

Submit pending offscreen clears at UMD command boundaries

Replaced:
- + dx11um_virtio_arm64.dll <- dist/arm64x/dx11um_virtio_arm64.dll  (2026-10-03 19:46, 659ffb9747f4)

Deploy: `bash win3d/deploy-gpu-driver.sh` (newest package by default).
