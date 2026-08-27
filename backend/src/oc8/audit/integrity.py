"""Tamper detection for the per-tenant audit hash chain (tech-spec §12.5).

audit_event is append-only at the database level (migration 0001 REVOKEs
UPDATE/DELETE from oc8_app), so this guards against tampering that happens
*around* the app: direct database access, or a doctored backup restore.

Verification is incremental: audit_chain_checkpoint remembers how far the chain
was proven intact, and each run only re-hashes what arrived since. A detected
break does NOT advance the checkpoint past itself -- the checkpoint always
retreats to the last-good position -- so the break is re-detected on every
later run. verify_incremental can never clear a "broken" status on its own;
only an operator-invoked verify_full (a full re-verification from genesis) may
return a tenant to "ok".

Callers should verify in a fresh session. _walk loads AuditEvent rows through
the ORM identity map, so a session that already holds those rows in memory
(e.g. from an earlier query in the same request) would see stale cached
instances instead of the current database state.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import logging
import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.audit.chain import GENESIS, recompute_hash
from oc8.config import get_settings
from oc8.db.session import tenant_session
from oc8.secrets.keyprovider import SecretError, audit_mac_key
from oc8.triggers.scheduler import list_active_tenant_ids

logger = logging.getLogger(__name__)

BATCH = 1000

# A checkpoint status meaning "the verifier could not run", as distinct from
# "the chain is intact" and "the chain is broken". Reached when the MAC key is
# missing, rotated or malformed while the chain has keyed rows: recompute_hash
# then raises SecretError, which is a configuration fault and NOT evidence of
# tampering. Reporting it as broken cries wolf; letting it escape leaves the
# stored "ok" on the operator's screen over a verifier that never ran. It is
# not sticky -- it says nothing about the chain, so a later run with the key
# present returns the tenant to "ok".
UNVERIFIABLE = "unverifiable"


async def get_checkpoint(
    session: AsyncSession, tenant_id: uuid.UUID
) -> m.AuditChainCheckpoint | None:
    return (
        await session.execute(
            select(m.AuditChainCheckpoint).where(m.AuditChainCheckpoint.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()


async def _ensure_checkpoint(session: AsyncSession, tenant_id: uuid.UUID) -> m.AuditChainCheckpoint:
    cp = await get_checkpoint(session, tenant_id)
    if cp is None:
        cp = m.AuditChainCheckpoint(
            tenant_id=tenant_id,
            last_seq=0,
            last_hash=GENESIS,
            status="ok",
            max_seen_seq=0,
            verified_count=0,
        )
        session.add(cp)
        await session.flush()
    return cp


async def _walk(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    from_seq: int,
    prev_hash: bytes,
    version_floor: int,
) -> tuple[int, bytes, int | None, str | None]:
    """Verify events with seq > from_seq.

    Returns (last_seq, last_hash, broken_at, break_kind), where break_kind may
    also be UNVERIFIABLE -- not a break, but "this walk could not be performed"
    (see the constant). _run turns that into a status rather than a finding.
    """
    cursor = from_seq
    prev = prev_hash
    floor = version_floor
    while True:
        events = (
            (
                await session.execute(
                    select(m.AuditEvent)
                    .where(
                        m.AuditEvent.tenant_id == tenant_id,
                        m.AuditEvent.seq > cursor,
                    )
                    .order_by(m.AuditEvent.seq.asc())
                    .limit(BATCH)
                )
            )
            .scalars()
            .all()
        )
        if not events:
            return cursor, prev, None, None
        for ev in events:
            if ev.mac_version < floor:
                # A row may never drop to a weaker (or unkeyed) MAC: forging an
                # unkeyed row needs no secret, so accepting one would void the
                # whole scheme.
                return cursor, prev, ev.seq, "mac_downgrade"
            try:
                recomputed = recompute_hash(ev, prev)
            except SecretError:
                # SecretStoreUnavailable derives from SecretError, not from
                # ValueError, so the clause below never caught it and it
                # escaped _run entirely.
                return cursor, prev, ev.seq, UNVERIFIABLE
            except ValueError:
                # An unrecognised mac_version is not a crash, it is a finding:
                # no legitimate writer produces one. Letting the ValueError
                # escape would leave run_integrity_tick logging and moving on
                # without ever writing a checkpoint -- a green chain over a
                # tampered row, which is strictly worse than a reported break.
                return cursor, prev, ev.seq, "unknown_mac_version"
            if ev.prev_hash != prev or not hmac.compare_digest(recomputed, ev.hash):
                return cursor, prev, ev.seq, "hash_mismatch"
            floor = max(floor, ev.mac_version)
            cursor, prev = ev.seq, ev.hash


async def _tail(session: AsyncSession, tenant_id: uuid.UUID) -> tuple[int, bytes]:
    """Return (seq, hash) of the tenant's current newest row, or (0, GENESIS)
    if the tenant has no events at all right now."""
    row = (
        await session.execute(
            select(m.AuditEvent.seq, m.AuditEvent.hash)
            .where(m.AuditEvent.tenant_id == tenant_id)
            .order_by(m.AuditEvent.seq.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return 0, GENESIS
    return row.seq, row.hash


async def _count_upto(session: AsyncSession, tenant_id: uuid.UUID, seq: int) -> int:
    """How many of this tenant's events currently sit at or below `seq`."""
    return (
        await session.execute(
            select(func.count())
            .select_from(m.AuditEvent)
            .where(m.AuditEvent.tenant_id == tenant_id, m.AuditEvent.seq <= seq)
        )
    ).scalar_one()


