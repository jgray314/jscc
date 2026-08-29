"""Deterministic synthetic seed — evaluation infrastructure for JSCC.

This is not just a demo fixture. It is the substrate every downstream eval
runs against: extractor evals check that the LLM recovers known facts,
scorer evals check band placement against a known profile, drafter evals
check routing on canned routine / non-routine situations. Bit-reproducibility
matters — evals that drift because the fixture drifted are eval theater.

Reproducibility contract: given the same `--random-seed` and the same `--now`,
this module produces byte-identical rows across runs. All UUIDs are drawn from
the seeded RNG (not `uuid4()`) and all timestamps are anchored on `now` (not
wall clock).

Content contract: obviously fake company names, obviously fake contact names
("Placeholder"), and role-tagged interaction chains. Per D7/D8 (parent plan
Rule 0), personal-data-shaped strings do not belong here even in synthetic
mode — the synthetic fixture is committed to a public repo.

Chain generation rule: interactions are anchored on `applied_at` and stepped
forward with realistic gaps, so `list_interactions()` returns events in
chronological order. `last_interaction_at` is the timestamp of the final event.
"""
from __future__ import annotations

import random
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone

from .models import (
    Application,
    Contact,
    ContactRole,
    DLQEntry,
    FailureMode,
    FetchStatus,
    Interaction,
    InteractionType,
    Resolution,
)
from .storage import (
    create_application,
    create_contact,
    create_dlq_entry,
    create_interaction,
    resolve_dlq_entry,
)

DEFAULT_SEED = 42


def _rng_uuid(rng: random.Random) -> str:
    """Return a UUID4-shaped string whose entropy comes from `rng`, not the OS.

    The default pydantic `Field(default_factory=uuid4)` on our models uses
    `os.urandom`, which is not affected by the seed. Every model construction
    in this module passes an explicit `id=_rng_uuid(rng)` so the whole
    fixture is reproducible from (`random_seed`, `now`) alone.
    """
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))

_COMPANIES = [
    "Acme Robotics", "Bluewave Systems", "Ceres Analytics", "Delta Foundry",
    "Ember Grid", "Falcon Ledger", "Gale Networks", "Helix Compute",
    "Ionic Studios", "Juno Labs", "Karma Freight", "Latitude AI",
    "Meridian Payments", "Nova Health", "Orbit Media", "Pinnacle Search",
    "Quartz Signals", "Rift Cloud", "Sable Data", "Timber Motors",
    "Umbra Security", "Vertex Loop", "Wharf Logistics", "Xenon Retail",
    "Yield Model Co",
]

_TITLES = [
    "Engineering Manager, Platform",
    "Senior Engineering Manager, Payments",
    "Staff Software Engineer, Infra",
    "Principal Engineer, Data",
    "Director of Engineering, ML",
    "Engineering Manager, Growth",
    "Head of Platform Engineering",
    "Senior Manager, Developer Experience",
    "Staff MLE, Foundations",
    "Engineering Manager, Reliability",
]

_STAGE_DISTRIBUTION: list[tuple[str, int]] = [
    ("identified", 6),
    ("applied", 8),
    ("recruiter_screen", 3),
    ("hm_screen", 3),
    ("technical_loop", 2),
    ("onsite", 1),
    ("closed", 2),
]

# How long ago (in days) the application was submitted, per current stage.
# Broad enough that later chain steps still leave a mix of fresh + stale apps.
_APPLIED_AGE_DAYS: dict[str, tuple[int, int]] = {
    "identified": (0, 20),   # identified only — no applied_at, used as identified_at
    "applied": (0, 40),
    "recruiter_screen": (5, 30),
    "hm_screen": (10, 45),
    "technical_loop": (14, 55),
    "onsite": (21, 60),
    "offer": (30, 75),
    "closed": (30, 120),
}

_STAGE_ORDER = [
    "identified", "applied", "recruiter_screen", "hm_screen",
    "technical_loop", "onsite", "offer", "closed",
]

