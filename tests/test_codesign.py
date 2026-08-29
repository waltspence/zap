"""Team ID extraction from a synthetic Mach-O code signature."""

from __future__ import annotations

import struct
import unittest

from tests.util import TempTree, make_app_bundle
from zap.core import codesign


def build_signed_macho(team: str, cd_version: int = 0x20400) -> bytes:
    # CodeDirectory: fixed 52-byte header (through teamOffset), then strings.
    ident = b"com.acme.Widget\x00"
    team_b = team.encode() + b"\x00"
    ident_off = 52
    team_off = ident_off + len(ident)
    cd_len = team_off + len(team_b)
    cd = struct.pack(
        ">IIIIIIIIIBBBBII",
        0xFADE0C02, cd_len, cd_version, 0, 0, ident_off, 0, 0, 0, 32, 2, 0, 12, 0, 0,
    )
    cd += struct.pack(">I", team_off) + ident + team_b
    assert len(cd) == cd_len
    # SuperBlob with one slot pointing at the CodeDirectory.
    sb = struct.pack(">IIIII", 0xFADE0CC0, 20 + len(cd), 1, 0, 20) + cd
    # 64-bit Mach-O header + one LC_CODE_SIGNATURE load command.
    lc = struct.pack("<IIII", 0x1D, 16, 32 + 16, len(sb))
    hdr = struct.pack("<IIIIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, 1, len(lc), 0, 0)
    return hdr + lc + sb


class TeamIdTests(unittest.TestCase):
    def test_reads_team_id(self):
        self.assertEqual(codesign._team_id_from_bytes(build_signed_macho("ABCDE12345")), "ABCDE12345")

    def test_old_codedirectory_has_no_team(self):
        self.assertIsNone(codesign._team_id_from_bytes(build_signed_macho("ABCDE12345", cd_version=0x20100)))

    def test_fat_binary(self):
        thin = build_signed_macho("ZZZZZ99999")
        fat = struct.pack(">II", 0xCAFEBABE, 1) + struct.pack(">IIIII", 0x0100000C, 0, 8 + 20, len(thin), 0) + thin
        self.assertEqual(codesign._team_id_from_bytes(fat), "ZZZZZ99999")

    def test_garbage_is_none(self):
        self.assertIsNone(codesign._team_id_from_bytes(b"\x00" * 100))
        self.assertIsNone(codesign._team_id_from_bytes(b"MZ"))

    def test_bundle_lookup(self):
        with TempTree() as root:
            app = make_app_bundle(root / "Applications", "Widget", "com.acme.Widget")
            (app / "Contents" / "MacOS" / "Widget").write_bytes(build_signed_macho("TEAMID0001"))
            self.assertEqual(codesign.team_id_for_bundle(app), "TEAMID0001")
            app2 = make_app_bundle(root / "Applications", "Plain", "com.acme.Plain")
            self.assertIsNone(codesign.team_id_for_bundle(app2))


if __name__ == "__main__":
    unittest.main()
