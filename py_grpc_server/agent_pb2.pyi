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
    __slots__ = ("professional_home_team", "professional_away_team", "player", "type", "points", "price")
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
    professional_home_team: str
    professional_away_team: str
    player: Player
    type: Bet.Type
    points: float
    price: float
    def __init__(self, professional_home_team: _Optional[str] = ..., professional_away_team: _Optional[str] = ..., player: _Optional[_Union[Player, _Mapping]] = ..., type: _Optional[_Union[Bet.Type, str]] = ..., points: _Optional[float] = ..., price: _Optional[float] = ...) -> None: ...

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
    __slots__ = ("id", "full_name", "allowed_positions", "professional_team", "player_bye_week", "rank", "tier", "position_rank", "position_tier", "draft_status")
    ID_FIELD_NUMBER: _ClassVar[int]
    FULL_NAME_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_POSITIONS_FIELD_NUMBER: _ClassVar[int]
    PROFESSIONAL_TEAM_FIELD_NUMBER: _ClassVar[int]
    PLAYER_BYE_WEEK_FIELD_NUMBER: _ClassVar[int]
    RANK_FIELD_NUMBER: _ClassVar[int]
    TIER_FIELD_NUMBER: _ClassVar[int]
    POSITION_RANK_FIELD_NUMBER: _ClassVar[int]
    POSITION_TIER_FIELD_NUMBER: _ClassVar[int]
    DRAFT_STATUS_FIELD_NUMBER: _ClassVar[int]
    id: str
    full_name: str
    allowed_positions: _containers.RepeatedScalarFieldContainer[str]
    professional_team: str
    player_bye_week: int
    rank: int
    tier: int
    position_rank: int
    position_tier: int
    draft_status: DraftStatus
    def __init__(self, id: _Optional[str] = ..., full_name: _Optional[str] = ..., allowed_positions: _Optional[_Iterable[str]] = ..., professional_team: _Optional[str] = ..., player_bye_week: _Optional[int] = ..., rank: _Optional[int] = ..., tier: _Optional[int] = ..., position_rank: _Optional[int] = ..., position_tier: _Optional[int] = ..., draft_status: _Optional[_Union[DraftStatus, _Mapping]] = ...) -> None: ...

class DraftStatus(_message.Message):
    __slots__ = ("availability", "pick_chosen", "team_id_chosen")
    class Availability(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        AVAILABLE: _ClassVar[DraftStatus.Availability]
        DRAFTED: _ClassVar[DraftStatus.Availability]
    AVAILABLE: DraftStatus.Availability
    DRAFTED: DraftStatus.Availability
    AVAILABILITY_FIELD_NUMBER: _ClassVar[int]
    PICK_CHOSEN_FIELD_NUMBER: _ClassVar[int]
    TEAM_ID_CHOSEN_FIELD_NUMBER: _ClassVar[int]
    availability: DraftStatus.Availability
    pick_chosen: int
    team_id_chosen: str
    def __init__(self, availability: _Optional[_Union[DraftStatus.Availability, str]] = ..., pick_chosen: _Optional[int] = ..., team_id_chosen: _Optional[str] = ...) -> None: ...

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

class GameState(_message.Message):
    __slots__ = ("players", "teams", "current_pick", "total_rounds", "drafting_team_id")
    PLAYERS_FIELD_NUMBER: _ClassVar[int]
    TEAMS_FIELD_NUMBER: _ClassVar[int]
    CURRENT_PICK_FIELD_NUMBER: _ClassVar[int]
    TOTAL_ROUNDS_FIELD_NUMBER: _ClassVar[int]
    DRAFTING_TEAM_ID_FIELD_NUMBER: _ClassVar[int]
    players: _containers.RepeatedCompositeFieldContainer[Player]
    teams: _containers.RepeatedCompositeFieldContainer[FantasyTeam]
    current_pick: int
    total_rounds: int
    drafting_team_id: str
    def __init__(self, players: _Optional[_Iterable[_Union[Player, _Mapping]]] = ..., teams: _Optional[_Iterable[_Union[FantasyTeam, _Mapping]]] = ..., current_pick: _Optional[int] = ..., total_rounds: _Optional[int] = ..., drafting_team_id: _Optional[str] = ...) -> None: ...
