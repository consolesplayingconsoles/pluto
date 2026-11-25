#!/usr/bin/env python3
"""Tests for netboot.py against a fake board on localhost (no hardware).

    python3 -m unittest nodes/local/naomi/scripts/test_netboot.py
"""
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import unittest
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netboot  # noqa: E402


class FakeBoard(object):
    """Answers the packets netboot.py sends the way a net DIMM does, and records them."""

    def __init__(self, game_mb=256):
        self.game_mb = game_mb
        self.packets = []          # (connection no, id, flags, data)
        self.memory = {}           # upload address -> bytes
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        self.conns = 0
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _read(self, c, n):
        out = b""
        while len(out) < n:
            got = c.recv(n - len(out))
            if not got:
                return None
            out += got
        return out

    def _reply(self, c, pktid, data):
        c.sendall(struct.pack("<I", (pktid << 24) | len(data)) + data)

    def _serve(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            self.conns += 1
            with c:
                while True:
                    h = self._read(c, 4)
                    if h is None:
                        break
                    word = struct.unpack("<I", h)[0]
                    pktid, flags, length = word >> 24, (word >> 16) & 0xFF, word & 0xFFFF
                    data = self._read(c, length) if length else b""
                    self.packets.append((self.conns, pktid, flags, data))
                    if pktid == netboot.PKT_INFO:
                        self._reply(c, pktid, struct.pack("<HHHHI", 0xC, 0x0317, self.game_mb, 512, 0))
                    elif pktid == netboot.PKT_HOST_MODE:
                        self._reply(c, pktid, struct.pack("<I", data[0]))
                    elif pktid == netboot.PKT_UPLOAD:
                        _seq, addr, _ = struct.unpack("<IIH", data[:10])
                        self.memory[addr] = data[10:]

    def close(self):
        self.srv.close()

    def ids(self):
        return [p[1] for p in self.packets]


class NetbootTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.board = FakeBoard()
        netboot.PORT = self.board.port

    def tearDown(self):
        self.board.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def game(self, name="game.bin", size=3 * netboot.CHUNK + 100):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(bytes(i & 0xFF for i in range(size)))
        return path

    def test_sends_the_game_and_boots_it(self):
        path = self.game()
        with open(path, "rb") as f:
            data = f.read()
        lines = []
        netboot.netboot("127.0.0.1", path, log=lines.append)
        # the game lands in memory from address 0, chunk by chunk, the last one flagged
        game = b"".join(self.board.memory[a] for a in sorted(a for a in self.board.memory if a < 0xF0000000))
        self.assertEqual(game, data)
        uploads = [p for p in self.board.packets if p[1] == netboot.PKT_UPLOAD and
                   struct.unpack("<IIH", p[3][:10])[1] < 0xF0000000]
        self.assertEqual([p[2] for p in uploads], [0x80, 0x80, 0x80, 0x81])
        # stamped with the inverted zlib CRC and the length
        stamp = [p[3] for p in self.board.packets if p[1] == netboot.PKT_SET_INFO][0]
        self.assertEqual(struct.unpack("<III", stamp), ((~zlib.crc32(data)) & 0xFFFFFFFF, len(data), 0))
        # unencrypted, CRC check on, then a second connection restarts the board into the game
        self.assertEqual([p[3] for p in self.board.packets if p[1] == netboot.PKT_KEY], [b"\x00" * 8])
        self.assertEqual(self.board.memory[netboot.ADDR_CRC_CHECK], b"\x00" * 16)
        self.assertEqual([p[0] for p in self.board.packets if p[1] == netboot.PKT_RESTART], [2])
        self.assertIn("booting game.bin", lines)
        self.assertTrue(all(l.encode("ascii") for l in lines))

    def test_host_mode_comes_before_the_upload(self):
        netboot.netboot("127.0.0.1", self.game(), log=lambda l: None)
        ids = self.board.ids()
        self.assertLess(ids.index(netboot.PKT_HOST_MODE), ids.index(netboot.PKT_UPLOAD))

    def test_refuses_a_game_bigger_than_the_dimm(self):
        self.board.game_mb = 0          # 0 = unknown: no check
        netboot.netboot("127.0.0.1", self.game(), log=lambda l: None)
        small = FakeBoard(game_mb=1)
        netboot.PORT = small.port
        try:
            with self.assertRaises(netboot.FileError):
                netboot.netboot("127.0.0.1", self.game(size=(1 << 20) + 1), log=lambda l: None)
        finally:
            small.close()

    def test_no_board(self):
        self.board.close()
        with self.assertRaises(netboot.BoardError):
            netboot.netboot("127.0.0.1", self.game(), log=lambda l: None)

    def test_unpacks_a_zip_holding_one_bin(self):
        z = os.path.join(self.tmp, "game.zip")
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("game.bin", b"abc")
            f.writestr("readme.txt", b"x")
        work = os.path.join(self.tmp, "w")
        os.mkdir(work)
        with open(netboot.unpack(z, work), "rb") as f:
            self.assertEqual(f.read(), b"abc")

    def test_refuses_a_zip_of_rom_chips(self):
        z = os.path.join(self.tmp, "mame.zip")
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("mpr-1.ic22", b"a")
            f.writestr("mpr-2.ic1", b"b")
        with self.assertRaises(netboot.FileError):
            netboot.unpack(z, self.tmp)

    def test_refuses_anything_but_a_bin(self):
        for name in ("game.chd", "game.dat", "game.lst"):
            with self.assertRaises(netboot.FileError):
                netboot.unpack(self.game(name), self.tmp)

    def test_main_exit_codes(self):
        self.assertEqual(netboot.main(["127.0.0.1", self.game()]), 0)
        self.assertEqual(netboot.main(["127.0.0.1", self.game("game.chd")]), 2)
        self.assertEqual(netboot.main(["127.0.0.1", os.path.join(self.tmp, "missing.bin")]), 2)
        self.board.close()
        self.assertEqual(netboot.main(["127.0.0.1", self.game()]), 1)


if __name__ == "__main__":
    unittest.main()
