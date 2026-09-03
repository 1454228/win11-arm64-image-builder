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

gzip runs multi-threaded (pigz-style, see ParallelGzipWriter): the output is still ONE ordinary
gzip member, so the app's plain java.util.zip.GZIPInputStream reads it unchanged.

Usage:
  pack-vmpkg.py --qcow2 out.qcow2 --config vms.json --out win11.vmpkg
                [--disk-name win11.qcow2] [--compression gzip|none|xz] [--threads N]
                [--app-version 1.0] [--app-version-code 1]
"""
import argparse
import collections
import concurrent.futures
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
import zlib

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


# --- multi-threaded gzip ----------------------------------------------------------------
# The same trick pigz uses. The input is cut into fixed chunks; every chunk is deflated on its
# own thread as a RAW deflate stream (wbits=-15) ended with Z_SYNC_FLUSH (an empty stored block,
# no BFINAL), and the pieces are concatenated in order. Raw deflate blocks are self-delimiting,
# so the concatenation is one valid deflate stream; a final empty BFINAL block terminates it,
# and a normal gzip header/trailer wraps the whole thing -- ONE member, readable by any gzip
# decoder. This matters here: the app reads with GZIPInputStream over a LimitedInputStream
# whose available() is 0, and in that configuration Java's concatenated-member detection is
# a heuristic (>26 leftover bytes) that fails on ~0.04% of member boundaries -- over the
# thousands of chunks in a multi-GB disk that is most imports. Each chunk is primed (zdict) with the previous
# chunk's last 32 KiB; the decoder's window already holds those bytes, so back-references
# across the cut resolve and the ratio matches plain single-threaded gzip.
# zlib releases the GIL inside deflate()/crc32(), so plain threads scale to real cores.
GZIP_HEADER = b"\x1f\x8b\x08\x00" + b"\x00\x00\x00\x00" + b"\x00\xff"  # mtime 0, xfl 0, OS unknown
GZIP_WINDOW = 32768
DEFLATE_FINAL_EMPTY_BLOCK = zlib.compressobj(6, zlib.DEFLATED, -15).flush(zlib.Z_FINISH)  # b"\x03\x00"


def _deflate_chunk(chunk, level, zdict):
    if zdict:
        c = zlib.compressobj(level, zlib.DEFLATED, -15, 8, zlib.Z_DEFAULT_STRATEGY, zdict)
    else:
        c = zlib.compressobj(level, zlib.DEFLATED, -15)
    return c.compress(chunk) + c.flush(zlib.Z_SYNC_FLUSH), zlib.crc32(chunk)


def _gf2_matrix_times(mat, vec):
    s = 0
    i = 0
    while vec:
        if vec & 1:
            s ^= mat[i]
        vec >>= 1
        i += 1
    return s


def _gf2_matrix_square(mat):
    return [_gf2_matrix_times(mat, mat[n]) for n in range(32)]


def _crc32_shift_operator(len2):
    """The GF(2) matrix that advances a CRC-32 over len2 zero bytes (zlib's crc32_combine)."""
    odd = [0xEDB88320] + [1 << n for n in range(31)]   # CRC-32 polynomial, then the shifts
    even = _gf2_matrix_square(odd)                       # operator for 2 zero bits
    odd = _gf2_matrix_square(even)                       # operator for 4 zero bits
    op = None                                            # identity until the first 1 bit
    while len2:
        even = _gf2_matrix_square(odd)                   # 8, 32, 128, ... zero bits
        if len2 & 1:
            op = even if op is None else [_gf2_matrix_times(even, op[n]) for n in range(32)]
        len2 >>= 1
        if not len2:
            break
        odd = _gf2_matrix_square(even)                   # 16, 64, 256, ... zero bits
        if len2 & 1:
            op = odd if op is None else [_gf2_matrix_times(odd, op[n]) for n in range(32)]
        len2 >>= 1
    return op


_CRC_OPS = {}


def crc32_combine(crc1, crc2, len2):
    """crc32(A + B) from crc32(A), crc32(B) and len(B). Operators are cached per len2, so the
    fixed chunk size costs one 32x32 matrix build, then ~microseconds per chunk."""
    if len2 <= 0:
        return crc1
    op = _CRC_OPS.get(len2)
    if op is None:
        op = _CRC_OPS[len2] = _crc32_shift_operator(len2)
    return _gf2_matrix_times(op, crc1) ^ crc2


class ParallelGzipWriter(object):
    """A write()/close() stream that gzips into fileobj (which it does not close) using
    `threads` worker threads. See the block comment above for the format."""

    def __init__(self, fileobj, level=6, threads=None, chunk_size=4 << 20):
        self.f = fileobj
        self.level = level
        self.chunk_size = chunk_size
        self.threads = max(1, threads or os.cpu_count() or 1)
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=self.threads)
        self.window = 2 * self.threads                   # chunks in flight (bounds memory)
        self.pending = collections.deque()               # (future, chunk_len) in input order
        self.buf = bytearray()
        self.tail = b""                                  # last 32 KiB of the previous chunk
        self.crc = 0
        self.size = 0                                    # uncompressed bytes emitted
        self.pos = 0                                     # uncompressed bytes accepted
        self.closed = False
        self.f.write(GZIP_HEADER)

    def tell(self):
        return self.pos                                  # tarfile reads this once, at open()

    def write(self, data):
        self.buf += data
        self.pos += len(data)
        while len(self.buf) >= self.chunk_size:
            chunk = bytes(self.buf[:self.chunk_size])
            del self.buf[:self.chunk_size]
            self._submit(chunk)
        return len(data)

    def _submit(self, chunk):
        while len(self.pending) >= self.window:
            self._drain_one()
        self.pending.append((self.pool.submit(_deflate_chunk, chunk, self.level, self.tail),
                             len(chunk)))
        self.tail = chunk[-GZIP_WINDOW:]

    def _drain_one(self):
        fut, n = self.pending.popleft()
        out, crc = fut.result()
        self.f.write(out)
        self.crc = crc32_combine(self.crc, crc, n)
        self.size += n

    def flush(self):
        pass

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.buf:
            self._submit(bytes(self.buf))
            self.buf = bytearray()
        while self.pending:
            self._drain_one()
        self.pool.shutdown()
        self.f.write(DEFLATE_FINAL_EMPTY_BLOCK)
        self.f.write(struct.pack("<II", self.crc & 0xFFFFFFFF, self.size & 0xFFFFFFFF))


