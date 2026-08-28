"""Read the Team Identifier out of a Mach-O code signature, stdlib only.

macOS keys several per-app locations by ``<TEAMID>.<bundle-id>`` (hardened
runtime caches, group containers, ``~/Library/Group Containers/<TEAMID>.*``),
so without the Team ID those leftovers are invisible. The id lives in the
CodeDirectory blob of the embedded signature:

    Mach-O header -> LC_CODE_SIGNATURE -> SuperBlob -> CodeDirectory
    (version >= 0x20200 carries ``teamOffset``)

We parse just enough of that to pull the string out. Everything here is
best-effort: any malformed or unsigned binary yields ``None``; nothing is
executed and no subprocess is spawned.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Optional

from . import identifiers as I
from . import plist

_MH_MAGIC_64 = 0xFEEDFACF
_MH_CIGAM_64 = 0xCFFAEDFE
_MH_MAGIC = 0xFEEDFACE
_MH_CIGAM = 0xCEFAEDFE
_FAT_MAGIC = 0xCAFEBABE
_FAT_CIGAM = 0xBEBAFECA
_LC_CODE_SIGNATURE = 0x1D
_CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
_CSMAGIC_CODEDIRECTORY = 0xFADE0C02
_CSSLOT_CODEDIRECTORY = 0
_MAX_LOAD_COMMANDS = 1024 * 1024  # sanity cap on the load-command table
_MAX_SIGNATURE = 16 * 1024 * 1024  # sanity cap on the signature blob


def team_id_for_bundle(bundle_dir: Path) -> Optional[str]:
    """Team ID of the bundle's main executable, or ``None``."""
    info = plist.read_bundle_info(bundle_dir)
    exe = info.get("CFBundleExecutable")
    if not isinstance(exe, str) or not I.is_safe_name_component(exe):
        return None
    binary = bundle_dir / "Contents" / "MacOS" / exe
    try:
        if not binary.is_file():
            return None
    except OSError:
        return None
    return team_id_for_binary(binary)


def team_id_for_binary(path: Path) -> Optional[str]:
    try:
        with open(path, "rb") as fh:
            return _team_id_from_file(fh)
    except (OSError, struct.error):
        return None


def _team_id_from_bytes(data: bytes) -> Optional[str]:
    import io

    try:
        return _team_id_from_file(io.BytesIO(data))
    except struct.error:
        return None


def _team_id_from_file(fh) -> Optional[str]:
    if True:
        if True:
            head = fh.read(8)
            if len(head) < 8:
                return None
            magic = struct.unpack(">I", head[:4])[0]
            if magic in (_FAT_MAGIC, _FAT_CIGAM):
                # Universal binary: try each slice, first hit wins.
                nfat = struct.unpack(">I", head[4:8])[0]
                if nfat > 32:
                    return None
                for i in range(nfat):
                    fh.seek(8 + i * 20)
                    arch = fh.read(20)
                    if len(arch) < 20:
                        return None
                    offset, _size = struct.unpack(">II", arch[8:16])
                    tid = _team_id_from_macho(fh, offset)
                    if tid:
                        return tid
                return None
            return _team_id_from_macho(fh, 0)


def _team_id_from_macho(fh, base: int) -> Optional[str]:
    """Walk the load commands at ``base`` and read only the signature blob."""
    fh.seek(base)
    hdr = fh.read(32)
    if len(hdr) < 32:
        return None
    magic = struct.unpack("<I", hdr[:4])[0]
    if magic in (_MH_MAGIC_64, _MH_MAGIC):
        end = "<"
    elif magic in (_MH_CIGAM_64, _MH_CIGAM):
        end = ">"
    else:
        return None
    is64 = magic in (_MH_MAGIC_64, _MH_CIGAM_64)
    header_size = 32 if is64 else 28
    ncmds, sizeofcmds = struct.unpack(end + "II", hdr[16:24])
    if ncmds > 4096 or sizeofcmds > _MAX_LOAD_COMMANDS:
        return None
    fh.seek(base + header_size)
    cmds = fh.read(sizeofcmds)
    off = 0
    for _ in range(ncmds):
        if off + 8 > len(cmds):
            return None
        cmd, cmdsize = struct.unpack(end + "II", cmds[off : off + 8])
        if cmdsize < 8 or off + cmdsize > len(cmds):
            return None
        if cmd == _LC_CODE_SIGNATURE and cmdsize >= 16:
            dataoff, datasize = struct.unpack(end + "II", cmds[off + 8 : off + 16])
            if datasize < 12 or datasize > _MAX_SIGNATURE:
                return None
            fh.seek(base + dataoff)
            return _team_id_from_superblob(fh.read(datasize))
        off += cmdsize
    return None


def _team_id_from_superblob(blob: bytes) -> Optional[str]:
    # SuperBlob is big-endian: magic, length, count, then (type, offset) pairs.
    magic, length, count = struct.unpack(">III", blob[:12])
    if magic != _CSMAGIC_EMBEDDED_SIGNATURE or count > 64 or length > len(blob):
        return None
    for i in range(count):
        base = 12 + i * 8
        if base + 8 > len(blob):
            return None
        slot, offset = struct.unpack(">II", blob[base : base + 8])
        if slot == _CSSLOT_CODEDIRECTORY:
            return _team_id_from_codedirectory(blob[offset:])
    return None


def _team_id_from_codedirectory(cd: bytes) -> Optional[str]:
    if len(cd) < 52:
        return None
    magic, length, version = struct.unpack(">III", cd[:12])
    if magic != _CSMAGIC_CODEDIRECTORY or version < 0x20200 or length > len(cd):
        return None
    team_offset = struct.unpack(">I", cd[48:52])[0]
    if team_offset == 0 or team_offset >= length:
        return None
    end = cd.find(b"\x00", team_offset)
    if end < 0:
        return None
    try:
        tid = cd[team_offset:end].decode("ascii")
    except UnicodeDecodeError:
        return None
    return tid if I.is_valid_team_id(tid) else None
