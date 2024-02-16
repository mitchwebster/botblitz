import agent_pb2 # must be imported for types
import random

def PerformFantasyActions(landscape):
    print(landscape)
    print("Performing bot actions")

    flip = random.randint(0, 1)
    bet = flip == 0

    return agent_pb2.FantasySelections(
        make_bet=bet
    )