async def _version_floor(session: AsyncSession, tenant_id: uuid.UUID, upto_seq: int) -> int:
    """The highest mac_version seen at or below upto_seq.

    Derived rather than stored: a value in the checkpoint could drift out of
    sync with the rows, and max() is exactly the monotonicity floor a later row
    must not fall below.
    """
    return (
        await session.execute(
            select(func.coalesce(func.max(m.AuditEvent.mac_version), 0)).where(
                m.AuditEvent.tenant_id == tenant_id,
                m.AuditEvent.seq <= upto_seq,
            )
        )
    ).scalar_one()


async def _keyed_count(
    session: AsyncSession, tenant_id: uuid.UUID, cp: m.AuditChainCheckpoint
) -> int:
    """How many rows at or below the checkpointed mark still carry the version
    the checkpoint remembers, RIGHT NOW.

    The one quantity a later append cannot restore. max(mac_version) over the
    whole chain cannot distinguish a laundered chain from a normal migration:
    after a wholesale downgrade, ONE honest append puts a v1 row back on the
    head and both shapes read 0,0,...,0,1. This count is measured strictly
    BELOW max_seen_seq, where no honest append can ever land (seq is a global
    identity column and every new row's seq is above the mark), so a wholesale
    rewrite drops it to 0 and nothing can raise it again.

    Compared against cp.keyed_count, the signed figure from the last clean
    pass. Only a DECREASE is a finding: an increase is not reachable by any
    downgrade, and the count is deliberately tolerant upward so that a restore
    that brings rows back can never itself read as an attack.
    """
    return (
        await session.execute(
            select(func.count())
            .select_from(m.AuditEvent)
            .where(
                m.AuditEvent.tenant_id == tenant_id,
                m.AuditEvent.seq <= cp.max_seen_seq,
                m.AuditEvent.mac_version >= cp.max_mac_version,
            )
        )
    ).scalar_one()


def _state_mac(cp: m.AuditChainCheckpoint) -> bytes:
    """A MAC over the checkpoint's own security-relevant state.

    Binds tenant, the highest mac_version ever reached, the truncation evidence
    pair, the keyed-row count below the mark, and the incremental walk's
    resumption point -- so none of them can be rolled back by someone who does
    not hold the key.

    This is the one piece of the scheme the hash chain cannot supply. Every
    other marker of "this tenant is keyed" lives in a plain column that the
    same attacker who can rewrite audit_event can rewrite too; a wholesale
    downgrade (every row set to mac_version 0 and re-hashed with unkeyed
    sha256) is monotonic, self-consistent and verifies perfectly. Only a
    marker they cannot forge without the key catches it.

    keyed_count is read from the checkpoint COLUMN, not re-derived live. The
    column is inside the message, so an attacker can lower it but cannot
    re-sign it, and lowering it without the key fails authentication -- the
    wholesale-downgrade attack stays closed. Deriving it live at both ends
    instead looked simpler but could not survive a truncation: see the column's
    comment in models.ops. The live figure is still taken, once, and compared
    against this one (live < stored is the downgrade evidence).

    last_seq/last_hash are the incremental walk's RESUMPTION POINT. Without
    them in the message, an attacker who rewrites the chain can also point the
    checkpoint at the rewritten tail: every signed field stays put, the marker
    still authenticates, and the next incremental walk resumes from the forged
    tail and links cleanly.
    """
    msg = (
        f"{cp.tenant_id}|{cp.max_mac_version}|{cp.max_seen_seq}|{cp.verified_count}"
        f"|{cp.keyed_count}|{cp.last_seq}|{cp.last_hash.hex()}"
    ).encode()
    return hmac.new(audit_mac_key(), msg, hashlib.sha256).digest()


