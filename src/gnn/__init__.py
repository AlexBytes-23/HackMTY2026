"""Optional graph-neural discovery subsystem.

Importing :mod:`src.gnn` must remain lightweight so the deterministic auditor can
run on machines where the optional neural stack is not installed.

Import concrete GNN components from their modules explicitly, for example::

    from src.gnn.graph_builder import build_gnn_graph

Nothing in this package is evidentiary authority.
"""
