from blitz_env import is_drafted, Player, GameState, load_players, AddDropSelection

from typing import List

def team_totals(team: List[Player]) -> dict:
    totals = dict()
    for player in team:
        main_position = player.allowed_positions[0]
        if main_position not in totals:
            totals[main_position] = 0

        totals[main_position] += 1

    return totals

def should_skip(totals: dict, player: Player) -> bool:
    main_position = player.allowed_positions[0]
    if main_position == "QB":
        return main_position in totals and totals[main_position] >= 1
    elif main_position == "RB":
        return main_position in totals and totals[main_position] >= 7
    elif main_position == "WR":
        return main_position in totals and totals[main_position] >= 4
    elif main_position == "TE":
        return main_position in totals and totals[main_position] >= 1
    elif main_position == "K":
        return main_position in totals and totals[main_position] >= 1
    elif main_position == "DST":
        return main_position in totals and totals[main_position] >= 1

def jons_rank(player: Player):
    if player.id == "16393":
        return player.rank + 24
    if player.id == "15802":
        return player.rank + 12
    if player.id == "23180":
        return player.rank + 24
    if player.id == "23064":
        return player.rank + 24
    if player.id == "17240":
        return 5
    if player.id == "12123":
        return player.rank + 12
    if player.id == "23163":
        return player.rank + 12
    if player.id == "20111":
        return player.rank + 12
    if player.id == "19790":
        return player.rank + 24
    if player.id == "16433":
        return 5
    if player.id == "20130":
        return 12
    if player.id == "22978":
        return player.rank + 12
    if player.id == "19252":
        return player.rank + 12
    if player.id == "11594":
        return player.rank + 12
    if player.id == "24333":
        return player.rank - 3
    if player.id == "18244":
        return player.rank - 6
    if player.id == "19222":
        return player.rank + 6
    if player.id == "23136":
        return player.rank - 12
    if player.id == "23891":
        return player.rank - 6
    if player.id == "16421":
        return player.rank - 24
    if player.id == "13894":
        return player.rank + 12
    if player.id == "13981":
        return player.rank - 6
    if player.id == "18269":
        return player.rank - 6
    if player.id == "22958":
        return player.rank - 24
    if player.id == "23113":
        return player.rank - 12
    if player.id == "19211":
        return player.rank + 30
    if player.id == "23021":
        return player.rank - 12
    if player.id == "17269":
        return player.rank + 24
    if player.id == "17268":
        return player.rank + 12
    if player.id == "16406":
        return player.rank - 12
    if player.id == "16447":
        return player.rank - 12
    if player.id == "22726":
        return player.rank - 13
    if player.id == "18239":
        return player.rank - 14
    if player.id == "22902":
        return player.rank - 6
    if player.id == "22739":
        return player.rank + 12
    if player.id == "20095":
        return player.rank + 24
    if player.id == "20164":
        return player.rank - 6
    if player.id == "23020":
        return player.rank - 20
    if player.id == "23070":
        return player.rank + 12
    if player.id == "18705":
        return player.rank - 12
    if player.id == "23000":
        return player.rank - 18
    if player.id == "23019":
        return player.rank - 35
    if player.id == "16399":
        return player.rank + 30
    if player.id == "17237":
        return player.rank - 24
    if player.id == "19245":
        return 84
    if player.id == "25987":
        return 130
    if player.id == "22969":
        return 48
    else:
        return player.rank

def positional_weighting(player: Player):
    if "K" in player.allowed_positions:
        return player.rank * 2
    if "DEF" in player.allowed_positions:
        return player.rank * 2
    if "RB" in player.allowed_positions:
        return player.rank * .9
    if "WR" in player.allowed_positions:
        return player.rank * 1

    return player.rank

def get_all_undrafted_players(gs: GameState):
    undrafted_players = []
    for player in gs.players:
        if is_drafted(player) is not True:
            undrafted_players.append(player)

    return undrafted_players

def determine_player(team: List[Player], players: List[Player]):
    top_ranked_player = None
    top_ranked_player_set_rank = 10000000
    totals = team_totals(team)
    for player in players:
        if should_skip(totals, player):
            pass
        else:
            true_rank = positional_weighting(player)

            if true_rank < top_ranked_player_set_rank:
                top_ranked_player = player
                top_ranked_player_set_rank = true_rank

    return top_ranked_player


def get_drafted_team(game_state: GameState, team_id: str):
    players = game_state.players
    roster = []
    for player in players:
        if is_drafted(player) and player.status.current_fantasy_team_id == team_id:
            roster.append(player)

    return roster

def draft_player(game_state: GameState) -> str:
    # Re-rank players
    for player in game_state.players:
        player.rank = jons_rank(player)

    my_team_id = game_state.current_bot_team_id
    my_team = get_drafted_team(game_state=game_state, team_id=my_team_id)
    player_to_draft = determine_player(my_team, get_all_undrafted_players(game_state))
    return player_to_draft.id

def propose_add_drop(game_state: GameState) -> AddDropSelection:
    """
    Selects a player to draft based on the highest rank.

    Args:
        players (List[Player]): A list of Player objects.

    Returns:
        str: The id of the drafted player.
    """
    return AddDropSelection(
        player_to_add_id="",
        player_to_drop_id=""
    )