_RESPONSIBILITY_POOLS: dict[str, list[str]] = {
    "platform": [
        "own the platform roadmap across two quarters ahead",
        "define API contract standards for internal services",
        "run capacity planning and cost modeling for the fleet",
        "align infra investment with product priorities",
        "coach senior engineers into staff-level scope",
        "partner with security on the platform threat model",
    ],
    "ml": [
        "own the training pipeline for production models",
        "define offline and online evals for shipping model changes",
        "instrument regressions in production model quality",
        "partner with research on productionization of new architectures",
        "coach engineers on ML system design and error budgets",
    ],
    "growth": [
        "own funnel instrumentation and experiment infrastructure",
        "partner with product on experiment design and readouts",
        "reduce time-to-first-value for new users",
        "build the analytics primitives that PMs and marketers rely on",
    ],
    "payments": [
        "own SLAs for payment settlement and dispute handling",
        "harden the fraud detection pipeline against emerging patterns",
        "partner with compliance on gateway and rail changes",
        "reduce time-to-onboard for a new payment method",
    ],
    "reliability": [
        "cut mean-time-to-recovery on tier-0 services in half",
        "lead post-incident learning reviews and follow-through",
        "own SLOs and error budgets across the org",
        "grow the SRE bench and its on-call practice",
    ],
    "devex": [
        "reduce local dev setup time to under 10 minutes",
        "define and enforce golden paths for new services",
        "own the CI budget and reliability targets",
        "reduce PR-merge lead time by 40 percent over the year",
    ],
    "data": [
        "own the data platform investment plan across two years",
        "define quality SLAs on the core datasets product depends on",
        "reduce the time to answer new product questions",
        "grow the data-engineering bench from four to eight",
    ],
    "general": [
        "grow senior engineers into staff and staff into principal",
        "partner with product on strategy and prioritization",
        "own the technical vision for the team",
        "define the hiring bar and grow the bench thoughtfully",
        "shape the roadmap in partnership with the PM triad",
    ],
}


def _responsibility_pool_for(title: str) -> list[str]:
    t = title.lower()
    if "platform" in t or "infra" in t:
        return _RESPONSIBILITY_POOLS["platform"]
    if "mle" in t or " ml" in t or t.startswith("ml") or "model" in t:
        return _RESPONSIBILITY_POOLS["ml"]
    if "growth" in t:
        return _RESPONSIBILITY_POOLS["growth"]
    if "payment" in t:
        return _RESPONSIBILITY_POOLS["payments"]
    if "reliability" in t or "sre" in t:
        return _RESPONSIBILITY_POOLS["reliability"]
    if "developer experience" in t or "devex" in t:
        return _RESPONSIBILITY_POOLS["devex"]
    if "data" in t:
        return _RESPONSIBILITY_POOLS["data"]
    return _RESPONSIBILITY_POOLS["general"]


def _pick_extracted(rng: random.Random, title: str) -> dict[str, object] | None:
    """Half of applications carry an extracted_jd; half don't (mirrors real ingestion mix)."""
    if rng.random() < 0.5:
        return None
    level = rng.choice(["L5", "L6", "L7", "M5", "M6"])
    stack = rng.sample(
        ["python", "go", "rust", "typescript", "kubernetes", "postgres", "spark"], k=3
    )
    pool = _responsibility_pool_for(title)
    responsibilities = rng.sample(pool, k=min(3, len(pool)))
    return {
        "level": level,
        "responsibilities": responsibilities,
        "stack": stack,
        "comp_band": {
            "min_usd": rng.choice([250_000, 300_000, 340_000]),
            "max_usd": rng.choice([450_000, 500_000, 600_000]),
        },
        "title_verbatim": title,
    }


def _placeholder_contact_letter(rng: random.Random) -> str:
    return rng.choice("ABCDEFGHJKMNPQRSTUVWXYZ")


def _effective_depth(rng: random.Random, stage: str) -> int:
    """Return the max stage index that has generated actual interactions.

    For most stages this is just the stage's own index. For `closed`, closure
    can happen at any point in the loop, so we randomize the depth reached
    before rejection.
    """
    if stage == "closed":
        # Weighted so early closures are more common than late ones — matches reality
        # for candidates who withdraw or get passed early.
        return rng.choices(
            [_STAGE_ORDER.index("applied"),
             _STAGE_ORDER.index("recruiter_screen"),
             _STAGE_ORDER.index("hm_screen"),
             _STAGE_ORDER.index("onsite")],
            weights=[3, 4, 3, 2],
        )[0]
    return _STAGE_ORDER.index(stage)


def _build_contacts(
    rng: random.Random,
    application_id: str,
    depth: int,
) -> tuple[list[Contact], dict[ContactRole, Contact]]:
    contacts: list[Contact] = []
    by_role: dict[ContactRole, Contact] = {}
    if depth >= _STAGE_ORDER.index("recruiter_screen"):
        recruiter = Contact(
            id=_rng_uuid(rng),
            application_id=application_id,
            name=f"Recruiter {_placeholder_contact_letter(rng)}. Placeholder",
            role=ContactRole.recruiter,
            notes="Synthetic contact.",
        )
        contacts.append(recruiter)
        by_role[ContactRole.recruiter] = recruiter
    if depth >= _STAGE_ORDER.index("hm_screen"):
        hm = Contact(
            id=_rng_uuid(rng),
            application_id=application_id,
            name=f"HM {_placeholder_contact_letter(rng)}. Placeholder",
            role=ContactRole.hm,
            notes="Synthetic contact.",
        )
        contacts.append(hm)
        by_role[ContactRole.hm] = hm
    return contacts, by_role