def open_compressor(fileobj, comp, threads=None):
    """Return a writable stream that compresses into fileobj (which it must NOT close)."""
    if comp == "none":
        return fileobj, False  # (stream, owns_close)
    if comp == "gzip":
        if threads == 1:
            return gzip.GzipFile(fileobj=fileobj, mode="wb", compresslevel=6, mtime=0), True
        return ParallelGzipWriter(fileobj, level=6, threads=threads), True
    if comp == "xz":
        return lzma.LZMAFile(fileobj, mode="wb"), True
    if comp == "zstd":
        raise SystemExit("zstd compression needs the 'zstandard' package; use --compression gzip")
    raise SystemExit("unknown compression: %s" % comp)


class AppTarInfo(tarfile.TarInfo):
    """GNU tar (tarfile, bsdtar) stores an entry size >= 8 GiB in base-256 (0x80 ...), which app builds
    before commit c0dd376 (TarReader without base-256) turned into 0 -> a 0-byte disk on import. The
    app's own TarWriter writes such sizes as 12 octal digits filling the field (no NUL); every app build
    reads that, and so do GNU tar, bsdtar and tarfile. Emit the same up to 64 GiB - 1 (what 12 octal
    digits hold); beyond that keep tarfile's base-256, which needs the fixed app."""

    def tobuf(self, format=tarfile.DEFAULT_FORMAT, encoding=tarfile.ENCODING, errors="surrogateescape"):
        buf = super().tobuf(format, encoding, errors)
        if self.size <= 0o77777777777:
            return buf
        if self.size > 0o777777777777:
            print("[vmpkg] note: %s is %.1f GiB (>= 64 GiB): tar size stays base-256, which needs a DroidVM with "
                  "base-256 tar sizes (app commit c0dd376 or later)" % (self.name, self.size / 2**30))
            return buf
        hdr = bytearray(buf[-512:])
        hdr[124:136] = b"%012o" % self.size
        hdr[148:155] = b"%06o\0" % tarfile.calc_chksums(bytes(hdr))[0]   # byte 155 stays ' '
        return buf[:-512] + bytes(hdr)


