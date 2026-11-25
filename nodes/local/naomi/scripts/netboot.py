#!/usr/bin/env python3
"""
netboot.py -- load a game into a board's net DIMM over the LAN and boot it.

    python3 netboot.py <board ip> <game file>
    python3 - <board ip> <game file>   < netboot.py     (how Pluto runs it on a node over SSH)

The game is a flat .bin. A .zip or .7z holding exactly one .bin is unpacked first; anything
else is refused: this script decompresses, it never converts. A send replaces whatever runs
on the board: the DIMM reboots into its loading screen, takes the game, checks its CRC and
boots it. The transfer is unencrypted (the zero key), which needs no crypto library.

Protocol as implemented by DragonMinded's netdimmutils (public domain): TCP 10703, a 4-byte
little-endian header per packet (id << 24 | flags << 16 | length), then the data.

Every line printed is progress for the caller's log. Exit 0 = booted, 1 = the board did not
take it, 2 = the file can't be sent.

Pure stdlib, 3.6-safe, ASCII only.
"""
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib

PORT = 10703
CHUNK = 0x8000
TIMEOUT = 15                 # seconds per reply; the DIMM is slow while it resets the board
READ_LIMIT = 10.0            # a single read that takes longer than this has failed

PKT_NOP = 0x01
PKT_UPLOAD = 0x04
PKT_HOST_MODE = 0x07
PKT_RESTART = 0x0A
PKT_TIME_LIMIT = 0x17
PKT_INFO = 0x18
PKT_SET_INFO = 0x19
PKT_KEY = 0x7F

ADDR_GAME_INFO = 0xFFFF0000  # length + CRC stamp of the loaded game
ADDR_CRC_CHECK = 0xFFFEFFF0  # zeros = check the CRC before booting


class BoardError(Exception):
    """The board did not answer, or answered something unexpected."""


class FileError(Exception):
    """The file is not something this script can send."""


def say(line):
    print(line)
    sys.stdout.flush()


# ---------------------------------------------------------------------------------------------
# The game file: a .bin, or an archive holding exactly one
# ---------------------------------------------------------------------------------------------
def _one_bin(names, what):
    bins = [n for n in names if n.lower().endswith(".bin") and not os.path.basename(n).startswith(".")]
    if len(bins) != 1:
        raise FileError("%s holds %d .bin files, not one" % (what, len(bins)))
    return bins[0]


