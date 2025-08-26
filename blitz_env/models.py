from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, ForeignKey, Table
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from typing import List

Base = declarative_base()

# Association table for player allowed positions
player_positions = Table(
    'player_positions', Base.metadata,
    Column('player_id', String, ForeignKey('players.id')),
    Column('position', String)
)

# Association table for player slot allowed positions
slot_positions = Table(
    'slot_positions', Base.metadata,
    Column('slot_id', Integer, ForeignKey('player_slots.id')),
    Column('position', String)
)

class DraftStatus(Base):
    __tablename__ = 'draft_status'
    
    id = Column(Integer, primary_key=True)
    current_draft_pick = Column(Integer, default=1)
    current_bot_team_id = Column(String)
    current_fantasy_week = Column(Integer, default=1)

class LeagueSettings(Base):
    __tablename__ = 'league_settings'
    
    id = Column(Integer, primary_key=True)
    num_teams = Column(Integer)
    is_snake_draft = Column(Boolean)
    total_rounds = Column(Integer)
    points_per_reception = Column(Float)
    year = Column(Integer)

class FantasyTeam(Base):
    __tablename__ = 'fantasy_teams'
    
    id = Column(String, primary_key=True)
    name = Column(String)
    owner = Column(String)
    current_waiver_priority = Column(Integer, default=0)

class Player(Base):
    __tablename__ = 'players'
    
    id = Column(String, primary_key=True)
    full_name = Column(String)
    professional_team = Column(String)
    player_bye_week = Column(Integer)
    rank = Column(Integer)
    tier = Column(Integer)
    position_rank = Column(Integer)
    position_tier = Column(Integer)
    gsis_id = Column(String)
    position = Column(String)  # Store the primary position (QB, RB, WR, etc.)
    
    # Player status fields
    availability = Column(String, default='AVAILABLE')  # AVAILABLE, DRAFTED, ON_HOLD
    pick_chosen = Column(Integer)
    current_fantasy_team_id = Column(String, ForeignKey('fantasy_teams.id'))
    
    # Relationship
    fantasy_team = relationship("FantasyTeam", back_populates="players")

class PlayerSlot(Base):
    __tablename__ = 'player_slots'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String)
    assigned_player_id = Column(String, ForeignKey('players.id'))
    allows_any_position = Column(Boolean, default=False)
    team_id = Column(String, ForeignKey('fantasy_teams.id'))
    
    # Relationships
    assigned_player = relationship("Player")
    team = relationship("FantasyTeam", back_populates="slots")

# Add back-references
FantasyTeam.players = relationship("Player", back_populates="fantasy_team")
FantasyTeam.slots = relationship("PlayerSlot", back_populates="team")

class DatabaseManager:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine)
        self.session = Session()
    
    def close(self):
        self.session.close()
    
    def get_draft_status(self) -> DraftStatus:
        status = self.session.query(DraftStatus).first()
        if not status:
            status = DraftStatus()
            self.session.add(status)
            self.session.commit()
        return status
    
    def get_league_settings(self) -> LeagueSettings:
        return self.session.query(LeagueSettings).first()
    
    def get_all_players(self) -> List[Player]:
        return self.session.query(Player).all()
    
    def get_all_teams(self) -> List[FantasyTeam]:
        return self.session.query(FantasyTeam).all()
    
    def get_player_by_id(self, player_id: str) -> Player:
        return self.session.query(Player).filter(Player.id == player_id).first()
    
    def draft_player(self, player_id: str, team_id: str, pick_number: int):
        player = self.get_player_by_id(player_id)
        if player:
            player.availability = 'DRAFTED'
            player.current_fantasy_team_id = team_id
            player.pick_chosen = pick_number
            self.session.commit()
    
    def update_draft_pick(self, pick_number: int, team_id: str):
        status = self.get_draft_status()
        status.current_draft_pick = pick_number
        status.current_bot_team_id = team_id
        self.session.commit()
    
    def get_team_by_index(self, index: int) -> FantasyTeam:
        teams = self.get_all_teams()
        return teams[index] if index < len(teams) else None
    
    def is_draft_complete(self) -> bool:
        settings = self.get_league_settings()
        status = self.get_draft_status()
        if not settings:
            return False
        total_picks = settings.total_rounds * settings.num_teams
        return status.current_draft_pick > total_picks