def _apply(
    cp: m.AuditChainCheckpoint,
    last_seq: int,
    last_hash: bytes,
    broken_at: int | None,
    break_kind: str | None,
    *,
    allow_recovery: bool,
) -> m.AuditChainCheckpoint:
    """Persist one verification pass onto the checkpoint.

    last_seq/last_hash always move to the last position this pass actually
    proved intact -- even when broken_at is set -- so the checkpoint never
    sits *past* an unresolved break. The next run walks forward from before
    the break and re-detects it, instead of treating the gap beyond it as
    new, unverified territory (that was the laundering bug: leaving the
    checkpoint past the break let a later "no new rows" result look clean).

    allow_recovery gates whether an ok result (broken_at is None) may clear
    an existing "broken" status. Only verify_full sets this True. This makes
    the "only an operator clears a break" rule a hard invariant of this
    function rather than an emergent property of the walk always
    re-detecting the same break -- verify_incremental must never flip
    "broken" back to "ok" by itself, full stop.

    first_break_at is write-once: clearing status back to "ok" is the
    operator's acknowledgement, but it must never erase the fact that a break
    was observed at all.
    """
    cp.last_seq = last_seq
    cp.last_hash = last_hash
    if broken_at is not None:
        cp.status = "broken"
        cp.broken_at_seq = broken_at
        cp.break_kind = break_kind
        if cp.first_break_at is None:
            cp.first_break_at = dt.datetime.now(tz=dt.UTC)
    elif allow_recovery or cp.status != "broken":
        cp.status = "ok"
        cp.broken_at_seq = None
        cp.break_kind = None
    # else: this pass found nothing newly wrong, but a prior break was never
    # cleared by an operator via verify_full -- leave status/broken_at_seq
    # exactly as they were.
    cp.verified_at = dt.datetime.now(tz=dt.UTC)
    return cp


