"""Container names an operator can attribute at a glance.

`docker ps` used to show Docker's own random names (`tender_lamarr`), so telling
which container belonged to which agent meant inspecting each one's mounts.
"""

from __future__ import annotations

import re
import uuid

from oc8.sandbox.naming import container_name

# Docker's own rule for a container name.
DOCKER_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


def test_a_name_says_which_agent_and_which_job() -> None:
    name = container_name("Nora", "agent", uuid.UUID("019fa438-f895-7d71-8a17-9685f4c7d3e6"))
    assert name.startswith("oc8-nora-agent-")
    assert DOCKER_NAME.match(name)


def test_names_from_the_same_inputs_still_differ() -> None:
    """Two containers exist for one run whenever it parks and resumes, and a
    leftover from a crashed run may still hold the name. Docker rejects a
    duplicate outright, so the suffix has to be unique per CONTAINER."""
    run = uuid.uuid4()
    assert container_name("Nora", "agent", run) != container_name("Nora", "agent", run)


def test_an_unruly_agent_name_still_produces_a_legal_one() -> None:
    """Agent names are free text a tenant types: umlauts, spaces, emoji, slashes.
    Passing one straight to Docker fails the run at provision time."""
    for raw in ("Kundenservice Süd/West", "  ", "···", "Ünïcødé 💥 Agent", "-leading-dash"):
        name = container_name(raw, "agent", uuid.uuid4())
        assert DOCKER_NAME.match(name), raw


def test_a_very_long_agent_name_is_truncated_not_rejected() -> None:
    name = container_name("A" * 300, "provision", uuid.uuid4())
    assert DOCKER_NAME.match(name)
    assert len(name) < 64


def test_the_run_id_in_the_name_is_the_real_prefix() -> None:
    """The point of carrying it: paste it back into a `GET /runs/<id>` query."""
    run = uuid.UUID("019fa438-f895-7d71-8a17-9685f4c7d3e6")
    assert "019fa438" in container_name("Nora", "agent", run)
