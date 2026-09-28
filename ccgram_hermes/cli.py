"""`ccgram-hermes run ...` is ccgram with Hermes patched in. `ccgram-hermes release` undoes its state."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def release(config_dir: Path) -> int:
    """Hand every Hermes window back to plain ccgram. Run with the bot stopped."""
    state_path = config_dir / "state.json"
    state = json.loads(state_path.read_text())
    windows = {w: s for w, s in state.get("window_states", {}).items() if s.get("provider_name") == "hermes"}
    sessions = {s.get("session_id") for s in windows.values()} - {""}
    for s in windows.values():
        for key in ("provider_name", "provider_manual_override", "transcript_path"):
            s.pop(key, None)
        if s.get("initial_provider_name") == "hermes":
            s.pop("initial_provider_name")
        s["session_id"] = ""
    for offsets in state.get("user_window_offsets", {}).values():
        for w in windows:
            offsets.pop(w, None)
    _write(state_path, state)

    monitor_path = config_dir / "monitor_state.json"
    if monitor_path.exists():
        monitor = json.loads(monitor_path.read_text())
        tracked = monitor.get("tracked_sessions", {})
        for sid in sessions:
            tracked.pop(sid, None)
        _write(monitor_path, monitor)

    map_path = config_dir / "session_map.json"
    if map_path.exists():
        smap = json.loads(map_path.read_text())
        _write(map_path, {k: v for k, v in smap.items()
                          if not (isinstance(v, dict) and v.get("provider_name") == "hermes")})
    return len(windows)


def main() -> None:
    if sys.argv[1:2] == ["release"]:
        from ccgram.utils import ccgram_dir

        print(f"released {release(ccgram_dir())} hermes window(s)")
        return

    import ccgram.main

    run_bot = ccgram.main.run_bot

    def run_bot_patched() -> None:
        try:
            import ccgram.config  # noqa: F401
        except ValueError:
            return run_bot()  # ccgram prints its own setup help
        from . import patches

        patches.apply()
        return run_bot()

    ccgram.main.run_bot = run_bot_patched
    ccgram.main.main()
