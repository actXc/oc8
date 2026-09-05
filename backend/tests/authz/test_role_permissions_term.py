"""Tests for the role_permissions term - REMOVED.

This file previously tested the role_rights parameter of effective_tool_policies
and the agent_tool_rights function. Both have been removed as part of dropping
the dead "role" term from the permission algebra (Task 1 of the guardrails redesign).

The role term was never actually restricting anything on the live system, since
all agents have role_id = NULL, which resolved to the default agent role granting
all rights. The frame and narrowing alone were doing the work, making the role
term dead code.
"""
