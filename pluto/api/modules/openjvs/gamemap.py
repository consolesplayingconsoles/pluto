"""OpenJVS game maps, read for display: which game map OpenJVS runs and what it maps.

OpenJVS keeps its own config on the node that runs it (/etc/openjvs): `config` names the
game map in use (DEFAULT_GAME), and games/<name> lays OpenJVS's generic controller out for
that game, one line per input:

    CONTROLLER_BUTTON_D CONTROLLER_1 COIN PLAYER_1        a button, for a player (or SYSTEM)
    CONTROLLER_ANALOGUE_X CONTROLLER_1 ANALOGUE_1          an axis
    INCLUDE generic                                        another map's lines, here

This module does no I/O: the caller passes read(name) -> the text of games/<name>, so the
same code reads over SSH or from a test's dict. READ ONLY: nothing here changes a map.

Pure stdlib, 3.6-safe, ASCII only.
"""

CONFIG = "/etc/openjvs/config"
GAMES = "/etc/openjvs/games"
MAX_DEPTH = 8                  # INCLUDE chains in the stock maps are two deep; a loop stops here


def default_game(config_text):
    """The DEFAULT_GAME line of OpenJVS's config, or "" when it has none."""
    for line in (config_text or "").splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) >= 2 and parts[0] == "DEFAULT_GAME":
            return parts[1]
    return ""


def resolve(name, read, depth=0, seen=None):
    """-> [{"input", "controller", "output", "player", "file"}] for games/<name>, with each
    INCLUDE's lines in its place. read(name) -> the file's text; an unreadable include is
    one row whose output says so, never an exception."""
    seen = set() if seen is None else seen
    if depth > MAX_DEPTH or name in seen:
        return [{"input": "INCLUDE", "controller": "", "output": "%s (loop, not followed)" % name, "player": "", "file": name}]
    seen = seen | {name}
    try:
        text = read(name)
    except Exception as exc:
        return [{"input": "INCLUDE", "controller": "", "output": "%s (unreadable: %s)" % (name, exc), "player": "", "file": name}]
    rows = []
    for line in text.splitlines():
        parts = line.split("#", 1)[0].split()
        if not parts:
            continue
        if parts[0] == "INCLUDE" and len(parts) >= 2:
            rows += resolve(parts[1], read, depth + 1, seen)
        elif len(parts) >= 3:
            rows.append({"input": parts[0], "controller": parts[1], "output": parts[2],
                         "player": parts[3] if len(parts) >= 4 else "", "file": name})
    return rows
