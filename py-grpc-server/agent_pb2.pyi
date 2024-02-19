from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class FantasyLandscape(_message.Message):
    __slots__ = ("match_number", "settings", "bot_team", "bet", "players")
    MATCH_NUMBER_FIELD_NUMBER: _ClassVar[int]
    SETTINGS_FIELD_NUMBER: _ClassVar[int]
    BOT_TEAM_FIELD_NUMBER: _ClassVar[int]
    BET_FIELD_NUMBER: _ClassVar[int]
    PLAYERS_FIELD_NUMBER: _ClassVar[int]
    match_number: int
    settings: LeagueSettings
    bot_team: FantasyTeam
    bet: Bet
    players: _containers.RepeatedCompositeFieldContainer[Player]
    def __init__(self, match_number: _Optional[int] = ..., settings: _Optional[_Union[LeagueSettings, _Mapping]] = ..., bot_team: _Optional[_Union[FantasyTeam, _Mapping]] = ..., bet: _Optional[_Union[Bet, _Mapping]] = ..., players: _Optional[_Iterable[_Union[Player, _Mapping]]] = ...) -> None: ...

class Bet(_message.Message):
    __slots__ = ("professional_home_team", "professional_away_team", "player", "type", "points", "price", "start_time")
    class Type(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        OVER: _ClassVar[Bet.Type]
        UNDER: _ClassVar[Bet.Type]
    OVER: Bet.Type
    UNDER: Bet.Type
    PROFESSIONAL_HOME_TEAM_FIELD_NUMBER: _ClassVar[int]
    PROFESSIONAL_AWAY_TEAM_FIELD_NUMBER: _ClassVar[int]
    PLAYER_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    POINTS_FIELD_NUMBER: _ClassVar[int]
    PRICE_FIELD_NUMBER: _ClassVar[int]
    START_TIME_FIELD_NUMBER: _ClassVar[int]
    professional_home_team: str
    professional_away_team: str
    player: Player
    type: Bet.Type
    points: float
    price: float
    start_time: _timestamp_pb2.Timestamp
    def __init__(self, professional_home_team: _Optional[str] = ..., professional_away_team: _Optional[str] = ..., player: _Optional[_Union[Player, _Mapping]] = ..., type: _Optional[_Union[Bet.Type, str]] = ..., points: _Optional[float] = ..., price: _Optional[float] = ..., start_time: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class FantasySelections(_message.Message):
    __slots__ = ("make_bet", "slots")
    MAKE_BET_FIELD_NUMBER: _ClassVar[int]
    SLOTS_FIELD_NUMBER: _ClassVar[int]
    make_bet: bool
    slots: _containers.RepeatedCompositeFieldContainer[PlayerSlot]
    def __init__(self, make_bet: bool = ..., slots: _Optional[_Iterable[_Union[PlayerSlot, _Mapping]]] = ...) -> None: ...

class FantasyTeam(_message.Message):
    __slots__ = ("id", "name", "owner")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    owner: str
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., owner: _Optional[str] = ...) -> None: ...

class LeagueSettings(_message.Message):
    __slots__ = ("num_teams", "slots_per_team")
    NUM_TEAMS_FIELD_NUMBER: _ClassVar[int]
    SLOTS_PER_TEAM_FIELD_NUMBER: _ClassVar[int]
    num_teams: int
    slots_per_team: _containers.RepeatedCompositeFieldContainer[PlayerSlot]
    def __init__(self, num_teams: _Optional[int] = ..., slots_per_team: _Optional[_Iterable[_Union[PlayerSlot, _Mapping]]] = ...) -> None: ...

class PlayerSlot(_message.Message):
    __slots__ = ("name", "allowed_player_positions", "assigned_player_id")
    NAME_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_PLAYER_POSITIONS_FIELD_NUMBER: _ClassVar[int]
    ASSIGNED_PLAYER_ID_FIELD_NUMBER: _ClassVar[int]
    name: str
    allowed_player_positions: _containers.RepeatedScalarFieldContainer[str]
    assigned_player_id: str
    def __init__(self, name: _Optional[str] = ..., allowed_player_positions: _Optional[_Iterable[str]] = ..., assigned_player_id: _Optional[str] = ...) -> None: ...

class Player(_message.Message):
    __slots__ = ("id", "full_name", "allowed_positions", "professional_team", "status", "fantasy_team_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    FULL_NAME_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_POSITIONS_FIELD_NUMBER: _ClassVar[int]
    PROFESSIONAL_TEAM_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    FANTASY_TEAM_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    full_name: str
    allowed_positions: _containers.RepeatedScalarFieldContainer[str]
    professional_team: str
    status: str
    fantasy_team_id: int
    def __init__(self, id: _Optional[str] = ..., full_name: _Optional[str] = ..., allowed_positions: _Optional[_Iterable[str]] = ..., professional_team: _Optional[str] = ..., status: _Optional[str] = ..., fantasy_team_id: _Optional[int] = ...) -> None: ...

class Bot(_message.Message):
    __slots__ = ("id", "source_type", "source_repo_username", "source_repo_name", "source_path", "fantasy_team_id")
    class Source(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        LOCAL: _ClassVar[Bot.Source]
        REMOTE: _ClassVar[Bot.Source]
    LOCAL: Bot.Source
    REMOTE: Bot.Source
    ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_TYPE_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REPO_USERNAME_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REPO_NAME_FIELD_NUMBER: _ClassVar[int]
    SOURCE_PATH_FIELD_NUMBER: _ClassVar[int]
    FANTASY_TEAM_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    source_type: Bot.Source
    source_repo_username: str
    source_repo_name: str
    source_path: str
    fantasy_team_id: int
    def __init__(self, id: _Optional[str] = ..., source_type: _Optional[_Union[Bot.Source, str]] = ..., source_repo_username: _Optional[str] = ..., source_repo_name: _Optional[str] = ..., source_path: _Optional[str] = ..., fantasy_team_id: _Optional[int] = ...) -> None: ...

class Simulation(_message.Message):
    __slots__ = ("id", "landscape", "num_iterations")
    ID_FIELD_NUMBER: _ClassVar[int]
    LANDSCAPE_FIELD_NUMBER: _ClassVar[int]
    NUM_ITERATIONS_FIELD_NUMBER: _ClassVar[int]
    id: str
    landscape: FantasyLandscape
    num_iterations: int
    def __init__(self, id: _Optional[str] = ..., landscape: _Optional[_Union[FantasyLandscape, _Mapping]] = ..., num_iterations: _Optional[int] = ...) -> None: ...

class GameLog(_message.Message):
    __slots__ = ("season_year", "player_id", "player_name", "nickname", "team_id", "team_abbreviation", "team_name", "game_id", "game_date", "matchup", "wl", "min", "fgm", "fga", "fg_pct", "fg3m", "fg3a", "fg3_pct", "ftm", "fta", "ft_pct", "oreb", "dreb", "reb", "ast", "tov", "stl", "blk", "blka", "pf", "pfd", "pts", "plus_minus", "nba_fantasy_pts", "dd2", "td3", "wnba_fantasy_pts", "gp_rank", "w_rank", "l_rank", "w_pct_rank", "min_rank", "fgm_rank", "fga_rank", "fg_pct_rank", "fg3m_rank", "fg3a_rank", "fg3_pct_rank", "ftm_rank", "fta_rank", "ft_pct_rank", "oreb_rank", "dreb_rank", "reb_rank", "ast_rank", "tov_rank", "stl_rank", "blk_rank", "blka_rank", "pf_rank", "pfd_rank", "pts_rank", "plus_minus_rank", "nba_fantasy_pts_rank", "dd2_rank", "td3_rank", "wnba_fantasy_pts_rank", "available_flag")
    SEASON_YEAR_FIELD_NUMBER: _ClassVar[int]
    PLAYER_ID_FIELD_NUMBER: _ClassVar[int]
    PLAYER_NAME_FIELD_NUMBER: _ClassVar[int]
    NICKNAME_FIELD_NUMBER: _ClassVar[int]
    TEAM_ID_FIELD_NUMBER: _ClassVar[int]
    TEAM_ABBREVIATION_FIELD_NUMBER: _ClassVar[int]
    TEAM_NAME_FIELD_NUMBER: _ClassVar[int]
    GAME_ID_FIELD_NUMBER: _ClassVar[int]
    GAME_DATE_FIELD_NUMBER: _ClassVar[int]
    MATCHUP_FIELD_NUMBER: _ClassVar[int]
    WL_FIELD_NUMBER: _ClassVar[int]
    MIN_FIELD_NUMBER: _ClassVar[int]
    FGM_FIELD_NUMBER: _ClassVar[int]
    FGA_FIELD_NUMBER: _ClassVar[int]
    FG_PCT_FIELD_NUMBER: _ClassVar[int]
    FG3M_FIELD_NUMBER: _ClassVar[int]
    FG3A_FIELD_NUMBER: _ClassVar[int]
    FG3_PCT_FIELD_NUMBER: _ClassVar[int]
    FTM_FIELD_NUMBER: _ClassVar[int]
    FTA_FIELD_NUMBER: _ClassVar[int]
    FT_PCT_FIELD_NUMBER: _ClassVar[int]
    OREB_FIELD_NUMBER: _ClassVar[int]
    DREB_FIELD_NUMBER: _ClassVar[int]
    REB_FIELD_NUMBER: _ClassVar[int]
    AST_FIELD_NUMBER: _ClassVar[int]
    TOV_FIELD_NUMBER: _ClassVar[int]
    STL_FIELD_NUMBER: _ClassVar[int]
    BLK_FIELD_NUMBER: _ClassVar[int]
    BLKA_FIELD_NUMBER: _ClassVar[int]
    PF_FIELD_NUMBER: _ClassVar[int]
    PFD_FIELD_NUMBER: _ClassVar[int]
    PTS_FIELD_NUMBER: _ClassVar[int]
    PLUS_MINUS_FIELD_NUMBER: _ClassVar[int]
    NBA_FANTASY_PTS_FIELD_NUMBER: _ClassVar[int]
    DD2_FIELD_NUMBER: _ClassVar[int]
    TD3_FIELD_NUMBER: _ClassVar[int]
    WNBA_FANTASY_PTS_FIELD_NUMBER: _ClassVar[int]
    GP_RANK_FIELD_NUMBER: _ClassVar[int]
    W_RANK_FIELD_NUMBER: _ClassVar[int]
    L_RANK_FIELD_NUMBER: _ClassVar[int]
    W_PCT_RANK_FIELD_NUMBER: _ClassVar[int]
    MIN_RANK_FIELD_NUMBER: _ClassVar[int]
    FGM_RANK_FIELD_NUMBER: _ClassVar[int]
    FGA_RANK_FIELD_NUMBER: _ClassVar[int]
    FG_PCT_RANK_FIELD_NUMBER: _ClassVar[int]
    FG3M_RANK_FIELD_NUMBER: _ClassVar[int]
    FG3A_RANK_FIELD_NUMBER: _ClassVar[int]
    FG3_PCT_RANK_FIELD_NUMBER: _ClassVar[int]
    FTM_RANK_FIELD_NUMBER: _ClassVar[int]
    FTA_RANK_FIELD_NUMBER: _ClassVar[int]
    FT_PCT_RANK_FIELD_NUMBER: _ClassVar[int]
    OREB_RANK_FIELD_NUMBER: _ClassVar[int]
    DREB_RANK_FIELD_NUMBER: _ClassVar[int]
    REB_RANK_FIELD_NUMBER: _ClassVar[int]
    AST_RANK_FIELD_NUMBER: _ClassVar[int]
    TOV_RANK_FIELD_NUMBER: _ClassVar[int]
    STL_RANK_FIELD_NUMBER: _ClassVar[int]
    BLK_RANK_FIELD_NUMBER: _ClassVar[int]
    BLKA_RANK_FIELD_NUMBER: _ClassVar[int]
    PF_RANK_FIELD_NUMBER: _ClassVar[int]
    PFD_RANK_FIELD_NUMBER: _ClassVar[int]
    PTS_RANK_FIELD_NUMBER: _ClassVar[int]
    PLUS_MINUS_RANK_FIELD_NUMBER: _ClassVar[int]
    NBA_FANTASY_PTS_RANK_FIELD_NUMBER: _ClassVar[int]
    DD2_RANK_FIELD_NUMBER: _ClassVar[int]
    TD3_RANK_FIELD_NUMBER: _ClassVar[int]
    WNBA_FANTASY_PTS_RANK_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_FLAG_FIELD_NUMBER: _ClassVar[int]
    season_year: str
    player_id: int
    player_name: str
    nickname: str
    team_id: int
    team_abbreviation: str
    team_name: str
    game_id: str
    game_date: _timestamp_pb2.Timestamp
    matchup: str
    wl: str
    min: float
    fgm: int
    fga: int
    fg_pct: float
    fg3m: int
    fg3a: int
    fg3_pct: float
    ftm: int
    fta: int
    ft_pct: float
    oreb: int
    dreb: int
    reb: int
    ast: int
    tov: int
    stl: int
    blk: int
    blka: int
    pf: int
    pfd: int
    pts: int
    plus_minus: int
    nba_fantasy_pts: float
    dd2: int
    td3: int
    wnba_fantasy_pts: float
    gp_rank: int
    w_rank: int
    l_rank: int
    w_pct_rank: int
    min_rank: int
    fgm_rank: int
    fga_rank: int
    fg_pct_rank: int
    fg3m_rank: int
    fg3a_rank: int
    fg3_pct_rank: int
    ftm_rank: int
    fta_rank: int
    ft_pct_rank: int
    oreb_rank: int
    dreb_rank: int
    reb_rank: int
    ast_rank: int
    tov_rank: int
    stl_rank: int
    blk_rank: int
    blka_rank: int
    pf_rank: int
    pfd_rank: int
    pts_rank: int
    plus_minus_rank: int
    nba_fantasy_pts_rank: int
    dd2_rank: int
    td3_rank: int
    wnba_fantasy_pts_rank: int
    available_flag: int
    def __init__(self, season_year: _Optional[str] = ..., player_id: _Optional[int] = ..., player_name: _Optional[str] = ..., nickname: _Optional[str] = ..., team_id: _Optional[int] = ..., team_abbreviation: _Optional[str] = ..., team_name: _Optional[str] = ..., game_id: _Optional[str] = ..., game_date: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ..., matchup: _Optional[str] = ..., wl: _Optional[str] = ..., min: _Optional[float] = ..., fgm: _Optional[int] = ..., fga: _Optional[int] = ..., fg_pct: _Optional[float] = ..., fg3m: _Optional[int] = ..., fg3a: _Optional[int] = ..., fg3_pct: _Optional[float] = ..., ftm: _Optional[int] = ..., fta: _Optional[int] = ..., ft_pct: _Optional[float] = ..., oreb: _Optional[int] = ..., dreb: _Optional[int] = ..., reb: _Optional[int] = ..., ast: _Optional[int] = ..., tov: _Optional[int] = ..., stl: _Optional[int] = ..., blk: _Optional[int] = ..., blka: _Optional[int] = ..., pf: _Optional[int] = ..., pfd: _Optional[int] = ..., pts: _Optional[int] = ..., plus_minus: _Optional[int] = ..., nba_fantasy_pts: _Optional[float] = ..., dd2: _Optional[int] = ..., td3: _Optional[int] = ..., wnba_fantasy_pts: _Optional[float] = ..., gp_rank: _Optional[int] = ..., w_rank: _Optional[int] = ..., l_rank: _Optional[int] = ..., w_pct_rank: _Optional[int] = ..., min_rank: _Optional[int] = ..., fgm_rank: _Optional[int] = ..., fga_rank: _Optional[int] = ..., fg_pct_rank: _Optional[int] = ..., fg3m_rank: _Optional[int] = ..., fg3a_rank: _Optional[int] = ..., fg3_pct_rank: _Optional[int] = ..., ftm_rank: _Optional[int] = ..., fta_rank: _Optional[int] = ..., ft_pct_rank: _Optional[int] = ..., oreb_rank: _Optional[int] = ..., dreb_rank: _Optional[int] = ..., reb_rank: _Optional[int] = ..., ast_rank: _Optional[int] = ..., tov_rank: _Optional[int] = ..., stl_rank: _Optional[int] = ..., blk_rank: _Optional[int] = ..., blka_rank: _Optional[int] = ..., pf_rank: _Optional[int] = ..., pfd_rank: _Optional[int] = ..., pts_rank: _Optional[int] = ..., plus_minus_rank: _Optional[int] = ..., nba_fantasy_pts_rank: _Optional[int] = ..., dd2_rank: _Optional[int] = ..., td3_rank: _Optional[int] = ..., wnba_fantasy_pts_rank: _Optional[int] = ..., available_flag: _Optional[int] = ...) -> None: ...
