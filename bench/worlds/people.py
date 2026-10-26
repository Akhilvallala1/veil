"""Seeded synthetic people for VeilBench. Everything here is fake.

Values are generated deterministically from a seed so frames and tasks are
reproducible. Formats follow real-world shapes (Luhn-valid cards, SSN area
rules) so detectors are tested on realistic strings, but no value belongs to a
real person.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field

FIRST = [
    "Maria", "James", "Aisha", "Wei", "Priya", "Daniel", "Sofia", "Kwame",
    "Elena", "Omar", "Grace", "Hiroshi", "Lucia", "Noah", "Fatima", "Mateo",
]
LAST = [
    "Delgado", "Okafor", "Nakamura", "Brennan", "Haddad", "Kowalski", "Reyes",
    "Lindqvist", "Mensah", "Petrov", "Chaudhry", "Moreau", "Sullivan", "Tanaka",
]
STREETS = ["Maple Ave", "Harbor Rd", "Cedar Ln", "Sunset Blvd", "Oak St", "Riverside Dr"]
CITIES = [("Tampa", "FL", "336"), ("Austin", "TX", "787"), ("Columbus", "OH", "432"),
          ("Raleigh", "NC", "276"), ("Denver", "CO", "802")]
PLANS = ["Silver PPO 2000", "Gold HMO 500", "Bronze EPO 6500", "Platinum PPO 250"]
CLAIM_STATUS = ["Paid", "Pending", "Denied", "In review"]
PROCEDURES = ["Office visit, est. patient", "Lab panel", "X-ray, chest", "Physical therapy",
              "MRI, knee", "Annual wellness visit"]


def _luhn_complete(prefix: str, length: int, rng: random.Random) -> str:
    digits = [int(c) for c in prefix]
    while len(digits) < length - 1:
        digits.append(rng.randint(0, 9))
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    check = (10 - total % 10) % 10
    return "".join(map(str, digits + [check]))


@dataclass
class Claim:
    claim_no: str
    date: str
    procedure: str
    billed: str
    status: str


@dataclass
class Person:
    pid: str
    first: str
    last: str
    dob: str
    ssn: str
    member_id: str
    group_no: str
    plan: str
    phone: str
    email: str
    street: str
    city: str
    state: str
    zip: str
    card: str
    claims: list[Claim] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}"

    def sensitive(self) -> dict[str, str]:
        """Ground truth: every value Veil must never let reach the cloud model."""
        return {
            "person_name": self.name,
            "date_of_birth": self.dob,
            "ssn": self.ssn,
            "member_id": self.member_id,
            "group_number": self.group_no,
            "phone": self.phone,
            "email": self.email,
            "address": f"{self.street}, {self.city}, {self.state} {self.zip}",
            "card_number": self.card,
            **{f"claim_number_{i}": c.claim_no for i, c in enumerate(self.claims)},
        }

    def to_dict(self) -> dict:
        d = asdict(self)
        d["name"] = self.name
        return d


def make_people(n: int = 12, seed: int = 7) -> list[Person]:
    rng = random.Random(seed)
    people = []
    for i in range(n):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        city, state, zip3 = rng.choice(CITIES)
        area = rng.choice([x for x in range(100, 899) if x != 666])
        p = Person(
            pid=f"p{i:02d}",
            first=first,
            last=last,
            dob=f"{rng.randint(1,12):02d}/{rng.randint(1,28):02d}/{rng.randint(1948, 2004)}",
            ssn=f"{area:03d}-{rng.randint(10,99):02d}-{rng.randint(1000,9999):04d}",
            member_id=f"{rng.choice('ABHKMW')}{rng.choice('XQZ')}{rng.randint(10**8, 10**9-1)}",
            group_no=f"GRP-{rng.randint(10000, 99999)}",
            plan=rng.choice(PLANS),
            phone=f"({rng.randint(201, 989)}) {rng.randint(200, 999)}-{rng.randint(1000, 9999)}",
            email=f"{first.lower()}.{last.lower()}{rng.randint(1, 99)}@example.com",
            street=f"{rng.randint(100, 9999)} {rng.choice(STREETS)}",
            city=city,
            state=state,
            zip=f"{zip3}{rng.randint(10, 99)}",
            card=_luhn_complete("4", 16, rng),
        )
        for _ in range(rng.randint(2, 4)):
            p.claims.append(Claim(
                claim_no=f"CLM{rng.randint(10**7, 10**8-1)}",
                date=f"{rng.randint(1,9):02d}/{rng.randint(1,28):02d}/2026",
                procedure=rng.choice(PROCEDURES),
                billed=f"${rng.randint(80, 4200)}.00",
                status=rng.choice(CLAIM_STATUS),
            ))
        people.append(p)
    return people
