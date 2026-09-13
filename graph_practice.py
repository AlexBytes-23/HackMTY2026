import networkx as nx

G = nx.MultiDiGraph()

G.add_edge(
    "A",
    "B",
    key="T001",
    amount=100,
)

G.add_edge(
    "A",
    "B",
    key="T002",
    amount=500,
)

G.add_edge(
    "A",
    "B",
    key="T003",
    amount=200,
)

print(G.edges(data=True, keys=True))
