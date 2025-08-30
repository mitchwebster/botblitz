from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class LeagueSettings(_message.Message):
    __slots__ = ("num_teams", "slots_per_team", "is_snake_draft", "total_rounds", "points_per_reception", "year")
    class SlotsPerTeamEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    NUM_TEAMS_FIELD_NUMBER: _ClassVar[int]
    SLOTS_PER_TEAM_FIELD_NUMBER: _ClassVar[int]
    IS_SNAKE_DRAFT_FIELD_NUMBER: _ClassVar[int]
    TOTAL_ROUNDS_FIELD_NUMBER: _ClassVar[int]
    POINTS_PER_RECEPTION_FIELD_NUMBER: _ClassVar[int]
    YEAR_FIELD_NUMBER: _ClassVar[int]
    num_teams: int
    slots_per_team: _containers.ScalarMap[str, int]
    is_snake_draft: bool
    total_rounds: int
    points_per_reception: float
    year: int
    def __init__(self, num_teams: _Optional[int] = ..., slots_per_team: _Optional[_Mapping[str, int]] = ..., is_snake_draft: bool = ..., total_rounds: _Optional[int] = ..., points_per_reception: _Optional[float] = ..., year: _Optional[int] = ...) -> None: ...

class PlayerSlot(_message.Message):
    __slots__ = ("name", "allowed_player_positions", "assigned_player_id", "allows_any_position")
    NAME_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_PLAYER_POSITIONS_FIELD_NUMBER: _ClassVar[int]
    ASSIGNED_PLAYER_ID_FIELD_NUMBER: _ClassVar[int]
    ALLOWS_ANY_POSITION_FIELD_NUMBER: _ClassVar[int]
    name: str
    allowed_player_positions: _containers.RepeatedScalarFieldContainer[str]
    assigned_player_id: str
    allows_any_position: bool
    def __init__(self, name: _Optional[str] = ..., allowed_player_positions: _Optional[_Iterable[str]] = ..., assigned_player_id: _Optional[str] = ..., allows_any_position: bool = ...) -> None: ...

