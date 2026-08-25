#!/usr/bin/env python3
"""
pack-vmpkg.py - Assemble a DroidVM `.vmpkg` from a qcow2 disk + a local VM config (vms.json).

Device-less, offline, dependency-free (Python 3 stdlib only). Produces a package that the
DroidVM app imports directly, with the VM configuration (RAM / swiotlb / serial ports / boot)
baked in -- so a shipped image is "import and run", no manual VM setup, no app export round-trip.

Container format (reverse-engineered from the app's VMExportTask / PackageHeader; all offsets
verified against lib/pkg + daemon/vm/pkg):

  offset 0                 24-byte header (little-endian)
                             [0:5]  "VMPKG"          magic
                             [5]    0x00             magic terminator
                             [6:8]  u16 manifest_version = 1
                             [8:10] u16 app_version_code (must equal manifest app_version_code)
                             [10:12]u16 manifest_size   (bytes of the header-region manifest.json)
                             [12:14]u16 compression     (0 none / 1 gzip / 2 xz / 3 zstd)
                             [14:16]u16 reserved = 0    (volume_count; 0 = single file)
                             [16:24]i64 data_size       (bytes of the compressed data blob)
  -> zero pad to 0x1000
  offset 0x1000            manifest.json (authoritative), manifest_size bytes
  -> zero pad to alignUpStrict(0x1000 + manifest_size)   (0x1000 grid; +0x1000 if already aligned)
  data_start (0x1000 grid) compressed(tar) blob, data_size bytes
                             tar (GNU) entries: manifest.json (redundant, ignored on import),
                             then each disk by archive_path
  -> zero pad to alignUp(data_end)                        (no trailing bytes beyond this)

The reader validates: magic, manifest_version==1, known compression, header<->manifest cross
checks (app_version_code, compression), exact data_size consumption, and all-zero padding.

Usage:
  pack-vmpkg.py --qcow2 out.qcow2 --config vms.json --out win11.vmpkg
                [--disk-name win11.qcow2] [--compression gzip|none|xz]
                [--app-version 1.0] [--app-version-code 1]
"""
import argparse
import gzip
import io
import json
import lzma
import os
import re
import struct
import sys
import tarfile
import time

ALIGN = 0x1000
HEADER_SIZE = 24
MAGIC = b"VMPKG"
MANIFEST_VERSION = 1
MANIFEST_NAME = "manifest.json"

# Compression type ids, matching the app's Compression enum (lib/archive/Compression.java).
COMPRESSION_ID = {"none": 0, "gzip": 1, "xz": 2, "zstd": 3}
# Enum constant names the manifest carries (read case-insensitively by the app).
COMPRESSION_NAME = {"none": "NONE", "gzip": "GZIP", "xz": "XZ", "zstd": "ZSTD"}


def align_up(v, a=ALIGN):
    return (v + a - 1) & ~(a - 1)


def align_up_strict(v, a=ALIGN):
    aligned = align_up(v, a)
    return v + a if aligned == v else aligned


def sanitize_basename(path):
    """Mirror the app's archive_path: the basename, stripped to a safe token."""
    name = os.path.basename(path)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name or "disk.img"


def write_zero(f, n):
    ZERO = b"\x00" * 65536
    while n > 0:
        step = min(len(ZERO), n)
        f.write(ZERO[:step])
        n -= step


def build_header(manifest_size, data_size, comp_id, app_version_code):
    if not (0 < manifest_size <= 0xFFFF):
        raise ValueError("manifest too large for u16 manifest_size: %d" % manifest_size)
    if not (0 <= app_version_code <= 0xFFFF):
        raise ValueError("app_version_code out of u16 range: %d" % app_version_code)
    hdr = bytearray(HEADER_SIZE)
    hdr[0:5] = MAGIC
    # hdr[5] stays 0 (magic terminator)
    struct.pack_into("<H", hdr, 6, MANIFEST_VERSION)
    struct.pack_into("<H", hdr, 8, app_version_code)
    struct.pack_into("<H", hdr, 10, manifest_size)
    struct.pack_into("<H", hdr, 12, comp_id)
    # hdr[14:16] reserved (volume_count) stays 0
    struct.pack_into("<q", hdr, 16, data_size)
    return bytes(hdr)


def open_compressor(fileobj, comp):
    """Return a writable stream that compresses into fileobj (which it must NOT close)."""
    if comp == "none":
        return fileobj, False  # (stream, owns_close)
    if comp == "gzip":
        return gzip.GzipFile(fileobj=fileobj, mode="wb", compresslevel=6, mtime=0), True
    if comp == "xz":
        return lzma.LZMAFile(fileobj, mode="wb"), True
    if comp == "zstd":
        raise SystemExit("zstd compression needs the 'zstandard' package; use --compression gzip")
    raise SystemExit("unknown compression: %s" % comp)