def unpack(path, workdir):
    """The .bin to send: path itself, or the one .bin inside a .zip / .7z, unpacked to workdir."""
    name = os.path.basename(path)
    low = name.lower()
    if low.endswith(".bin"):
        return path
    if low.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            member = _one_bin([i for i in z.namelist() if not i.endswith("/")], name)
            z.extract(member, workdir)
        return os.path.join(workdir, member)
    if low.endswith(".7z"):
        exe = shutil.which("7zr") or shutil.which("7z") or shutil.which("7za")
        if not exe:
            raise FileError("no 7z tool here to unpack %s" % name)
        r = subprocess.run([exe, "x", "-y", "-o" + workdir, path], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
        if r.returncode != 0:
            raise FileError("could not unpack %s" % name)
        found = [os.path.relpath(os.path.join(d, f), workdir) for d, _, fs in os.walk(workdir) for f in fs]
        return os.path.join(workdir, _one_bin(found, name))
    raise FileError("%s is not a .bin: netboot takes a flat .bin (decompressed, never converted)" % name)


# ---------------------------------------------------------------------------------------------
# The DIMM
# ---------------------------------------------------------------------------------------------
class Dimm(object):
    def __init__(self, ip, timeout=TIMEOUT):
        self.ip = ip
        self.timeout = timeout
        self.sock = None

    def __enter__(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sys.platform == "darwin":
                # macOS does not honour a timeout changed after connect (netdimmutils' note)
                s.settimeout(self.timeout)
                s.connect((self.ip, PORT))
            else:
                s.settimeout(1)
                s.connect((self.ip, PORT))
                s.settimeout(self.timeout)
        except Exception as exc:
            s.close()
            raise BoardError("no board answering on %s:%d (%s)" % (self.ip, PORT, exc))
        self.sock = s
        self.send(PKT_NOP)
        return self

    def __exit__(self, *exc):
        try:
            self.sock.close()
        finally:
            self.sock = None

    def send(self, pktid, flags=0, data=b""):
        header = ((pktid & 0xFF) << 24) | ((flags & 0xFF) << 16) | (len(data) & 0xFFFF)
        try:
            self.sock.sendall(struct.pack("<I", header) + data)
        except Exception as exc:
            raise BoardError("could not send to the board (%s)" % exc)

    def _read(self, n):
        out, start = b"", time.time()
        while len(out) < n:
            if time.time() - start > READ_LIMIT:
                raise BoardError("the board stopped answering")
            try:
                got = self.sock.recv(n - len(out))
            except Exception as exc:
                raise BoardError("the board stopped answering (%s)" % exc)
            if not got:
                raise BoardError("the board closed the connection")
            out += got
        return out

    def recv(self, want_id, want_len):
        header = struct.unpack("<I", self._read(4))[0]
        pktid, length = (header >> 24) & 0xFF, header & 0xFFFF
        data = self._read(length) if length else b""
        if pktid != want_id or length != want_len:
            raise BoardError("unexpected reply 0x%02x (%d bytes) to 0x%02x" % (pktid, length, want_id))
        return data

    def upload(self, sequence, addr, data, last):
        self.send(PKT_UPLOAD, 0x81 if last else 0x80, struct.pack("<IIH", sequence, addr, 0) + data)

    def info(self):
        """-> (firmware "3.17", game memory in bytes, loaded game's CRC)."""
        self.send(PKT_INFO)
        _unknown, version, game_mb, _dimm_mb, crc = struct.unpack("<HHHHI", self.recv(PKT_INFO, 12))
        return "%x.%02x" % ((version >> 8) & 0xFF, version & 0xFF), game_mb << 20, crc

    def host_mode(self, mode):
        self.send(PKT_HOST_MODE, 0, struct.pack("<I", mode & 0xFF))
        self.recv(PKT_HOST_MODE, 4)


def netboot(ip, path, log=say):
    """Send the .bin at path to the board at ip and boot it."""
    total = os.path.getsize(path)
    if total == 0:
        raise FileError("%s is empty" % os.path.basename(path))
    with Dimm(ip) as d:
        firmware, room, _ = d.info()
        log("board %s: net DIMM firmware %s, %dMB for games" % (ip, firmware, room >> 20))
        if room and total > room:
            raise FileError("%s is %dMB, the DIMM holds %dMB" % (os.path.basename(path), total >> 20, room >> 20))
        d.host_mode(1)                                  # reboot into "NOW LOADING"
        d.send(PKT_KEY, 0, b"\x00" * 8)                 # zero key: no encryption
        d.upload(1, ADDR_CRC_CHECK, b"\x00" * 16, True)  # check the CRC before booting
        d.upload(1, ADDR_GAME_INFO, b"\x00" * 32, False)  # forget the old game first
        crc, sent, sequence, shown = 0, 0, 2, -1
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(CHUNK), b""):
                d.upload(sequence, sent, chunk, sent + len(chunk) == total)
                crc = zlib.crc32(chunk, crc)
                sent += len(chunk)
                sequence += 1
                pct = sent * 100 // total
                if pct // 10 != shown:
                    shown = pct // 10
                    log("sent %d%% (%dMB of %dMB)" % (pct, sent >> 20, total >> 20))
        d.send(PKT_SET_INFO, 0, struct.pack("<III", (~crc) & 0xFFFFFFFF, total, 0))
    with Dimm(ip) as d:
        d.send(PKT_RESTART)                             # boots the game once its CRC checks out
        d.send(PKT_TIME_LIMIT, 0, struct.pack("<I", 10))
    log("booting %s" % os.path.basename(path))


def main(argv):
    if len(argv) != 2:
        say("usage: netboot.py <board ip> <game file>")
        return 2
    ip, path = argv
    if not os.path.isfile(path):
        say("FAILED %s: no such file" % path)
        return 2
    work = tempfile.mkdtemp(prefix="netboot-")
    try:
        try:
            game = unpack(path, work)
            if game != path:
                say("unpacked %s" % os.path.basename(game))
            netboot(ip, game)
        except FileError as exc:
            say("FAILED %s" % exc)
            return 2
        except BoardError as exc:
            say("FAILED %s" % exc)
            return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