class Player(_message.Message):
    __slots__ = ("id", "full_name", "allowed_positions", "professional_team", "player_bye_week", "rank", "tier", "position_rank", "position_tier", "status", "gsis_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    FULL_NAME_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_POSITIONS_FIELD_NUMBER: _ClassVar[int]
    PROFESSIONAL_TEAM_FIELD_NUMBER: _ClassVar[int]
    PLAYER_BYE_WEEK_FIELD_NUMBER: _ClassVar[int]
    RANK_FIELD_NUMBER: _ClassVar[int]
    TIER_FIELD_NUMBER: _ClassVar[int]
    POSITION_RANK_FIELD_NUMBER: _ClassVar[int]
    POSITION_TIER_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    GSIS_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    full_name: str
    allowed_positions: _containers.RepeatedScalarFieldContainer[str]
    professional_team: str
    player_bye_week: int
    rank: int
    tier: int
    position_rank: int
    position_tier: int
    status: PlayerStatus
    gsis_id: str
    def __init__(self, id: _Optional[str] = ..., full_name: _Optional[str] = ..., allowed_positions: _Optional[_Iterable[str]] = ..., professional_team: _Optional[str] = ..., player_bye_week: _Optional[int] = ..., rank: _Optional[int] = ..., tier: _Optional[int] = ..., position_rank: _Optional[int] = ..., position_tier: _Optional[int] = ..., status: _Optional[_Union[PlayerStatus, _Mapping]] = ..., gsis_id: _Optional[str] = ...) -> None: ...

class PlayerStatus(_message.Message):
    __slots__ = ("availability", "pick_chosen", "current_team_bot_id")
    class Availability(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        AVAILABLE: _ClassVar[PlayerStatus.Availability]
        DRAFTED: _ClassVar[PlayerStatus.Availability]
        ON_HOLD: _ClassVar[PlayerStatus.Availability]
    AVAILABLE: PlayerStatus.Availability
    DRAFTED: PlayerStatus.Availability
    ON_HOLD: PlayerStatus.Availability
    AVAILABILITY_FIELD_NUMBER: _ClassVar[int]
    PICK_CHOSEN_FIELD_NUMBER: _ClassVar[int]
    CURRENT_TEAM_BOT_ID_FIELD_NUMBER: _ClassVar[int]
    availability: PlayerStatus.Availability
    pick_chosen: int
    current_team_bot_id: str
    def __init__(self, availability: _Optional[_Union[PlayerStatus.Availability, str]] = ..., pick_chosen: _Optional[int] = ..., current_team_bot_id: _Optional[str] = ...) -> None: ...

class Bot(_message.Message):
    __slots__ = ("id", "fantasy_team_name", "owner", "source_type", "source_repo_username", "source_repo_name", "source_path", "env_path", "current_waiver_priority")
    class Source(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        LOCAL: _ClassVar[Bot.Source]
        REMOTE: _ClassVar[Bot.Source]
    LOCAL: Bot.Source
    REMOTE: Bot.Source
    ID_FIELD_NUMBER: _ClassVar[int]
    FANTASY_TEAM_NAME_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    SOURCE_TYPE_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REPO_USERNAME_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REPO_NAME_FIELD_NUMBER: _ClassVar[int]
    SOURCE_PATH_FIELD_NUMBER: _ClassVar[int]
    ENV_PATH_FIELD_NUMBER: _ClassVar[int]
    CURRENT_WAIVER_PRIORITY_FIELD_NUMBER: _ClassVar[int]
    id: str
    fantasy_team_name: str
    owner: str
    source_type: Bot.Source
    source_repo_username: str
    source_repo_name: str
    source_path: str
    env_path: str
    current_waiver_priority: int
    def __init__(self, id: _Optional[str] = ..., fantasy_team_name: _Optional[str] = ..., owner: _Optional[str] = ..., source_type: _Optional[_Union[Bot.Source, str]] = ..., source_repo_username: _Optional[str] = ..., source_repo_name: _Optional[str] = ..., source_path: _Optional[str] = ..., env_path: _Optional[str] = ..., current_waiver_priority: _Optional[int] = ...) -> None: ...

class GameState(_message.Message):
    __slots__ = ("players", "bots", "league_settings", "current_bot_team_id", "current_draft_pick", "current_fantasy_week")
    PLAYERS_FIELD_NUMBER: _ClassVar[int]
    BOTS_FIELD_NUMBER: _ClassVar[int]
    LEAGUE_SETTINGS_FIELD_NUMBER: _ClassVar[int]
    CURRENT_BOT_TEAM_ID_FIELD_NUMBER: _ClassVar[int]
    CURRENT_DRAFT_PICK_FIELD_NUMBER: _ClassVar[int]
    CURRENT_FANTASY_WEEK_FIELD_NUMBER: _ClassVar[int]
    players: _containers.RepeatedCompositeFieldContainer[Player]
    bots: _containers.RepeatedCompositeFieldContainer[Bot]
    league_settings: LeagueSettings
    current_bot_team_id: str
    current_draft_pick: int
    current_fantasy_week: int
    def __init__(self, players: _Optional[_Iterable[_Union[Player, _Mapping]]] = ..., bots: _Optional[_Iterable[_Union[Bot, _Mapping]]] = ..., league_settings: _Optional[_Union[LeagueSettings, _Mapping]] = ..., current_bot_team_id: _Optional[str] = ..., current_draft_pick: _Optional[int] = ..., current_fantasy_week: _Optional[int] = ...) -> None: ...

class DraftSelection(_message.Message):
    __slots__ = ("player_id",)
    PLAYER_ID_FIELD_NUMBER: _ClassVar[int]
    player_id: str
    def __init__(self, player_id: _Optional[str] = ...) -> None: ...

class AttemptedFantasyActions(_message.Message):
    __slots__ = ("add_drop_selections",)
    ADD_DROP_SELECTIONS_FIELD_NUMBER: _ClassVar[int]
    add_drop_selections: _containers.RepeatedCompositeFieldContainer[AddDropSelection]
    def __init__(self, add_drop_selections: _Optional[_Iterable[_Union[AddDropSelection, _Mapping]]] = ...) -> None: ...

class AddDropSelection(_message.Message):
    __slots__ = ("player_to_drop_id", "player_to_add_id")
    PLAYER_TO_DROP_ID_FIELD_NUMBER: _ClassVar[int]
    PLAYER_TO_ADD_ID_FIELD_NUMBER: _ClassVar[int]
    player_to_drop_id: str
    player_to_add_id: str
    def __init__(self, player_to_drop_id: _Optional[str] = ..., player_to_add_id: _Optional[str] = ...) -> None: ...
