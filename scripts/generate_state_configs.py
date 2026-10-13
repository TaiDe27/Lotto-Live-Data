#!/usr/bin/env python3
"""Generates every lottery-running state/territory's self-contained `data/<folder>/config.json`.

This is the ONE place a human edits when a game's rules/payout/liveMapping/matchRule/ocrRule
data changes (see the iOS app's CLAUDE.md for the full remote-config redesign this is part of).
Each output file is self-contained on purpose (no shared "games catalog" file the app has to
cross-reference) — this script is what keeps that duplication from becoming a maintenance trap:
edit a game's FULL object (pickRule, payoutTable, liveMapping, matchRule, ocrRule, etc.) once in
`state_config_base_games.json`, or a state's game list/routing in `state_config_states.json`, then
re-run this script to regenerate every file consistently.

Reconciled 2026-10-13: earlier versions of this script only knew the original 17-game Arizona/
Kansas pilot (hardcoded Python LIVE_MAPPINGS/MATCH_RULES/STATE_GAMES dicts) and were silently
bypassed by hand-editing real config.json files directly as every state/game added since went in —
confirmed stale 3 separate times in one day before this reconciliation. The fix: instead of
hand-transcribing every game's liveMapping/matchRule as Python literals (which is exactly how this
drifted out of sync the first time), `state_config_base_games.json` now holds each game's COMPLETE
real object (including liveMapping/matchRule/ocrRule) extracted directly from the real, deployed
config.json files — this script is now a thin assembler, not a place that re-encodes game logic.

Usage: python3 scripts/generate_state_configs.py
(no args; writes data/<folder>/config.json for every state listed in state_config_states.json)
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(SCRIPT_DIR, "state_config_base_games.json")) as f:
    BASE_GAMES = json.load(f)

with open(os.path.join(SCRIPT_DIR, "state_config_states.json")) as f:
    STATES = json.load(f)


def build_game(game_id):
    if game_id not in BASE_GAMES:
        raise KeyError(
            f"'{game_id}' is listed in state_config_states.json but has no entry in "
            f"state_config_base_games.json — add its full game object there first."
        )
    return BASE_GAMES[game_id]


def main():
    for state_code, meta in STATES.items():
        config = {
            "schemaVersion": meta.get("schemaVersion", 1),
            "stateCode": state_code,
            "routedCourierPlatform": meta["routedCourierPlatform"],
            "games": [build_game(gid) for gid in meta["games"]],
        }
        out_dir = os.path.join(ROOT, "data", meta["folder"])
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "config.json")
        with open(out_path, "w") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"wrote {out_path} ({len(meta['games'])} games)")


if __name__ == "__main__":
    main()