def _build_chain(
    rng: random.Random,
    application_id: str,
    stage: str,
    applied_at: datetime,
    contacts_by_role: dict[ContactRole, Contact],
    depth: int,
    now: datetime,
) -> tuple[list[Interaction], datetime]:
    """Return chronologically-ordered interactions and the final event timestamp.

    Cursor advances by realistic gaps between events. Steps included depend on
    `depth` (the effective stage reached). For `closed` applications a final
    rejection event is appended.

    Chain end is clamped to `now`: an interaction that would land in the future
    is dropped. Synthetic fixtures must not emit future timestamps — the report
    module now raises on them (M6 regression).
    """
    events: list[Interaction] = []
    recruiter = contacts_by_role.get(ContactRole.recruiter)
    hm = contacts_by_role.get(ContactRole.hm)

    if stage == "identified":
        return [], applied_at

    cursor = applied_at
    events.append(
        Interaction(
            id=_rng_uuid(rng),
            application_id=application_id,
            contact_id=None,
            type=InteractionType.applied,
            occurred_at=cursor,
            notes="submitted via portal",
        )
    )

    if depth >= _STAGE_ORDER.index("recruiter_screen"):
        cursor = cursor + timedelta(days=rng.randint(3, 10))
        if cursor > now:
            return events, events[-1].occurred_at
        events.append(
            Interaction(
                id=_rng_uuid(rng),
                application_id=application_id,
                contact_id=recruiter.id if recruiter else None,
                type=InteractionType.recruiter_reply,
                occurred_at=cursor,
                notes="recruiter reached out; scheduled intro",
                next_action="prep intro call",
                next_action_due=(cursor + timedelta(days=rng.randint(2, 7))).date(),
            )
        )

    if depth >= _STAGE_ORDER.index("hm_screen"):
        cursor = cursor + timedelta(days=rng.randint(5, 12))
        if cursor > now:
            return events, events[-1].occurred_at
        events.append(
            Interaction(
                id=_rng_uuid(rng),
                application_id=application_id,
                contact_id=hm.id if hm else None,
                type=InteractionType.screen,
                occurred_at=cursor,
                notes="HM screen — scope and level aligned",
            )
        )

    if depth >= _STAGE_ORDER.index("technical_loop"):
        cursor = cursor + timedelta(days=rng.randint(3, 10))
        if cursor > now:
            return events, events[-1].occurred_at
        events.append(
            Interaction(
                id=_rng_uuid(rng),
                application_id=application_id,
                contact_id=None,
                type=InteractionType.screen,
                occurred_at=cursor,
                notes="technical screen — passed to loop",
            )
        )

    if depth >= _STAGE_ORDER.index("onsite"):
        cursor = cursor + timedelta(days=rng.randint(5, 14))
        if cursor > now:
            return events, events[-1].occurred_at
        events.append(
            Interaction(
                id=_rng_uuid(rng),
                application_id=application_id,
                contact_id=hm.id if hm else None,
                type=InteractionType.onsite,
                occurred_at=cursor,
                notes="onsite complete; awaiting debrief",
            )
        )

    if stage == "closed":
        cursor = cursor + timedelta(days=rng.randint(3, 14))
        if cursor > now:
            return events, events[-1].occurred_at
        events.append(
            Interaction(
                id=_rng_uuid(rng),
                application_id=application_id,
                contact_id=None,
                type=InteractionType.rejection,
                occurred_at=cursor,
                notes="closed loop",
            )
        )

    return events, cursor


