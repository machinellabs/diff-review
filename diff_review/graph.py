from langgraph.graph import StateGraph, START, END
from .schema import ReviewState
from .nodes import parse_diff, review_chunks, synthesize


def build_graph():
    graph = StateGraph(ReviewState)

    graph.add_node("parse_diff", parse_diff)
    graph.add_node("review_chunks", review_chunks)
    graph.add_node("synthesize", synthesize)

    graph.add_edge(START, "parse_diff")
    graph.add_edge("parse_diff", "review_chunks")
    graph.add_edge("review_chunks", "synthesize")
    graph.add_edge("synthesize", END)

    return graph.compile()
