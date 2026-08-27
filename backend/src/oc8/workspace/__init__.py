"""The employee's half of the product: the work a person is actually asked for.

Deliberately its own package rather than a second module under `approvals/`. An
approval and a clarification are one queue on the screen (§7) and two different
rows in the database, and the thing they share is the department term -- not a
table. `oc8.approvals` is the funnel that decides a held action; this is where
the reads and answers a HUMAN performs live.
"""
