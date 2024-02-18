import agent_pb2 # Required do not edit

def PerformFantasyActions(landscape):
    print(landscape)
    print("Performing bot actions")

    return agent_pb2.FantasySelections(
        make_bet=True
    )