def add_tar_bytes(tar, name, data):
    ti = AppTarInfo(name)
    ti.size = len(data)
    ti.mtime = 0
    ti.mode = 0o644
    tar.addfile(ti, io.BytesIO(data))


def add_tar_file(tar, name, path):
    st = os.stat(path)
    ti = AppTarInfo(name)
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
    ap.add_argument("--threads", type=int, default=0,
                    help="gzip worker threads (default 0 = all CPUs; 1 = plain single-threaded gzip)")
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
    # Networks: a NIC in vm.networks may carry its network's definition under "pkg_network". The app's
    # exporter shape is: the definition in the top-level networks[] tagged with the NIC's pkg_network_ref
    # (on import, mode "existing" maps the tag to a device network by name, "auto" creates the network).
    pkg_networks = []
    for nic in vm.get("networks") or []:
        if not isinstance(nic, dict) or "pkg_network" not in nic:
            continue
        net = nic.pop("pkg_network")
        ref = nic.get("pkg_network_ref")
        if not ref:
            raise SystemExit("vms.json: a NIC carrying pkg_network needs a pkg_network_ref")
        net["pkg_network_ref"] = ref
        pkg_networks.append(net)

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
        "networks": pkg_networks,
    }
    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
    if len(manifest_bytes) > 0xFFFF:
        raise SystemExit("manifest too large: %d bytes (>64KiB)" % len(manifest_bytes))

    comp_id = COMPRESSION_ID[args.compression]
    threads = args.threads if args.threads > 0 else (os.cpu_count() or 1)
    t0 = time.time()

    with open(args.out, "wb") as f:
        f.write(b"\x00" * HEADER_SIZE)                       # header placeholder
        write_zero(f, align_up(f.tell()) - f.tell())         # pad to 0x1000
        assert f.tell() == ALIGN, f.tell()
        f.write(manifest_bytes)                              # authoritative manifest
        write_zero(f, align_up_strict(f.tell()) - f.tell())  # pad to alignUpStrict
        data_start = f.tell()
        assert data_start % ALIGN == 0, data_start

        stream, owns = open_compressor(f, args.compression, threads)
        try:   # 4 MiB copy buffer instead of tarfile's 16 KiB default (Python >= 3.7)
            tar = tarfile.open(fileobj=stream, mode="w", format=tarfile.GNU_FORMAT, copybufsize=4 << 20)
        except TypeError:
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

    elapsed = max(time.time() - t0, 1e-6)
    total = os.path.getsize(args.out)
    print("[vmpkg] wrote %s" % args.out)
    print("[vmpkg]   disk=%s (%.2f GiB)  compression=%s  data=%.2f GiB  package=%.2f GiB"
          % (archive_path, disk_size / 2**30, args.compression, data_size / 2**30, total / 2**30))
    print("[vmpkg]   %.1fs, %.0f MB/s in%s"
          % (elapsed, disk_size / 1e6 / elapsed,
             (", %d threads" % threads) if args.compression == "gzip" else ""))
    print("[vmpkg]   vm: memory_mb=%s cpu_count=%s swiotlb_mb=%s"
          % (vm.get("memory_mb"), vm.get("cpu_count"), vm.get("swiotlb_mb")))


if __name__ == "__main__":
    main()
