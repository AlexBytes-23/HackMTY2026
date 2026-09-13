from graph_engine.analyzer import (
    analyze_graph,
)
from graph_engine.mock_data import (
    MOCK_GRAPH_INPUT,
)

result = analyze_graph(MOCK_GRAPH_INPUT)

print(result.model_dump_json(indent=2))