async def _run(
    session: AsyncSession, tenant_id: uuid.UUID, *, full: bool
) -> m.AuditChainCheckpoint:
    cp = await _ensure_checkpoint(session, tenant_id)
    # Deletion check, applied in BOTH incremental and full mode. _walk only
    # ever sees "no rows past the cursor", which it treats as success -- but
    # that looks identical to "nothing new happened" when rows were deleted
    # outright, and a walk over the survivors of a head-truncation is a
    # perfectly valid chain from genesis, so a full pass would look clean too.
    #
    # The invariant is a COUNT of rows at or below a FIXED high-water mark, not
    # the mark itself. seq is a GLOBAL identity column, so a mark comparison
    # (tail_seq < max_seen_seq) goes quiet the moment one new event is appended
    # -- its seq climbs back over the mark, and on a live tenant every approval
    # decision appends. A later append cannot inflate this count, because its
    # seq is above the mark. So the count stays short until the missing rows
    # actually come back from a restore, which is the only thing that should
    # clear a truncation. It also catches a deletion strictly inside an
    # already-checkpointed range, which no tail comparison can see.
    high_water = cp.max_seen_seq
    present = await _count_upto(session, tenant_id, high_water)
    truncated = present < cp.verified_count
    # The mirror case, and the other thing the same count buys. A legitimate
    # append always lands ABOVE max_seen_seq, so it can never add to the count
    # at or below the mark: present > verified_count is not reachable by any
    # honest writer. It IS reachable by a forged row given an explicit seq
    # inside the checkpointed range -- seq is Identity(always=True), but
    # OVERRIDING SYSTEM VALUE needs only the INSERT privilege migration 0001
    # leaves oc8_app, and an incremental walk (seq > cp.last_seq) never looks
    # back down there. The truncation guard cannot see it: an insertion RAISES
    # the count.
    inserted = present > cp.verified_count
    # Re-read from genesis when it fires, so the row is located and classified
    # by the same walk that classifies every other break rather than guessed at
    # from a count. allow_recovery below stays tied to the CALLER's `full`:
    # this is a forced re-read, not an operator's acknowledgement, and it must
    # not gain the power to clear a standing break.
    walk_full = full or inserted

    from_seq, prev_hash = (0, GENESIS) if walk_full else (cp.last_seq, cp.last_hash)
    version_floor = await _version_floor(session, tenant_id, from_seq)
    last_seq, last_hash, broken_at, walk_kind = await _walk(
        session,
        tenant_id,
        from_seq=from_seq,
        prev_hash=prev_hash,
        version_floor=version_floor,
    )
    tail_seq, tail_hash = await _tail(session, tenant_id)

    # An inconclusive walk is not a finding: discard its position so nothing
    # downstream reads it as a break. The truncation check below is pure
    # counting and needs no key, so it still gets to speak.
    unverifiable = walk_kind == UNVERIFIABLE
    if unverifiable:
        broken_at, walk_kind = None, None

    break_kind: str | None = None
    if truncated:
        # Missing rows outrank a hash mismatch: a mismatch found while rows are
        # gone is a symptom of the deletion (the surviving neighbour no longer
        # links to its predecessor), and only the restore fixes either.
        break_kind = "truncation"
        if broken_at is None:
            last_seq, last_hash = tail_seq, tail_hash
            if cp.break_kind == "truncation" and cp.broken_at_seq is not None:
                # Sticky: keep pointing at where the entries went missing.
                # Without this the reported position would drift up to the new
                # tail on every append and the operator copy ("entries recorded
                # after seq N are no longer present") would become a lie.
                broken_at = cp.broken_at_seq
            else:
                # The tenant's current max(seq), or -- when nothing is left at
                # all (tail_seq == 0, which no real seq value ever is, seq
                # being a global Identity column starting above 0) -- the
                # high-water mark instead.
                broken_at = tail_seq or high_water
    elif broken_at is not None:
        break_kind = walk_kind
    elif inserted:
        # The re-walk found every link internally consistent, yet rows exist
        # below the mark that were not there at the last clean pass. Reported
        # as hash_mismatch rather than a new break_kind: a full
        # re-verification after a legitimate restore is the same remedy path,
        # and unlike a truncation there is no missing_count to report.
        broken_at, break_kind = high_water, "hash_mismatch"

    # The chain cannot police its own mac_version: it is a plain column, not
    # part of the hashed payload. Everything below is about the ONE marker an
    # attacker with UPDATE cannot forge -- the checkpoint's keyed state MAC.
    keyed = get_settings().audit_mac_enabled
    chain_version = await _version_floor(session, tenant_id, tail_seq)

    # Whether this pass is allowed to (re-)sign the marker at the end. Cleared
    # the moment the marker fails to authenticate: re-signing then would launder
    # exactly the edit that was just caught, and the break must stay sticky.
    may_sign = True

    if break_kind != "truncation":
        # Skipped while rows are missing: truncation keeps its precedence (see
        # above), and a chain whose keyed rows were deleted would otherwise be
        # reported as a downgrade, sending the operator after the wrong remedy.
        if cp.state_mac is not None:
            # The tenant has been keyed before. Verify the marker itself, then
            # verify the chain still lives up to it.
            try:
                expected = _state_mac(cp)
            except SecretError:
                # The marker cannot even be checked without the key. Same fault
                # as a walk that could not run, and the same answer: say so,
                # rather than accusing anyone of tampering.
                unverifiable = True
                may_sign = False
            else:
                # Either the marker was rewritten, or one of the quantities it
                # binds moved (the signed keyed-row count, the resumption
                # point, the truncation pair), or the chain no longer lives up
                # to what the marker remembers: fewer keyed rows below the mark
                # than were signed for, or a top version below the signed one
                # -- i.e. every row rewritten at a lower version, which is
                # self-consistent and invisible to the walk.
                #
                # Strictly `<` on the count, not `!=`: the count may legitimately
                # come back UP (a restore returning rows below the mark), and an
                # increase is not reachable by any downgrade.
                live_keyed = await _keyed_count(session, tenant_id, cp)
                if (
                    not hmac.compare_digest(expected, cp.state_mac)
                    or chain_version < cp.max_mac_version
                    or live_keyed < cp.keyed_count
                ):
                    broken_at, break_kind = broken_at or cp.last_seq, "mac_downgrade"
                    may_sign = False
        elif keyed and cp.verified_count > 0:
            # ERASURE. Deleting the marker is not forgery, so nothing above can
            # see it -- yet one UPDATE setting state_mac = NULL and
            # max_mac_version = 0 was enough to make the whole scheme skip
            # itself, after which a wholesale downgrade verifies clean and the
            # clean branch signs a fresh marker over "never keyed".
            #
            # audit_mac_enabled comes from the deployment's environment, not
            # from any row the attacker can reach. On a keyed deployment every
            # clean pass signs, so a checkpoint that has verified something
            # (verified_count > 0) and yet carries no marker can only have had
            # one removed. The one legitimate way into this state -- turning
            # the flag on for the first time over checkpoints written while it
            # was off -- is an operator event, adopted once by migration 0031;
            # after that this is fail-closed by design, because the two states
            # are otherwise information-theoretically identical.
            broken_at, break_kind = broken_at or cp.last_seq, "mac_downgrade"
            may_sign = False

    if broken_at is None and (keyed or cp.max_mac_version > 0 or chain_version > 0):
        # A clean pass would re-sign the marker below, which needs the key.
        # Checked here rather than caught there, so the signing site cannot
        # half-advance the evidence and then fail.
        #
        # `or chain_version > 0` closes the gap between this condition and the
        # signing site's, which tests the POST-update max(cp.max_mac_version,
        # chain_version). They diverged exactly where migration 0030 leaves a
        # deployment: a checkpoint backfilled with max_mac_version = 0 over a
        # chain that is already keyed, restarted with the flag off and the KEK
        # gone. The walk finds no new rows so nothing raises, the state block
        # is skipped -- and the clean branch then advances the evidence for a
        # marker it cannot write.
        try:
            audit_mac_key()
        except SecretError:
            unverifiable = True

    if unverifiable and break_kind is None:
        # The run completed; it just could not conclude. Every piece of
        # evidence -- broken_at_seq, break_kind, the truncation pair, last_seq
        # -- is left exactly as it was, so nothing is advanced on the strength
        # of a walk that did not happen and nothing already recorded is lost.
        #
        # The STORED status decides, not this run's verdict. break_kind above
        # is what THIS pass found, and a pass that could not run finds nothing
        # -- so testing it let "unverifiable" overwrite a tenant already
        # recorded broken, leaving status=unverifiable beside a surviving
        # break_kind (an incoherent DTO) and silencing every alert keyed on
        # status == "broken" for as long as the key stays missing. A standing
        # break outlives a verifier outage: only verify_full may clear it.
        if cp.status == "broken":
            cp.verified_at = dt.datetime.now(tz=dt.UTC)
            await session.flush()
            return cp
        logger.warning(
            "audit chain UNVERIFIABLE for tenant %s: the MAC key is unavailable",
            tenant_id,
        )
        cp.status = UNVERIFIABLE
        cp.verified_at = dt.datetime.now(tz=dt.UTC)
        await session.flush()
        return cp

    if broken_at is None:
        # ONLY a clean pass may advance the evidence. max() keeps the mark
        # monotonic; the count is then re-derived at the new mark, so the pair
        # always describes the same instant.
        cp.max_seen_seq = max(high_water, tail_seq, last_seq)
        cp.verified_count = await _count_upto(session, tenant_id, cp.max_seen_seq)
        cp.max_mac_version = max(cp.max_mac_version, chain_version)
        # Re-derived at the NEW mark and version, so all four describe the same
        # instant. Only here: a break path must keep the last clean figure, or
        # the restore that fixes the break would look like a downgrade.
        cp.keyed_count = await _keyed_count(session, tenant_id, cp)
    else:
        logger.error(
            "audit chain break detected: tenant_id=%s broken_at_seq=%s kind=%s",
            tenant_id,
            broken_at,
            break_kind,
        )
    _apply(cp, last_seq, last_hash, broken_at, break_kind, allow_recovery=full)
    if may_sign and (keyed or cp.max_mac_version > 0):
        # `or cp.max_mac_version > 0`: a tenant that was keyed must keep a
        # valid marker even if a later run happens with the flag off, so the
        # marker cannot be shed by flipping a setting.
        #
        # Signed AFTER _apply, and on break paths too, because the message
        # binds last_seq/last_hash and _apply is what moves them -- a marker
        # signed before the retreat would fail to authenticate on the very next
        # run and turn an honest hash mismatch into a phantom downgrade. This
        # is safe: may_sign is False whenever the marker itself failed, so
        # nothing an attacker touched is ever re-signed, and the break branch
        # never lowers max_mac_version, keyed_count or the evidence pair.
        #
        # cp.keyed_count is whatever the last CLEAN pass stored -- untouched
        # above on a break path. That is what makes truncate-then-restore work:
        # the re-sign carries the full pre-truncation count forward, so when
        # the rows come back the live figure matches it again instead of
        # exceeding a count signed while rows were missing.
        try:
            cp.state_mac = _state_mac(cp)
        except SecretError:
            # Only reachable on a break path (a clean pass pre-flights the key
            # above). Leave the previous marker rather than clearing it:
            # clearing is the one edit the erasure check treats as an attack.
            logger.error(
                "audit chain: could not re-sign the checkpoint marker for tenant %s "
                "(the MAC key is unavailable); the stored marker is now stale",
                tenant_id,
            )
    await session.flush()
    return cp


