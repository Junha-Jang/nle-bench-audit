"""Replay and integrity tools of the audit repository for "Red-Teaming the Grader".

The tools read the files of the checkout they sit in. Nothing here is the
benchmark: the benchmark modules a replay needs are the pinned exports under
`runtime/pins/`, which are loaded in a subprocess and never installed.
"""

__version__ = "0.1.0.dev0"
