#!/usr/bin/env python3
"""Generates each piloted state's self-contained `data/<state>/config.json`.

This is the ONE place a human edits when a game's rules, payout mapping, or buy-link data change
for the Arizona/Kansas pilot (see the iOS app's CLAUDE.md / plan doc for the full redesign this
is part of). The two output files are self-contained on purpose (no shared "games catalog" file
the app has to cross-reference) — this script is what keeps that duplication from becoming a
maintenance trap: edit LIVE_MAPPINGS/MATCH_RULES/STATE_GAMES below (or state_config_base_games.json
for a game's pickRule/payoutTable/etc.) once, then re-run this script to regenerate every state
file that embeds that game consistently.

Usage: python3 scripts/generate_state_configs.py
(no args; writes data/arizona/config.json and data/kansas/config.json in this repo)
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "state_config_base_games.json")) as f:
    BASE_GAMES = json.load(f)

SCHEMA_VERSION = 1

# Multi-state games every piloted state offers, plus each state's own exclusive games.
STATE_GAMES = {
    "AZ": [
        "powerball", "mega-millions", "powerball-double-play", "lotto-america",
        "millionaire-for-life", "powerball-xo", "2by2",
        "az-the-pick", "az-fantasy-5", "az-triple-twist",
        "az-pick3-midday", "az-pick3-evening", "az-pick4-midday", "az-pick4-evening",
    ],
    "KS": [
        "powerball", "mega-millions", "powerball-double-play", "lotto-america",
        "millionaire-for-life", "powerball-xo", "2by2",
        "ks-pick3-midday", "ks-pick3-evening", "ks-super-kansas-cash",
    ],
}

STATE_ROUTED_COURIER_PLATFORM = {
    "AZ": "jackpocket",
    "KS": "jackpotcom",
}

STATE_FOLDER = {"AZ": "arizona", "KS": "kansas"}

EASTERN = "America/New_York"
PHOENIX = "America/Phoenix"
CENTRAL = "America/Chicago"

ML_BASE = "https://cdn.jsdelivr.net/gh/TaiDe27/Lotto-Live-Data@main/data"


def date_rule(field, date_format, tz, anchor_hour=None, anchor_minute=None,
              truncate=None, anchor_by_session=None, session_field=None):
    return {
        "field": field,
        "dateFormat": date_format,
        "truncateToLength": truncate,
        "timeZoneIdentifier": tz,
        "anchorHour": anchor_hour,
        "anchorMinute": anchor_minute,
        "anchorHourBySession": anchor_by_session,
        "sessionField": session_field,
    }


def money_none():
    return {"source": "none", "field": None, "moneyFormat": None, "guardField": None}


def money_direct(field, fmt):
    return {"source": "directField", "field": field, "moneyFormat": fmt, "guardField": None}


def money_lookback(field, guard_field, fmt):
    return {"source": "previousEntryLookback", "field": field, "moneyFormat": fmt, "guardField": guard_field}


def winners_none():
    return None


def winners_dict_by_tier_name(field, remap, count_mode="plain", near_miss=None):
    return {
        "mode": "dictionaryByTierName",
        "field": field,
        "countMode": count_mode,
        "tierKeyRemap": remap,
        "divisionKey": None,
        "tierKeyTemplate": None,
        "nearMissTier": near_miss,
    }


def winners_single_division(field, division_key, tier_key_template, near_miss=None):
    return {
        "mode": "singleDivision",
        "field": field,
        "countMode": None,
        "tierKeyRemap": None,
        "divisionKey": division_key,
        "tierKeyTemplate": tier_key_template,
        "nearMissTier": near_miss,
    }


def near_miss(text_field, contains_text, tier_key_template, count_field):
    return {
        "textField": text_field,
        "containsText": contains_text,
        "tierKeyTemplate": tier_key_template,
        "countField": count_field,
    }


# MARK: - Multi-state games (shared verbatim across every piloted state's config.json)

LIVE_MAPPINGS = {
    "powerball": {
        "sourceURL": f"{ML_BASE}/multistate/powerball/latest.json",
        "cachePath": "live/powerball/latest.json",
        "shape": "numeric",
        "sessionFilter": None,
        "drawDate": date_rule("draw_date", "EEE, MMM d, yyyy", EASTERN, anchor_hour=22, anchor_minute=59),
        "nextDrawDate": date_rule("next_drawing.next_draw_date", "EEE, MMM d, yyyy", EASTERN, anchor_hour=22, anchor_minute=59),
        "mainNumbersField": "white_balls",
        "specialNumbers": {"field": "red_ball", "wrapScalar": True},
        "winningTeamsField": None,
        "jackpot": money_lookback("next_drawing.estimated_jackpot", "next_drawing.next_draw_date", "compactWords"),
        "cashValue": money_lookback("next_drawing.cash_value", "next_drawing.next_draw_date", "compactWords"),
        "nextJackpot": money_direct("next_drawing.estimated_jackpot", "compactWords"),
        "nextCashValue": money_direct("next_drawing.cash_value", "compactWords"),
        "multiplierField": "power_play",
        "winnersByTier": winners_dict_by_tier_name("winners_by_tier", {"Powerball JACKPOT": "{mainCount}+1"}),
    },
    "mega-millions": {
        "sourceURL": f"{ML_BASE}/multistate/mega-millions/latest.json",
        "cachePath": "live/mega-millions/latest.json",
        "shape": "numeric",
        "sessionFilter": None,
        "drawDate": date_rule("draw_date", "yyyy-MM-dd", EASTERN, anchor_hour=23, anchor_minute=0, truncate=10),
        "nextDrawDate": date_rule("next_draw_date", "yyyy-MM-dd", EASTERN, anchor_hour=23, anchor_minute=0, truncate=10),
        "mainNumbersField": "white_balls",
        "specialNumbers": {"field": "mega_ball", "wrapScalar": True},
        "winningTeamsField": None,
        "jackpot": money_direct("current_prize_pool", "decimal"),
        "cashValue": money_direct("current_cash_value", "decimal"),
        "nextJackpot": money_direct("next_prize_pool", "decimal"),
        "nextCashValue": money_direct("next_cash_value", "decimal"),
        "multiplierField": None,
        "winnersByTier": winners_single_division(
            "jackpot_winners", None, "{mainCount}+1",
            near_miss=near_miss("match5_winner_text", "match 5", "{mainCount}+0", "match5_locations"),
        ),
    },
    "powerball-double-play": {
        "sourceURL": f"{ML_BASE}/multistate/powerball-double-play/latest.json",
        "cachePath": "live/powerball-double-play/latest.json",
        "shape": "numeric",
        "sessionFilter": None,
        "drawDate": date_rule("draw_date", "EEE, MMM d, yyyy", EASTERN, anchor_hour=22, anchor_minute=59),
        "nextDrawDate": date_rule("next_drawing.next_draw_date", "EEE, MMM d, yyyy", EASTERN, anchor_hour=22, anchor_minute=59),
        "mainNumbersField": "white_balls",
        "specialNumbers": {"field": "red_ball", "wrapScalar": True},
        "winningTeamsField": None,
        "jackpot": money_direct("next_drawing.top_prize", "compactWords"),
        "cashValue": money_none(),
        "nextJackpot": money_direct("next_drawing.top_prize", "compactWords"),
        "nextCashValue": money_none(),
        "multiplierField": None,
        "winnersByTier": winners_dict_by_tier_name("winners_by_tier", {"Double Play JACKPOT": "{mainCount}+1"}),
    },
    "lotto-america": {
        "sourceURL": f"{ML_BASE}/multistate/lotto-america/latest.json",
        "cachePath": "live/lotto-america/latest.json",
        "shape": "numeric",
        "sessionFilter": None,
        "drawDate": date_rule("draw_date", "EEE, MMM d, yyyy", EASTERN, anchor_hour=22, anchor_minute=15),
        "nextDrawDate": date_rule("next_drawing.next_draw_date", "EEE, MMM d, yyyy", EASTERN, anchor_hour=22, anchor_minute=15),
        "mainNumbersField": "main_balls",
        "specialNumbers": {"field": "star_ball", "wrapScalar": True},
        "winningTeamsField": None,
        "jackpot": money_lookback("next_drawing.estimated_jackpot", "next_drawing.next_draw_date", "compactWords"),
        "cashValue": money_lookback("next_drawing.cash_value", "next_drawing.next_draw_date", "compactWords"),
        "nextJackpot": money_direct("next_drawing.estimated_jackpot", "compactWords"),
        "nextCashValue": money_direct("next_drawing.cash_value", "compactWords"),
        "multiplierField": "all_star_bonus",
        "winnersByTier": winners_dict_by_tier_name("winners_by_tier", {"Jackpot": "{mainCount}+1"}),
    },
    "millionaire-for-life": {
        "sourceURL": f"{ML_BASE}/multistate/millionaire-for-life/latest.json",
        "cachePath": "live/millionaire-for-life/latest.json",
        "shape": "numeric",
        "sessionFilter": None,
        "drawDate": date_rule("draw_date", "EEE, MMM d, yyyy", EASTERN, anchor_hour=23, anchor_minute=15),
        "nextDrawDate": date_rule("next_drawing.next_draw_date", "EEE, MMM d, yyyy", EASTERN, anchor_hour=23, anchor_minute=15),
        "mainNumbersField": "white_balls",
        "specialNumbers": {"field": "millionaire_ball", "wrapScalar": True},
        "winningTeamsField": None,
        "jackpot": money_direct("next_drawing.top_prize", "leadingNumber"),
        "cashValue": money_direct("next_drawing.cash_option", "leadingNumber"),
        "nextJackpot": money_direct("next_drawing.top_prize", "leadingNumber"),
        "nextCashValue": money_direct("next_drawing.cash_option", "leadingNumber"),
        "multiplierField": None,
        "winnersByTier": winners_dict_by_tier_name("winners_by_tier", {"Top Prize": "{mainCount}+1"}),
    },
    "powerball-xo": {
        "sourceURL": f"{ML_BASE}/multistate/powerball-xo/latest.json",
        "cachePath": "live/powerball-xo/latest.json",
        "shape": "teamPick",
        "sessionFilter": None,
        "drawDate": date_rule("draw_date", "EEE, MMM d, yyyy", EASTERN, anchor_hour=22, anchor_minute=0),
        "nextDrawDate": date_rule("next_drawing.next_draw_date", "EEE, MMM d, yyyy", EASTERN, anchor_hour=22, anchor_minute=0),
        "mainNumbersField": None,
        "specialNumbers": None,
        "winningTeamsField": "winning_teams",
        "jackpot": money_lookback("next_drawing.estimated_jackpot", "next_drawing.next_draw_date", "compactWords"),
        "cashValue": money_none(),
        "nextJackpot": money_direct("next_drawing.estimated_jackpot", "compactWords"),
        "nextCashValue": money_none(),
        "multiplierField": None,
        "winnersByTier": winners_dict_by_tier_name(
            "winners_by_tier", {"Match 8 of 8 Teams": "{mainCount}+1"}, count_mode="parenthetical",
        ),
    },
    "2by2": {
        "sourceURL": f"{ML_BASE}/multistate/2by2/latest.json",
        "cachePath": "live/2by2/latest.json",
        "shape": "numeric",
        "sessionFilter": None,
        "drawDate": date_rule("draw_date", "EEE, MMM d, yyyy", EASTERN, anchor_hour=22, anchor_minute=30),
        "nextDrawDate": date_rule("next_drawing.next_draw_date", "EEE, MMM d, yyyy", EASTERN, anchor_hour=22, anchor_minute=30),
        "mainNumbersField": "white_balls",
        "specialNumbers": {"field": "red_balls", "wrapScalar": False},
        "winningTeamsField": None,
        "jackpot": money_direct("next_drawing.top_prize", "compactWords"),
        "cashValue": money_none(),
        "nextJackpot": money_direct("next_drawing.top_prize", "compactWords"),
        "nextCashValue": money_none(),
        "multiplierField": None,
        "winnersByTier": winners_dict_by_tier_name("winners_by_tier", {"Top Prize": "{mainCount}+{specialCount}"}),
    },

    # MARK: - Arizona-exclusive games

    "az-the-pick": {
        "sourceURL": f"{ML_BASE}/arizona/the-pick/latest.json", "cachePath": "live/az-the-pick/latest.json",
        "shape": "numeric", "sessionFilter": None,
        "drawDate": date_rule("draw_date", "yyyy-MM-dd", PHOENIX, anchor_hour=19, anchor_minute=0),
        "nextDrawDate": date_rule("next_draw_date", "yyyy-MM-dd", PHOENIX, anchor_hour=19, anchor_minute=0),
        "mainNumbersField": "numbers", "specialNumbers": None, "winningTeamsField": None,
        "jackpot": money_direct("jackpot_amount", "decimal"), "cashValue": money_none(),
        "nextJackpot": money_direct("next_jackpot_amount", "decimal"), "nextCashValue": money_none(),
        "multiplierField": None,
        "winnersByTier": winners_single_division("winners_by_division", "division1", "{mainCount}"),
    },
    "az-fantasy-5": {
        "sourceURL": f"{ML_BASE}/arizona/fantasy-5/latest.json", "cachePath": "live/az-fantasy-5/latest.json",
        "shape": "numeric", "sessionFilter": None,
        "drawDate": date_rule("draw_date", "yyyy-MM-dd", PHOENIX, anchor_hour=19, anchor_minute=0),
        "nextDrawDate": date_rule("next_draw_date", "yyyy-MM-dd", PHOENIX, anchor_hour=19, anchor_minute=0),
        "mainNumbersField": "numbers", "specialNumbers": None, "winningTeamsField": None,
        "jackpot": money_direct("jackpot_amount", "decimal"), "cashValue": money_none(),
        "nextJackpot": money_direct("next_jackpot_amount", "decimal"), "nextCashValue": money_none(),
        "multiplierField": None,
        "winnersByTier": winners_single_division("winners_by_division", "division1", "{mainCount}"),
    },
    "az-triple-twist": {
        "sourceURL": f"{ML_BASE}/arizona/triple-twist/latest.json", "cachePath": "live/az-triple-twist/latest.json",
        "shape": "numeric", "sessionFilter": None,
        "drawDate": date_rule("draw_date", "yyyy-MM-dd", PHOENIX, anchor_hour=19, anchor_minute=0),
        "nextDrawDate": date_rule("next_draw_date", "yyyy-MM-dd", PHOENIX, anchor_hour=19, anchor_minute=0),
        "mainNumbersField": "numbers", "specialNumbers": None, "winningTeamsField": None,
        "jackpot": money_direct("jackpot_amount", "decimal"), "cashValue": money_none(),
        "nextJackpot": money_direct("next_jackpot_amount", "decimal"), "nextCashValue": money_none(),
        "multiplierField": None,
        "winnersByTier": winners_single_division("winners_by_division", "division1", "{mainCount}"),
    },
}


def digit_game_mapping(source_url, cache_path, tz, session):
    hour = 12 if session == "midday" else 19
    return {
        "sourceURL": source_url, "cachePath": cache_path,
        "shape": "numeric",
        "sessionFilter": {"field": "session", "equals": session},
        "drawDate": date_rule("draw_date", "yyyy-MM-dd", tz, anchor_hour=hour, anchor_minute=0),
        "nextDrawDate": date_rule(
            "next_draw_date", "yyyy-MM-dd", tz,
            anchor_by_session={"midday": 12, "evening": 19}, session_field="next_session",
        ),
        "mainNumbersField": "digits", "specialNumbers": None, "winningTeamsField": None,
        "jackpot": money_none(), "cashValue": money_none(),
        "nextJackpot": money_none(), "nextCashValue": money_none(),
        "multiplierField": None,
        "winnersByTier": None,
    }


LIVE_MAPPINGS["az-pick3-midday"] = digit_game_mapping(f"{ML_BASE}/arizona/pick-3/latest.json", "live/az-pick3/latest.json", PHOENIX, "midday")
LIVE_MAPPINGS["az-pick3-evening"] = digit_game_mapping(f"{ML_BASE}/arizona/pick-3/latest.json", "live/az-pick3/latest.json", PHOENIX, "evening")
LIVE_MAPPINGS["az-pick4-midday"] = digit_game_mapping(f"{ML_BASE}/arizona/pick-4/latest.json", "live/az-pick4/latest.json", PHOENIX, "midday")
LIVE_MAPPINGS["az-pick4-evening"] = digit_game_mapping(f"{ML_BASE}/arizona/pick-4/latest.json", "live/az-pick4/latest.json", PHOENIX, "evening")
LIVE_MAPPINGS["ks-pick3-midday"] = digit_game_mapping(f"{ML_BASE}/kansas/pick-3/latest.json", "live/ks-pick3/latest.json", CENTRAL, "midday")
LIVE_MAPPINGS["ks-pick3-evening"] = digit_game_mapping(f"{ML_BASE}/kansas/pick-3/latest.json", "live/ks-pick3/latest.json", CENTRAL, "evening")

LIVE_MAPPINGS["ks-super-kansas-cash"] = {
    "sourceURL": f"{ML_BASE}/kansas/super-kansas-cash/latest.json", "cachePath": "live/ks-super-kansas-cash/latest.json",
    "shape": "numeric", "sessionFilter": None,
    "drawDate": date_rule("draw_date", "yyyy-MM-dd", CENTRAL, anchor_hour=21, anchor_minute=0),
    "nextDrawDate": date_rule("next_draw_date", "yyyy-MM-dd", CENTRAL, anchor_hour=21, anchor_minute=0),
    "mainNumbersField": "numbers",
    "specialNumbers": {"field": "cash_ball", "wrapScalar": True},
    "winningTeamsField": None,
    "jackpot": money_direct("jackpot_amount", "decimal"), "cashValue": money_none(),
    "nextJackpot": money_direct("next_jackpot_amount", "decimal"), "nextCashValue": money_none(),
    "multiplierField": None,
    "winnersByTier": None,
}


# MARK: - matchRule (only for games whose tierKey can't be resolved by the generic
# "{mainMatches}+{specialMatched}" formula — every other game omits this entirely, which is
# `combinationMatch`'s exact pre-existing behavior).

def positional_digits_rule(box_tier_keys_by_way_count):
    return {
        "kind": "positionalDigits",
        "straightTierKey": "straight",
        "boxTierKeysByWayCount": box_tier_keys_by_way_count,
        "teamCount": None, "teamMatchTierKeyTemplate": None, "teamJackpotTierKey": None,
    }


PICK3_BOX_WAYS = {"6": "box6way", "3": "box3way"}
PICK4_BOX_WAYS = {"24": "box24way", "12": "box12way", "6": "box6way", "4": "box4way"}

MATCH_RULES = {
    "az-pick3-midday": positional_digits_rule(PICK3_BOX_WAYS),
    "az-pick3-evening": positional_digits_rule(PICK3_BOX_WAYS),
    "az-pick4-midday": positional_digits_rule(PICK4_BOX_WAYS),
    "az-pick4-evening": positional_digits_rule(PICK4_BOX_WAYS),
    "ks-pick3-midday": positional_digits_rule(PICK3_BOX_WAYS),
    "ks-pick3-evening": positional_digits_rule(PICK3_BOX_WAYS),
}


def build_game(game_id):
    game = dict(BASE_GAMES[game_id])
    if game_id in LIVE_MAPPINGS:
        game["liveMapping"] = LIVE_MAPPINGS[game_id]
    if game_id in MATCH_RULES:
        game["matchRule"] = MATCH_RULES[game_id]
    return game


def main():
    for state_code, game_ids in STATE_GAMES.items():
        config = {
            "schemaVersion": SCHEMA_VERSION,
            "stateCode": state_code,
            "routedCourierPlatform": STATE_ROUTED_COURIER_PLATFORM[state_code],
            "games": [build_game(gid) for gid in game_ids],
        }
        out_dir = os.path.join(ROOT, "data", STATE_FOLDER[state_code])
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "config.json")
        with open(out_path, "w") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"wrote {out_path} ({len(game_ids)} games)")


if __name__ == "__main__":
    main()
