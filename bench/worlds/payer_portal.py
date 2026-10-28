"""Synthetic payer portal ("ClearPath Health Payer Portal") for VeilBench.

A small, realistic back-office web app: member search, member eligibility,
claims, and claim notes. Every sensitive value is wrapped in an element with a
`data-pii` attribute so ground truth can be read from the DOM. Veil itself never
reads the DOM; it only sees pixels.

Run: uv run uvicorn bench.worlds.payer_portal:app --port 8100
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from fastapi import FastAPI, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from bench.worlds.people import make_people

ASSETS = Path(__file__).resolve().parent.parent / "assets"
PEOPLE = make_people()
BY_ID = {p.pid: p for p in PEOPLE}
STATE: dict = {"messages": [], "address_updates": [], "notes": {}}

app = FastAPI(title="ClearPath payer portal (synthetic)")
if ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")

CSS = """
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif;
       font-size: 15px; color: #1f2933; background: #f4f6f8; }
header { background: #0b4f6c; color: #fff; padding: 14px 28px; display: flex;
         align-items: center; gap: 28px; }
header .brand { font-weight: 700; font-size: 18px; }
header nav a { color: #d6eef7; margin-right: 18px; text-decoration: none; }
main { padding: 24px 28px; max-width: 1180px; }
h1 { font-size: 22px; margin: 0 0 16px; }
h2 { font-size: 17px; margin: 22px 0 10px; }
.card { background: #fff; border: 1px solid #dde3e8; border-radius: 8px; padding: 18px 20px;
        margin-bottom: 16px; }
.grid { display: grid; grid-template-columns: 170px 1fr 170px 1fr; gap: 10px 16px; }
.label { color: #5b6b78; }
table { width: 100%; border-collapse: collapse; background: #fff; }
th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid #e6ebef; }
th { background: #eef2f5; font-weight: 600; }
input, textarea, select { font: inherit; padding: 7px 9px; border: 1px solid #b8c4cc;
                          border-radius: 5px; }
button { font: inherit; padding: 8px 16px; border: 0; border-radius: 5px; background: #0b7a75;
         color: #fff; cursor: pointer; }
.pill { padding: 2px 9px; border-radius: 10px; font-size: 13px; background: #e3f4ea; }
.Denied { background: #fbe4e4; } .Pending { background: #fff3d6; } .In { background: #e5eefc; }
.note { background: #fffbea; border-left: 4px solid #e0b100; padding: 10px 14px; margin: 8px 0; }
a { color: #0b5f8a; }
tr.rowlink { cursor: pointer; } tr.rowlink:hover { background: #f0f7fb; }
td { height: 40px; }
"""


def pii(kind: str, value: str) -> str:
    return f'<span data-pii="{kind}">{html.escape(value)}</span>'


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<title>{html.escape(title)} - ClearPath Provider Portal</title><style>{CSS}</style></head>
<body><header><span class="brand">ClearPath Health</span>
<nav><a href="/">Member search</a><a href="/claims">Claims</a><a href="/messages">Messages</a></nav>
<span style="margin-left:auto;font-size:13px">Provider: Riverside Family Clinic &middot; NPI on file</span>
</header><main>{body}</main></body></html>""")


@app.get("/")
def search(q: str = Query("", description="name or member id")) -> HTMLResponse:
    rows = ""
    if q:
        ql = q.lower().strip()
        hits = [p for p in PEOPLE if ql in p.name.lower() or ql in p.member_id.lower()]
        for p in hits:
            rows += (f"<tr class='rowlink' onclick=\"location='/member/{p.pid}'\"><td><a href='/member/{p.pid}'>{pii('person_name', p.name)}</a></td>"
                     f"<td>{pii('member_id', p.member_id)}</td><td>{pii('date_of_birth', p.dob)}</td>"
                     f"<td>{html.escape(p.plan)}</td></tr>")
        rows = rows or "<tr><td colspan=4>No members found.</td></tr>"
    table = (f"<table><tr><th>Member</th><th>Member ID</th><th>Date of birth</th><th>Plan</th></tr>"
             f"{rows}</table>") if q else ""
    return page("Member search", f"""<h1>Member search</h1>
<div class="card"><form method="get" action="/">
<label class="label" for="q">Member name or ID</label><br>
<input id="q" name="q" value="{html.escape(q)}" style="width:360px" autofocus>
<button type="submit">Search</button></form></div>{table}""")


@app.get("/member/{pid}")
def member(pid: str) -> HTMLResponse:
    p = BY_ID[pid]
    claims = "".join(
        f"<tr><td><a href='/claim/{pid}/{i}'>{pii('claim_number', c.claim_no)}</a></td><td>{c.date}</td>"
        f"<td>{html.escape(c.procedure)}</td><td>{c.billed}</td>"
        f"<td><span class='pill {c.status.split()[0]}'>{c.status}</span></td></tr>"
        for i, c in enumerate(p.claims))
    return page(p.name, f"""<h1>Member eligibility</h1>
<div class="card"><div class="grid">
<span class="label">Name</span><span>{pii('person_name', p.name)}</span>
<span class="label">Member ID</span><span>{pii('member_id', p.member_id)}</span>
<span class="label">Date of birth</span><span>{pii('date_of_birth', p.dob)}</span>
<span class="label">Group number</span><span>{pii('group_number', p.group_no)}</span>
<span class="label">SSN</span><span>{pii('ssn', p.ssn)}</span>
<span class="label">Plan</span><span>{html.escape(p.plan)}</span>
<span class="label">Phone</span><span>{pii('phone', p.phone)}</span>
<span class="label">Coverage</span><span><span class="pill">Active</span> since 01/01/2026</span>
<span class="label">Email</span><span>{pii('email', p.email)}</span>
<span class="label">Address</span><span>{pii('address', f"{p.street}, {p.city}, {p.state} {p.zip}")}</span>
</div></div>
<h2>Recent claims</h2>
<table><tr><th>Claim #</th><th>Service date</th><th>Procedure</th><th>Billed</th><th>Status</th></tr>
{claims}</table>
<p><a href="/member/{pid}/address">Update mailing address</a></p>{id_card(pid)}""")


def id_card(pid: str) -> str:
    """Scanned ID card on file. Only p00 has one (the Muse Image asset matches p00's data)."""
    if pid != "p00" or not (ASSETS / "insurance_card_1.png").exists():
        return ""
    return ('<h2>Documents on file</h2><div class="card"><span class="label">Member ID card (front), '
            'uploaded 01/04/2026</span><br><img data-pii="id_card" src="/assets/insurance_card_1.png" '
            'style="width:420px;margin-top:8px;border-radius:6px"></div>')


@app.get("/claim/{pid}/{idx}")
def claim(pid: str, idx: int) -> HTMLResponse:
    p = BY_ID[pid]
    c = p.claims[idx]
    notes = "".join(f"<div class='note'>{n}</div>" for n in STATE["notes"].get(f"{pid}/{idx}", []))
    return page(c.claim_no, f"""<h1>Claim detail</h1>
<div class="card"><div class="grid">
<span class="label">Claim #</span><span>{pii('claim_number', c.claim_no)}</span>
<span class="label">Status</span><span><span class="pill {c.status.split()[0]}">{c.status}</span></span>
<span class="label">Member</span><span>{pii('person_name', p.name)}</span>
<span class="label">Member ID</span><span>{pii('member_id', p.member_id)}</span>
<span class="label">Service date</span><span>{c.date}</span>
<span class="label">Billed</span><span>{c.billed}</span>
<span class="label">Procedure</span><span>{html.escape(c.procedure)}</span>
</div></div><h2>Claim notes</h2>{notes or "<p class='label'>No notes.</p>"}
<p><a href="/member/{pid}">Back to member</a></p>""")


@app.get("/member/{pid}/address")
def address_form(pid: str) -> HTMLResponse:
    p = BY_ID[pid]
    return page("Update address", f"""<h1>Update mailing address</h1>
<div class="card">Member: {pii('person_name', p.name)} &middot; {pii('member_id', p.member_id)}
<form method="post" action="/member/{pid}/address" style="margin-top:14px">
<label class="label" for="street">Street</label><br><input id="street" name="street" style="width:420px"><br><br>
<label class="label" for="city">City</label><br><input id="city" name="city"><br><br>
<button type="submit" style="padding:14px 26px">Save address</button></form></div>""")
# Oct 28: bigger Save button. Spark clicked 6-8 px below the default 33 px button in 2 of
# 2 live runs (raw runs also missed it at times), so the gate test measured aim, not Veil.
# Raw address runs before this change (raw_address_v2_*) used the smaller button.


@app.post("/member/{pid}/address")
def address_save(pid: str, street: str = Form(...), city: str = Form(...)):
    STATE["address_updates"].append({"pid": pid, "street": street, "city": city})
    return RedirectResponse(f"/member/{pid}", status_code=303)


@app.get("/messages")
def messages() -> HTMLResponse:
    sent = "".join(f"<tr><td>{html.escape(m['to'])}</td><td>{html.escape(m['body'][:120])}</td></tr>"
                   for m in STATE["messages"])
    return page("Messages", f"""<h1>Secure messages</h1>
<div class="card"><form method="post" action="/messages">
<label class="label" for="to">To</label><br><input id="to" name="to" style="width:420px"><br><br>
<label class="label" for="body">Message</label><br><textarea id="body" name="body" rows="5" cols="60"></textarea><br><br>
<button type="submit">Send</button></form></div>
<h2>Sent</h2><table><tr><th>To</th><th>Message</th></tr>{sent}</table>""")


@app.post("/messages")
def send_message(to: str = Form(...), body: str = Form(...)):
    STATE["messages"].append({"to": to, "body": body})
    return RedirectResponse("/messages", status_code=303)


@app.get("/claims")
def claims_index(q: str = Query("")) -> HTMLResponse:
    rows = ""
    if q:
        ql = q.lower().strip()
        for p in PEOPLE:
            for i, c in enumerate(p.claims):
                if ql in c.claim_no.lower() or ql in p.name.lower():
                    rows += (f"<tr class='rowlink' onclick=\"location='/claim/{p.pid}/{i}'\">"
                             f"<td><a href='/claim/{p.pid}/{i}'>{pii('claim_number', c.claim_no)}</a></td>"
                             f"<td>{pii('person_name', p.name)}</td><td>{c.date}</td><td>{c.billed}</td>"
                             f"<td><span class='pill {c.status.split()[0]}'>{c.status}</span></td></tr>")
        rows = rows or "<tr><td colspan=5>No claims found.</td></tr>"
    table = ("<table><tr><th>Claim #</th><th>Member</th><th>Service date</th><th>Billed</th>"
             f"<th>Status</th></tr>{rows}</table>") if q else ""
    return page("Claims", f"""<h1>Claim lookup</h1>
<div class="card"><form method="get" action="/claims">
<label class="label" for="q">Claim number or member name</label><br>
<input id="q" name="q" value="{html.escape(q)}" style="width:360px">
<button type="submit">Search</button></form></div>{table}""")


# --- bench control endpoints (not visible to the agent) ---------------------

@app.get("/_bench/state")
def bench_state() -> JSONResponse:
    return JSONResponse(STATE)


@app.post("/_bench/reset")
def bench_reset(notes: str = Form("{}")) -> JSONResponse:
    STATE.update({"messages": [], "address_updates": [], "notes": json.loads(notes)})
    return JSONResponse({"ok": True})


@app.get("/_bench/people")
def bench_people() -> JSONResponse:
    return JSONResponse([p.to_dict() for p in PEOPLE])


if __name__ == "__main__":
    import uvicorn

    Path(__file__).parent
    uvicorn.run(app, host="127.0.0.1", port=8100)
