from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class FantasyLandscape(_message.Message):
    __slots__ = ("match_number", "settings", "bot_team", "players")
    MATCH_NUMBER_FIELD_NUMBER: _ClassVar[int]
    SETTINGS_FIELD_NUMBER: _ClassVar[int]
    BOT_TEAM_FIELD_NUMBER: _ClassVar[int]
    PLAYERS_FIELD_NUMBER: _ClassVar[int]
    match_number: int
    settings: LeagueSettings
    bot_team: FantasyTeam
    players: _containers.RepeatedCompositeFieldContainer[Player]
    def __init__(self, match_number: _Optional[int] = ..., settings: _Optional[_Union[LeagueSettings, _Mapping]] = ..., bot_team: _Optional[_Union[FantasyTeam, _Mapping]] = ..., players: _Optional[_Iterable[_Union[Player, _Mapping]]] = ...) -> None: ...

class FantasySelections(_message.Message):
    __slots__ = ("slots",)
    SLOTS_FIELD_NUMBER: _ClassVar[int]
    slots: _containers.RepeatedCompositeFieldContainer[PlayerSlot]
    def __init__(self, slots: _Optional[_Iterable[_Union[PlayerSlot, _Mapping]]] = ...) -> None: ...

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
    __slots__ = ("id", "source_repo_username", "source_repo_name", "fantasy_team_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REPO_USERNAME_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REPO_NAME_FIELD_NUMBER: _ClassVar[int]
    FANTASY_TEAM_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    source_repo_username: str
    source_repo_name: str
    fantasy_team_id: int
    def __init__(self, id: _Optional[str] = ..., source_repo_username: _Optional[str] = ..., source_repo_name: _Optional[str] = ..., fantasy_team_id: _Optional[int] = ...) -> None: ...
