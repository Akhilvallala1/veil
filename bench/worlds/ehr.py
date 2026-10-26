"""Held-out world: "Northwind Clinic" EHR-style patient chart (synthetic).

Built to look different from the payer portal so detection is tested on a layout
it was not tuned on: sidebar navigation, labels stacked ABOVE values, uppercase
small-caps labels, a different date format (YYYY-MM-DD), patient banners, a
schedule table with different headers, and a free-text clinical-admin note.
Every sensitive value carries data-pii. The detector code must not be tuned on
this world; it is evaluation only.

Run: uv run uvicorn bench.worlds.ehr:app --port 8200
"""

from __future__ import annotations

import html
import random

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from bench.worlds.people import make_people

PEOPLE = make_people(n=10, seed=4242)  # different people than the portal
rng = random.Random(99)
for p in PEOPLE:
    m, d, y = p.dob.split("/")
    p.dob_iso = f"{y}-{m}-{d}"  # type: ignore[attr-defined]
    p.mrn = f"{rng.randint(10**6, 10**7 - 1)}"  # type: ignore[attr-defined]
    p.pcp = rng.choice(["Dr. Alvarez", "Dr. Chen", "Dr. Okoye", "Dr. Ramsey"])  # type: ignore[attr-defined]
BY_ID = {p.pid: p for p in PEOPLE}

app = FastAPI(title="Northwind Clinic EHR (synthetic)")

CSS = """
* { box-sizing: border-box; }
body { margin: 0; font-family: Georgia, 'Times New Roman', serif; font-size: 14px; color: #222;
       background: #fbfaf7; display: flex; }
aside { width: 210px; min-height: 100vh; background: #2f3b2f; color: #e6efe6; padding: 18px 14px; }
aside .brand { font-size: 17px; font-weight: bold; margin-bottom: 22px; }
aside a { display: block; color: #cfe3cf; text-decoration: none; padding: 7px 4px; }
main { flex: 1; padding: 20px 26px; }
.banner { background: #e9efe4; border: 1px solid #c7d4bd; border-radius: 4px; padding: 12px 16px;
          display: flex; gap: 36px; margin-bottom: 18px; }
.field { display: flex; flex-direction: column; gap: 3px; }
.field .k { font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: #6a7a6a;
            font-family: Verdana, sans-serif; }
.field .v { font-size: 15px; }
.panel { background: #fff; border: 1px solid #ddd8cc; border-radius: 4px; padding: 14px 18px;
         margin-bottom: 16px; }
.panel h3 { margin: 0 0 12px; font-size: 15px; color: #3d4d3d; }
.cols { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px 24px; }
table { border-collapse: collapse; width: 100%; font-family: Verdana, sans-serif; font-size: 13px; }
th { text-align: left; font-size: 11px; text-transform: uppercase; color: #6a7a6a;
     border-bottom: 2px solid #c7d4bd; padding: 7px 8px; }
td { padding: 9px 8px; border-bottom: 1px solid #eee; }
.note { font-size: 14px; line-height: 1.5; }
a { color: #2b6c4f; }
"""


def pii(kind: str, value: str) -> str:
    return f'<span data-pii="{kind}">{html.escape(value)}</span>'


def page(body: str) -> HTMLResponse:
    nav = "".join(f"<a href='{u}'>{t}</a>" for u, t in
                  [("/", "Today's schedule"), ("/patients", "Patients"), ("#", "Inbox"),
                   ("#", "Billing"), ("#", "Reports")])
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style>
</head><body><aside><div class="brand">Northwind Clinic</div>{nav}</aside><main>{body}</main>
</body></html>""")


def f(k: str, v: str) -> str:
    return f"<div class='field'><span class='k'>{k}</span><span class='v'>{v}</span></div>"


@app.get("/")
def schedule() -> HTMLResponse:
    rows = "".join(
        f"<tr><td>{9 + i // 2}:{'00' if i % 2 == 0 else '30'}</td>"
        f"<td><a href='/chart/{p.pid}'>{pii('person_name', p.last + ', ' + p.first)}</a></td>"
        f"<td>{pii('mrn', p.mrn)}</td><td>{pii('date_of_birth', p.dob_iso)}</td>"
        f"<td>{p.pcp}</td><td>Follow-up</td></tr>" for i, p in enumerate(PEOPLE))
    return page(f"""<h2>Today's schedule</h2><div class="panel"><table>
<tr><th>Time</th><th>Patient</th><th>MRN</th><th>DOB</th><th>Provider</th><th>Visit type</th></tr>
{rows}</table></div>""")


@app.get("/patients")
def patients() -> HTMLResponse:
    rows = "".join(
        f"<tr><td><a href='/chart/{p.pid}'>{pii('person_name', p.name)}</a></td>"
        f"<td>{pii('phone', p.phone)}</td><td>{pii('email', p.email)}</td><td>{p.plan}</td></tr>"
        for p in PEOPLE)
    return page(f"""<h2>Patients</h2><div class="panel"><table>
<tr><th>Name</th><th>Phone number</th><th>E-mail</th><th>Insurance plan</th></tr>{rows}</table>
</div>""")


@app.get("/chart/{pid}")
def chart(pid: str) -> HTMLResponse:
    p = BY_ID[pid]
    banner = "".join([
        f("Patient", pii("person_name", p.name)), f("MRN", pii("mrn", p.mrn)),
        f("DOB", pii("date_of_birth", p.dob_iso)), f("PCP", p.pcp)])
    demo = "".join([
        f("Home address", pii("address", f"{p.street}, {p.city}, {p.state} {p.zip}")),
        f("Mobile", pii("phone", p.phone)), f("Email address", pii("email", p.email)),
        f("Social security no.", pii("ssn", p.ssn)), f("Insurance plan", p.plan),
        f("Subscriber ID", pii("member_id", p.member_id))])
    note = (f"Spoke with {pii('person_name', p.first + ' ' + p.last)} by phone at "
            f"{pii('phone', p.phone)} about the {p.plan} referral. Patient confirmed mailing "
            f"address on file. Prior authorization requested for MRI, knee; payer reference "
            f"{pii('claim_number', p.claims[0].claim_no)}. Will follow up Friday.")
    return page(f"""<div class="banner">{banner}</div>
<div class="panel"><h3>Demographics</h3><div class="cols">{demo}</div></div>
<div class="panel"><h3>Front desk note</h3><div class="note">{note}</div></div>""")