async def verify_incremental(session: AsyncSession, tenant_id: uuid.UUID) -> m.AuditChainCheckpoint:
    return await _run(session, tenant_id, full=False)


async def verify_full(session: AsyncSession, tenant_id: uuid.UUID) -> m.AuditChainCheckpoint:
    return await _run(session, tenant_id, full=True)


async def run_integrity_tick(*, full: bool = False) -> int:
    """Verify every tenant's chain. One tenant's failure never aborts the tick.

    Each tenant gets its own fresh tenant_session -- see the module docstring
    on why re-verifying inside a session that already loaded AuditEvent rows
    (via the ORM identity map) would see stale in-memory instances instead of
    current database state."""
    verified = 0
    for tenant_id in await list_active_tenant_ids():
        try:
            async with tenant_session(tenant_id) as db:
                cp = await (verify_full if full else verify_incremental)(db, tenant_id)
                if cp.status == "broken":
                    logger.error(
                        "audit chain BROKEN for tenant %s at seq %s",
                        tenant_id,
                        cp.broken_at_seq,
                    )
            verified += 1
        except Exception:
            logger.exception("audit integrity tick failed for tenant %s", tenant_id)
    return verified


async def adopt_checkpoints(tenant_ids: Sequence[uuid.UUID] | None = None) -> int:
    """Sign a marker for every checkpoint that has none. Returns how many.

    The in-band counterpart to migration 0031's adoption pass, and the reason
    it is needed: 0031 adopts only if audit_mac_enabled happens to be on WHEN
    THE MIGRATION RUNS, but the normal rollout ships migrations first and turns
    the flag on afterwards. In that ordering every checkpoint that has verified
    something and carries no marker hits _run's erasure branch on the first
    keyed tick and is reported as mac_downgrade -- deliberately unclearable in
    band, because "the flag was just turned on" and "an attacker deleted the
    marker" are information-theoretically identical states.

    Telling them apart is an operator act, so it is an operator COMMAND rather
    than a runbook full of hand-written SQL. It is deliberately narrow: it only
    ever writes a marker where there is none, so it can never launder a marker
    that failed to authenticate, and running it twice is a no-op.

    tenant_ids narrows the pass to specific tenants; the default is every known
    tenant, which is what the rollout wants.
    """
    if not get_settings().audit_mac_enabled:
        # Signing markers on a deployment that is not keyed would write
        # evidence nothing will check and that a later keyed run must reject.
        raise RuntimeError("audit MAC is not enabled; nothing to adopt")
    audit_mac_key()  # fail fast, before touching any tenant
    adopted = 0
    targets = list(tenant_ids) if tenant_ids is not None else await list_active_tenant_ids()
    for tenant_id in targets:
        async with tenant_session(tenant_id) as db:
            cp = await get_checkpoint(db, tenant_id)
            # verified_count == 0 is a fresh checkpoint: it signs itself on its
            # first clean pass, and the erasure branch never fires for it.
            if cp is None or cp.state_mac is not None or cp.verified_count == 0:
                continue
            cp.keyed_count = await _keyed_count(db, tenant_id, cp)
            cp.state_mac = _state_mac(cp)
            await db.flush()
            adopted += 1
            logger.info("audit chain: adopted the checkpoint for tenant %s", tenant_id)
    return adopted


async def run_integrity_job(
    *, once: bool = False, interval_seconds: float = 3600.0, full: bool = False
) -> None:
    while True:
        try:
            await run_integrity_tick(full=full)
        except Exception:
            logger.exception("unhandled error in audit integrity tick")
        if once:
            return
        await asyncio.sleep(interval_seconds)
