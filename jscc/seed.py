"""Deterministic synthetic seed for the JSCC demo fixture.

Uses obviously fake company names and job titles — never anything shaped like a
real person, real employer, or real contact. Per D7/D8 (parent plan Rule 0),
personal-data-shaped strings do not belong here even in synthetic mode.
"""
from __future__ import annotations

import random
import sqlite3
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

# For each stage, the min/max days-ago range for last_interaction_at, chosen so
# the resulting fixture contains a mix of fresh and stale under typical
# staleness thresholds (see config/stages.yaml).
_STAGE_AGE_DAYS: dict[str, tuple[int, int]] = {
    "identified": (0, 20),
    "applied": (0, 40),
    "recruiter_screen": (0, 14),
    "hm_screen": (0, 14),
    "technical_loop": (2, 18),
    "onsite": (5, 15),
    "offer": (5, 45),
    "closed": (30, 90),
}


def _pick_extracted(rng: random.Random, title: str) -> dict[str, object] | None:
    """Half of applications carry an extracted_jd; half don't (mirrors real ingestion mix)."""
    if rng.random() < 0.5:
        return None
    level = rng.choice(["L5", "L6", "L7", "M5", "M6"])
    stack = rng.sample(["python", "go", "rust", "typescript", "kubernetes", "postgres", "spark"], k=3)
    return {
        "level": level,
        "responsibilities": [
            "own platform roadmap",
            "grow and coach senior engineers",
            "partner with product on strategy",
        ],
        "stack": stack,
        "comp_band": {
            "min_usd": rng.choice([250_000, 300_000, 340_000]),
            "max_usd": rng.choice([450_000, 500_000, 600_000]),
        },
        "title_verbatim": title,
    }


def _make_application(
    rng: random.Random,
    now: datetime,
    stage: str,
    company: str,
) -> Application:
    title = rng.choice(_TITLES)
    lo, hi = _STAGE_AGE_DAYS[stage]
    age = rng.randint(lo, hi)
    last_interaction_at = now - timedelta(days=age)
    created_at = last_interaction_at - timedelta(days=rng.randint(0, 10))
    applied_at = (
        (created_at.date() + timedelta(days=rng.randint(0, 3)))
        if stage != "identified"
        else None
    )
    fit_score = round(rng.uniform(35.0, 95.0), 1) if stage != "identified" else None
    fit_rationale = (
        "Skills match strong on platform; comp band tight on the low end."
        if fit_score is not None
        else None
    )
    return Application(
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
        updated_at=last_interaction_at,
        last_interaction_at=last_interaction_at,
    )


def _make_contacts_and_interactions(
    rng: random.Random,
    app: Application,
    now: datetime,
) -> tuple[list[Contact], list[Interaction]]:
    """Later-stage applications carry a recruiter/HM contact and an interaction chain."""
    contacts: list[Contact] = []
    interactions: list[Interaction] = []
    stage_order = [
        "identified", "applied", "recruiter_screen", "hm_screen",
        "technical_loop", "onsite", "offer", "closed",
    ]
    stage_idx = stage_order.index(app.stage)

    recruiter = None
    if stage_idx >= 2:  # anyone past applied has talked to a recruiter
        recruiter = Contact(
            application_id=app.id,
            name=f"Recruiter {rng.choice('ABCDEFGHJKMNPQRSTUVWXYZ')}. Placeholder",
            role=ContactRole.recruiter,
            notes="Synthetic contact.",
        )
        contacts.append(recruiter)

    if stage_idx >= 3:  # anyone past recruiter_screen has talked to an HM
        contacts.append(
            Contact(
                application_id=app.id,
                name=f"HM {rng.choice('ABCDEFGHJKMNPQRSTUVWXYZ')}. Placeholder",
                role=ContactRole.hm,
                notes="Synthetic contact.",
            )
        )

    if app.stage != "identified":
        applied_when = datetime.combine(
            app.applied_at or app.created_at.date(),
            datetime.min.time(),
            tzinfo=timezone.utc,
        )
        interactions.append(
            Interaction(
                application_id=app.id,
                contact_id=None,
                type=InteractionType.applied,
                occurred_at=applied_when,
                notes="submitted via portal",
            )
        )

    if stage_idx >= 2 and recruiter is not None:
        interactions.append(
            Interaction(
                application_id=app.id,
                contact_id=recruiter.id,
                type=InteractionType.recruiter_reply,
                occurred_at=app.last_interaction_at or now,
                notes="recruiter reached out, scheduled intro",
                next_action="prep intro call",
                next_action_due=(now + timedelta(days=rng.randint(2, 7))).date(),
            )
        )

    if stage_idx >= 4:  # technical_loop or later
        interactions.append(
            Interaction(
                application_id=app.id,
                contact_id=None,
                type=InteractionType.screen,
                occurred_at=(app.last_interaction_at or now) - timedelta(days=3),
                notes="technical screen passed",
            )
        )

    if stage_idx >= 5:  # onsite or later
        interactions.append(
            Interaction(
                application_id=app.id,
                contact_id=None,
                type=InteractionType.onsite,
                occurred_at=app.last_interaction_at or now,
                notes="onsite complete; awaiting debrief",
            )
        )

    if app.stage == "closed":
        interactions.append(
            Interaction(
                application_id=app.id,
                contact_id=None,
                type=InteractionType.rejection,
                occurred_at=app.last_interaction_at or now,
                notes="closed loop",
            )
        )

    return contacts, interactions


def _make_dlq_entries(rng: random.Random, now: datetime) -> list[DLQEntry]:
    """A handful of failed-fetch entries so DLQ views have content."""
    modes = [
        (FailureMode.paywall, "HTTP 402 on ATS gate"),
        (FailureMode.blocked, "Cloudflare challenge; skipping"),
        (FailureMode.timeout, "readability extract exceeded 20s"),
    ]
    entries: list[DLQEntry] = []
    for i, (mode, detail) in enumerate(modes):
        entries.append(
            DLQEntry(
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
            app = _make_application(rng, now, stage, company)
            apps.append(app)
            c, i = _make_contacts_and_interactions(rng, app, now)
            contacts.extend(c)
            interactions.extend(i)

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

    apps, contacts, interactions, dlq = build_seed(now=now, random_seed=random_seed)

    for app in apps:
        create_application(conn, app)
    for contact in contacts:
        create_contact(conn, contact)
    for interaction in interactions:
        create_interaction(conn, interaction)
    for entry in dlq:
        create_dlq_entry(conn, entry)

    # Mark one DLQ entry as resolved so both states are represented.
    if dlq:
        resolve_dlq_entry(conn, dlq[0].id, Resolution.manual_paste)

    return {
        "applications": len(apps),
        "contacts": len(contacts),
        "interactions": len(interactions),
        "dlq_entries": len(dlq),
    }
