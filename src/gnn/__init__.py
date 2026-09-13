"""Optional graph-neural discovery components.

Nothing in this package is evidentiary authority.  The GNN layer may surface
candidates for investigation, while exact estate records remain the source of
forensic evidence.
"""

from .contracts import GNNGraphBundle
from .graph_builder import build_gnn_graph

__all__ = ["GNNGraphBundle", "build_gnn_graph"]
