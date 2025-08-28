from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, ForeignKey, Table, JSON
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from typing import List

Base = declarative_base()

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
    allowed_positions = Column(JSON)

    # Player status fields
    availability = Column(String, default='AVAILABLE')  # AVAILABLE, DRAFTED, ON_HOLD
    pick_chosen = Column(Integer)
    current_bot_id = Column(String, ForeignKey('bots.id'))

    # Correct relationship: points to Bot.players
    bot = relationship("Bot", back_populates="players")

class Bot(Base):
    __tablename__ = 'bots'

    id = Column(String, primary_key=True)
    draft_order = Column(Integer)
    name = Column(String)
    owner = Column(String)
    current_waiver_priority = Column(Integer, default=0)

    # Reverse side: a bot has many players
    players = relationship("Player", back_populates="bot")

class LeagueSettings(Base):
    __tablename__ = 'league_settings'

    id = Column(Integer, primary_key=True)
    year = Column(Integer)
    player_slots = Column(JSON) # {"RB": 2, "QB": 2}
    is_snake_draft = Column(Boolean)
    total_rounds = Column(Integer)
    points_per_reception = Column(Float)


class GameStatus(Base):
    __tablename__ = 'game_status'

    id = Column(Integer, primary_key=True)
    current_bot_id = Column(String, ForeignKey('bots.id'))
    current_draft_pick = Column(Integer)
    current_fantasy_week = Column(Integer)


class DatabaseManager:
    # TODO: this should live elsewhere, since it's not a constant
    DB_URL = "sqlite:///draft.db"
    
    def __init__(self):
        self.engine = create_engine(self.DB_URL)
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine)
        self.session = Session()

    def close(self):
        self.session.close()

    def get_game_status(self) -> GameStatus:
        status = self.session.query(GameStatus).first()
        if not status:
            status = GameStatus()
            self.session.add(status)
            self.session.commit()
        return status

    def get_league_settings(self) -> LeagueSettings:
        return self.session.query(LeagueSettings).first()

    def get_all_players(self) -> List[Player]:
        return self.session.query(Player).all()

    def get_all_bots(self) -> List[Bot]:
        return self.session.query(Bot).all()

    def get_player_by_id(self, player_id: str) -> Player:
        return self.session.query(Player).filter(Player.id == player_id).first()

    def draft_player(self, player_id: str, bot_id: str, pick_number: int):
        player = self.get_player_by_id(player_id)
        if player:
            player.availability = 'DRAFTED'
            player.current_bot_id = bot_id
            player.pick_chosen = pick_number
            self.session.commit()

    def update_draft_pick(self, pick_number: int, bot_id: str):
        status = self.get_game_status()
        status.current_draft_pick = pick_number
        status.current_bot_id = bot_id
        self.session.commit()

    def get_bot_by_index(self, index: int) -> Bot:        
        return self.session.query(Bot).filter(Bot.draft_order == index).first()

    def is_draft_complete(self) -> bool:
        settings = self.get_league_settings()
        status = self.get_game_status()
        bots = self.get_all_bots()
        num_bots = len(bots)
        if not settings:
            return False
        total_picks = settings.total_rounds * num_bots
        return status.current_draft_pick > total_picks