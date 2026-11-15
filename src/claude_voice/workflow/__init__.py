"""The workflow collector: where work gets stuck, measured from what the bridge can see.

Each source has one collector, which yields raw items. The core gates them into
events of a fixed, versioned format and appends them to a local log. One analysis
reads the log, and views read only the analysis. docs/workflow-event.md describes
the format.
"""