def _make_application_bundle(
    rng: random.Random,
    now: datetime,
    stage: str,
    company: str,
) -> tuple[Application, list[Contact], list[Interaction]]:
    title = rng.choice(_TITLES)
    lo, hi = _APPLIED_AGE_DAYS[stage]
    applied_days_ago = rng.randint(lo, hi)

    if stage == "identified":
        created_at = now - timedelta(days=applied_days_ago)
        applied_at: date | None = None
        applied_at_dt = created_at
    else:
        applied_at_dt = now - timedelta(days=applied_days_ago)
        applied_at = applied_at_dt.date()
        created_at = applied_at_dt - timedelta(days=rng.randint(0, 5))

    # Provisional Application so we have a stable id for contacts/interactions.
    fit_score = round(rng.uniform(35.0, 95.0), 1) if stage != "identified" else None
    fit_rationale = (
        "Skills match strong on platform; comp band tight on the low end."
        if fit_score is not None
        else None
    )
    app = Application(
        id=_rng_uuid(rng),
        source_url=f"https://jobs.example/{company.lower().replace(' ', '-')}/{rng.randint(1000, 9999)}",
        source_raw="(synthetic)",
        fetch_status=FetchStatus.ok,
        title=title,
        company=company,
        extracted_jd=_pick_extracted(rng, title),
        stage=stage,
        fit_score=fit_score,
        fit_rationale=fit_rationale,
        applied_at=applied_at,
        created_at=created_at,
        updated_at=created_at,
        last_interaction_at=None,
    )

    depth = _effective_depth(rng, stage)
    contacts, contacts_by_role = _build_contacts(rng, app.id, depth)
    interactions, chain_end = _build_chain(
        rng, app.id, stage, applied_at_dt, contacts_by_role, depth, now
    )

    if interactions:
        app = app.model_copy(update={
            "last_interaction_at": chain_end,
            "updated_at": chain_end,
        })
    else:
        # identified: no interactions; last_interaction_at stays None; updated_at = created_at
        pass

    return app, contacts, interactions


def _make_dlq_entries(rng: random.Random, now: datetime) -> list[DLQEntry]:
    modes = [
        (FailureMode.paywall, "HTTP 402 on ATS gate"),
        (FailureMode.blocked, "Cloudflare challenge; skipping"),
        (FailureMode.timeout, "readability extract exceeded 20s"),
    ]
    entries: list[DLQEntry] = []
    for i, (mode, detail) in enumerate(modes):
        entries.append(
            DLQEntry(
                id=_rng_uuid(rng),
                source_url=f"https://jobs.example/blocked/{i}",
                failure_mode=mode,
                attempted_at=now - timedelta(days=rng.randint(1, 5)),
                error_detail=detail,
            )
        )
    return entries


def build_seed(
    *,
    now: datetime | None = None,
    random_seed: int = DEFAULT_SEED,
) -> tuple[list[Application], list[Contact], list[Interaction], list[DLQEntry]]:
    """Build the seed in memory without touching the DB. Deterministic for a given seed."""
    rng = random.Random(random_seed)
    now = now or datetime.now(timezone.utc).replace(microsecond=0)

    companies = _COMPANIES.copy()
    rng.shuffle(companies)
    company_iter = iter(companies)

    apps: list[Application] = []
    contacts: list[Contact] = []
    interactions: list[Interaction] = []

    for stage, count in _STAGE_DISTRIBUTION:
        for _ in range(count):
            company = next(company_iter)
            app, cs, ints = _make_application_bundle(rng, now, stage, company)
            apps.append(app)
            contacts.extend(cs)
            interactions.extend(ints)

    dlq = _make_dlq_entries(rng, now)

    return apps, contacts, interactions, dlq


def reset_tables(conn: sqlite3.Connection) -> None:
    """Delete all rows from every JSCC table. Preserves schema."""
    for table in ("interactions", "dlq_entries", "contacts", "applications"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def seed_synthetic(
    conn: sqlite3.Connection,
    *,
    reset: bool = True,
    random_seed: int = DEFAULT_SEED,
    now: datetime | None = None,
) -> dict[str, int]:
    """Populate `conn` with the synthetic fixture. Returns counts by entity."""
    if reset:
        reset_tables(conn)

    # Pin the anchor now here (not inside build_seed) so the DLQ resolve below
    # can stamp resolved_at against the same moment build_seed used — otherwise
    # resolved_at falls back to wall clock and breaks reproducibility.
    ref_now = now if now is not None else datetime.now(timezone.utc).replace(microsecond=0)

    apps, contacts, interactions, dlq = build_seed(now=ref_now, random_seed=random_seed)

    for app in apps:
        create_application(conn, app)
    for contact in contacts:
        create_contact(conn, contact)
    for interaction in interactions:
        create_interaction(conn, interaction)
    for entry in dlq:
        create_dlq_entry(conn, entry)

    if dlq:
        resolve_dlq_entry(conn, dlq[0].id, Resolution.manual_paste, now=ref_now)

    return {
        "applications": len(apps),
        "contacts": len(contacts),
        "interactions": len(interactions),
        "dlq_entries": len(dlq),
    }