def add_tar_bytes(tar, name, data):
    ti = tarfile.TarInfo(name)
    ti.size = len(data)
    ti.mtime = 0
    ti.mode = 0o644
    tar.addfile(ti, io.BytesIO(data))


def add_tar_file(tar, name, path):
    st = os.stat(path)
    ti = tarfile.TarInfo(name)
    ti.size = st.st_size
    ti.mtime = 0
    ti.mode = 0o644
    with open(path, "rb") as fh:
        tar.addfile(ti, fh)  # streamed, not loaded into memory


def main():
    ap = argparse.ArgumentParser(description="Assemble a DroidVM .vmpkg (offline, stdlib-only).")
    ap.add_argument("--qcow2", required=True, help="path to the built qcow2 disk")
    ap.add_argument("--config", required=True, help="vms.json: the VM config (manifest 'vm' object)")
    ap.add_argument("--out", required=True, help="output .vmpkg path")
    ap.add_argument("--disk-name", default=None, help="disk filename inside the package (default: qcow2 basename)")
    ap.add_argument("--disk-format", default="qcow2", help="disk format field (default: qcow2)")
    ap.add_argument("--compression", default="gzip", choices=["none", "gzip", "xz"],
                    help="data-blob compression (default: gzip; stdlib-only)")
    ap.add_argument("--app-version", default="0.0", help="manifest app_version string")
    ap.add_argument("--app-version-code", type=int, default=1, help="manifest app_version_code (u16)")
    args = ap.parse_args()

    for p in (args.qcow2, args.config):
        if not os.path.isfile(p):
            raise SystemExit("not found: %s" % p)

    with open(args.config, "r", encoding="utf-8") as fh:
        vm = json.load(fh)
    if not isinstance(vm, dict):
        raise SystemExit("vms.json must be a JSON object (the VM config)")
    # The exporter removes vm.disks (disks are promoted to the top level and rebuilt on import).
    vm.pop("disks", None)
    vm.pop("id", None)

    disk_name = args.disk_name or os.path.basename(args.qcow2)
    archive_path = sanitize_basename(disk_name)
    disk_size = os.path.getsize(args.qcow2)

    disk_entry = {
        "archive_path": archive_path,
        "name": disk_name,
        "path": "",
        "format": args.disk_format,
        "size": disk_size,
        "readonly": False,
        "bus": "virtio",
    }

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "format": "vmpkg",
        "created_at": int(time.time() * 1000),
        "app_version": args.app_version,
        "app_version_code": args.app_version_code,
        "app_build_type": "release",
        "compression": COMPRESSION_NAME[args.compression],
        "vm": vm,
        "disks": [disk_entry],
        "boots": [],
        "networks": [],
    }
    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
    if len(manifest_bytes) > 0xFFFF:
        raise SystemExit("manifest too large: %d bytes (>64KiB)" % len(manifest_bytes))

    comp_id = COMPRESSION_ID[args.compression]

    with open(args.out, "wb") as f:
        f.write(b"\x00" * HEADER_SIZE)                       # header placeholder
        write_zero(f, align_up(f.tell()) - f.tell())         # pad to 0x1000
        assert f.tell() == ALIGN, f.tell()
        f.write(manifest_bytes)                              # authoritative manifest
        write_zero(f, align_up_strict(f.tell()) - f.tell())  # pad to alignUpStrict
        data_start = f.tell()
        assert data_start % ALIGN == 0, data_start

        stream, owns = open_compressor(f, args.compression)
        tar = tarfile.open(fileobj=stream, mode="w", format=tarfile.GNU_FORMAT)
        add_tar_bytes(tar, MANIFEST_NAME, manifest_bytes)    # redundant copy (import ignores it)
        add_tar_file(tar, archive_path, args.qcow2)          # the disk, streamed
        tar.close()
        if owns:
            stream.close()                                   # flush compression trailer into f
        f.flush()

        data_end = f.tell()
        data_size = data_end - data_start
        write_zero(f, align_up(data_end) - data_end)         # trailing pad
        f.truncate(f.tell())                                 # no bytes beyond the padding
        f.seek(0)
        f.write(build_header(len(manifest_bytes), data_size, comp_id, args.app_version_code))

    total = os.path.getsize(args.out)
    print("[vmpkg] wrote %s" % args.out)
    print("[vmpkg]   disk=%s (%.2f GiB)  compression=%s  data=%.2f GiB  package=%.2f GiB"
          % (archive_path, disk_size / 2**30, args.compression, data_size / 2**30, total / 2**30))
    print("[vmpkg]   vm: memory_mb=%s cpu_count=%s swiotlb_mb=%s"
          % (vm.get("memory_mb"), vm.get("cpu_count"), vm.get("swiotlb_mb")))


if __name__ == "__main__":
    main()
