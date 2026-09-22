"""Phase 5 — the part that proves it works.

`auto_supersede_confidence` is the most consequential setting in the project and
until now it was 0.80 because 0.80 felt right. This package replaces that with a
number: a labelled corpus, metrics that separate the two ways the system can
fail, and a sweep that shows what each gate value costs.
"""
