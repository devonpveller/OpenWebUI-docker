"""The scripted evening for scripts/checks/drill-pantry-e2e.ps1 (item pantry-wire).

Runs INSIDE a throwaway ``openwebui:local`` container that is attached ONLY to the
drill's private, labelled, internal network. It imports the real tool
(frontend/owui/tools/pantry.py), points its valves at the drill's own openbrain-pantry
container and plays one household evening through the tool's functions - exactly the
calls the Kitchen model makes - asserting on what the SERVICE returned:

    health + auth, household (2 adults + a 4.5-year-old child with a peanut allergy),
    add stock, save household recipes, plan two dinners, shopping list, restock (bought
    all EXCEPT one item, a SUBSTITUTION, a pack-size ACTUAL), an allergen-refused cook,
    cook, evaluate with a CHILD REFUSAL, guidance shows the COOLDOWN, CSV export ->
    import = EMPTY DIFF.

Every step prints ``[PASS] name`` or ``[FAIL] name: why`` and the run ends with a RESULT
line; the exit code is the number of failed steps. The taste steps (evaluation, guidance)
need the routes from item pantry-taste: ``--taste skip`` leaves them out and says so.

Environment: PANTRY_URL (default http://openbrain-pantry:8000), PANTRY_KEY.
Standard library + what the OWUI image has (pydantic); no network except PANTRY_URL.
"""

import argparse
import asyncio
import csv
import datetime
import importlib.util
import io
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.environ.get("PANTRY_TOOL", "/o/tools/pantry.py")

results: list = []  # (ok, name, why)


def load_tool():
    spec = importlib.util.spec_from_file_location("pantry_tool", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pantry_tool"] = mod
    spec.loader.exec_module(mod)
    return mod


def say(ok: bool, name: str, why: str = ""):
    results.append((ok, name, why))
    print(("[PASS] " if ok else "[FAIL] ") + name + ("" if ok else f": {why}"), flush=True)


class Fail(Exception):
    pass


def need(cond, why):
    if not cond:
        raise Fail(why)


def j(text):
    """The tool returns compact JSON text (or, for export, prose)."""
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        raise Fail(f"tool did not return JSON: {str(text)[:200]!r}")


def errcode(res):
    return res.get("error") if isinstance(res, dict) else None


def by_name(items, name):
    for it in items:
        if str(it.get("name", "")).lower() == name.lower():
            return it
    raise Fail(f"{name!r} not found among {[i.get('name') for i in items]}")


def near(a, b, tol=1e-6):
    return a is not None and abs(float(a) - float(b)) <= tol


async def step(name, coro, state):
    try:
        await coro(state)
        say(True, name)
    except Fail as e:
        say(False, name, str(e))
    except Exception as e:  # a crash in a step is a failed step, never a crashed drill
        say(False, name, f"{type(e).__name__}: {e}")


async def main(taste: str) -> int:
    base = os.environ.get("PANTRY_URL", "http://openbrain-pantry:8000")
    key = os.environ.get("PANTRY_KEY", "")
    mod = load_tool()
    tools = mod.Tools()
    tools.valves.service_url = base
    tools.valves.api_key = key
    tools.valves.request_timeout_s = 30.0

    today = datetime.date.today()
    monday = today + datetime.timedelta(days=(7 - today.weekday()) % 7 or 7)
    tuesday = monday + datetime.timedelta(days=1)
    child_birth = (today.replace(day=1) - datetime.timedelta(days=int(4.5 * 365.25))).strftime("%Y-%m")
    st: dict = {"monday": monday.isoformat(), "tuesday": tuesday.isoformat()}

    # -------------------------------------------------------------- health, auth
    async def s_health(s):
        deadline = datetime.datetime.now() + datetime.timedelta(seconds=90)
        last = ""
        while datetime.datetime.now() < deadline:
            try:
                with urllib.request.urlopen(base + "/health", timeout=5) as r:
                    body = json.loads(r.read().decode())
                    if r.status == 200 and body.get("ok") and body.get("db") is True:
                        return
                    last = str(body)
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
            await asyncio.sleep(2)
        raise Fail(f"/health never answered ok+db within 90 s (last: {last})")

    async def s_auth(s):
        good_key = tools.valves.api_key
        tools.valves.api_key = "wrong-key"
        try:
            res = j(await tools.get_pantry())
        finally:
            tools.valves.api_key = good_key
        need(res.get("http_status") == 401 and errcode(res) == "unauthorized", f"wrong key was not refused: {res}")
        res = j(await tools.get_pantry())
        need(isinstance(res.get("items"), list), f"right key was refused: {res}")

    # -------------------------------------------------------------- household
    async def s_household(s):
        for label, role, extra in (
            ("Alex", "adult", {}),
            ("Sam", "adult", {}),
            ("Robin", "child", {"birth_month": child_birth, "allergies": ["peanut"]}),
        ):
            res = j(await tools.manage_household(action="save", label=label, role=role, **extra))
            person = res.get("person", res)
            need(person.get("id"), f"person {label} not saved: {res}")
            s["people_" + label] = person["id"]
        listing = j(await tools.manage_household(action="list"))
        people = listing.get("people", [])
        need(len(people) == 3, f"expected 3 people, got {len(people)}: {listing}")
        robin = by_name([{"name": p["label"], **p} for p in people], "Robin")
        need(robin.get("role") == "child" and "peanut" in [a.lower() for a in robin.get("allergies", [])], f"Robin wrong: {robin}")
        cfg = j(await tools.manage_household(action="get_settings"))
        portions = cfg.get("portions") or cfg.get("settings", {}).get("portions")
        need(portions and near(portions.get("adult"), 1.0) and near(portions.get("child"), 0.5), f"portions: {cfg}")

    # -------------------------------------------------------------- stock
    async def s_stock(s):
        items = [
            {"name": "Chicken thighs", "create": True, "kind": "counted", "quantity": 200, "unit": "g", "category": "meat", "location": "fridge"},
            {"name": "Rice", "create": True, "kind": "counted", "quantity": 2, "unit": "kg", "category": "pantry"},
            {"name": "Broccoli", "create": True, "kind": "counted", "quantity": 600, "unit": "g", "category": "produce"},
            {"name": "Pasta", "create": True, "kind": "counted", "quantity": 500, "unit": "g", "category": "pantry"},
            {"name": "Tomatoes", "create": True, "kind": "counted", "quantity": 1, "unit": "count", "category": "produce"},
            {"name": "Canned tomatoes", "create": True, "kind": "counted", "quantity": 0, "unit": "g", "category": "pantry"},
            {"name": "Peanut butter", "create": True, "kind": "counted", "quantity": 300, "unit": "g", "category": "pantry", "allergens": ["peanut"]},
            {"name": "Olive oil", "create": True, "kind": "staple", "level": "plenty", "category": "pantry"},
            {"name": "Salt", "create": True, "kind": "staple", "level": "plenty", "category": "pantry"},
            {"name": "Soy sauce", "create": True, "kind": "staple", "level": "low", "category": "pantry"},
        ]
        res = j(await tools.update_pantry(items=items))
        need(len(res.get("created", [])) == len(items), f"created {len(res.get('created', []))} of {len(items)}: {res}")
        need(not res.get("unmatched") and not res.get("unconvertible"), f"unmatched/unconvertible: {res}")
        pantry = j(await tools.get_pantry())["items"]
        rice = by_name(pantry, "Rice")
        need(rice["unit"] == "g" and near(rice["quantity"], 2000), f"2 kg rice must be stored as 2000 g: {rice}")
        s["ids"] = {i["name"]: i["id"] for i in pantry}
        # a name that is not in the pantry is NOT created without create:true (D3)
        miss = j(await tools.update_pantry(items=[{"name": "Dragonfruit", "quantity": 2, "unit": "count"}]))
        need(miss.get("unmatched") and not miss.get("created"), f"unknown name must come back unmatched: {miss}")

    # -------------------------------------------------------------- recipes
    async def s_recipes(s):
        ids = s["ids"]
        ing = lambda name, q=None, u=None, staple=False: {  # noqa: E731
            k: v for k, v in {"pantry_item_id": ids[name], "name": name, "quantity": q, "unit": u, "staple": staple or None}.items() if v is not None
        }
        r1 = j(await tools.save_household_recipe(
            name="Family chicken and rice", servings=5, rotation=True, cuisine="american",
            ingredients=[ing("Chicken thighs", 500, "g"), ing("Rice", 400, "g"), ing("Broccoli", 300, "g"),
                         ing("Olive oil", staple=True), ing("Salt", staple=True)],
            instructions=["Roast the chicken.", "Steam the rice and broccoli."]))
        need(r1.get("recipe", {}).get("id") and not r1.get("unmatched") and not r1.get("allergen_conflicts"), f"recipe 1: {r1}")
        s["r_chicken"] = r1["recipe"]["id"]
        r2 = j(await tools.save_household_recipe(
            name="Tomato pasta", servings=5, cuisine="italian",
            ingredients=[ing("Pasta", 400, "g"), ing("Tomatoes", 4, "count"), ing("Olive oil", staple=True)],
            instructions=["Boil the pasta.", "Toss with tomatoes and oil."]))
        need(r2.get("recipe", {}).get("id") and not r2.get("unmatched"), f"recipe 2: {r2}")
        s["r_pasta"] = r2["recipe"]["id"]
        r3 = j(await tools.save_household_recipe(
            name="Peanut noodles", servings=4,
            ingredients=[ing("Pasta", 200, "g"), ing("Peanut butter", 100, "g")], instructions=["Mix."]))
        need(r3.get("recipe", {}).get("id"), f"recipe 3: {r3}")
        # the service flags the conflict at save time too (informational) - the REFUSAL is /cook's
        s["r_peanut"] = r3["recipe"]["id"]
        got = j(await tools.save_household_recipe(
            name="Family chicken and rice", servings=5, recipe_id=s["r_chicken"], reason="same recipe, new revision",
            ingredients=[ing("Chicken thighs", 500, "g"), ing("Rice", 400, "g"), ing("Broccoli", 300, "g"),
                         ing("Olive oil", staple=True), ing("Salt", staple=True)],
            instructions=["Roast the chicken.", "Steam the rice and broccoli."]))
        need(got.get("revision", {}).get("revision") == 2, f"a second save must be revision 2: {got.get('revision')}")

    # -------------------------------------------------------------- plan
    async def s_plan(s):
        p1 = j(await tools.plan_meal(date=s["monday"], recipe_id=s["r_chicken"]))
        p2 = j(await tools.plan_meal(date=s["tuesday"], recipe_id=s["r_pasta"]))
        need(p1.get("plan", {}).get("id") and p2.get("plan", {}).get("id"), f"plans: {p1} {p2}")
        s["plan_mon"], s["plan_tue"] = p1["plan"]["id"], p2["plan"]["id"]
        need(any(near(r.get("reserved"), 250) for r in p1.get("reservations", []) if r["name"] == "Chicken thighs"),
             f"Monday must reserve 250 g chicken (2.5 portions / 5 servings x 500 g): {p1.get('reservations')}")
        need(any(sf["name"] == "Chicken thighs" for sf in p1.get("shortfalls", [])), f"Monday is short on chicken: {p1.get('shortfalls')}")
        week = j(await tools.get_plan(week_start=s["monday"]))
        need(len(week.get("plans", [])) == 2, f"week must hold two dinners: {week}")
        pantry = j(await tools.get_pantry())["items"]
        rice = by_name(pantry, "Rice")
        need(near(rice["reserved"], 200) and near(rice["available"], 1800), f"rice reserved/available: {rice}")

    # -------------------------------------------------------------- shopping list
    async def s_shopping(s):
        res = j(await tools.shopping_list(week_start=s["monday"]))
        need(res.get("list_id"), f"no list_id: {res}")
        s["list_id"] = res["list_id"]
        lines = {it["name"].lower(): it for it in res.get("items", [])}
        need("chicken thighs" in lines and near(lines["chicken thighs"]["quantity"], 50) and lines["chicken thighs"]["reason"] == "shortfall",
             f"chicken: need 250 have 200 -> buy 50 g: {sorted(lines)}")
        need("tomatoes" in lines and near(lines["tomatoes"]["quantity"], 1), f"tomatoes: need 2 have 1 -> buy 1: {sorted(lines)}")
        need("soy sauce" in lines and lines["soy sauce"]["reason"] == "staple_low", f"low staple must be listed: {sorted(lines)}")
        need("rice" not in lines and "pasta" not in lines and "broccoli" not in lines,
             f"the list is NET of what is on hand - rice/pasta/broccoli must not be on it: {sorted(lines)}")

    # -------------------------------------------------------------- restock
    async def s_restock(s):
        res = j(await tools.restock(
            list_id=s["list_id"], bought="all", except_items=["Soy sauce"],
            substitutions=[{"for": "Tomatoes", "name": "Canned tomatoes", "quantity": 400, "unit": "g"}],
            actual=[{"name": "Chicken thighs", "quantity": 700, "unit": "g"}]))
        restocked = {r["name"].lower(): r for r in res.get("restocked", [])}
        need("chicken thighs" in restocked and near(restocked["chicken thighs"]["delta"], 700), f"pack-size actual 700 g must win over 50 g: {restocked}")
        need("canned tomatoes" in restocked and near(restocked["canned tomatoes"]["delta"], 400)
             and restocked["canned tomatoes"].get("substitution_for"), f"substitution row: {restocked}")
        need("tomatoes" not in restocked, f"the substituted item must not be restocked itself: {sorted(restocked)}")
        need("soy sauce" not in restocked, f"the excepted item must not be restocked: {sorted(restocked)}")
        carried = [c.get("name", "").lower() for c in res.get("carried_over", [])]
        need("soy sauce" in carried, f"the unbought item must carry over: {res.get('carried_over')}")
        affected = res.get("affected_plans", [])
        need([a["plan_id"] for a in affected] == [s["plan_mon"]],
             f"ONLY Monday's shortfalls changed (Tuesday's tomato gap is untouched): {affected}")
        need(not affected[0]["shortfalls_after"] and affected[0]["shortfalls_before"], f"Monday before/after: {affected[0]}")
        pantry = j(await tools.get_pantry())["items"]
        need(near(by_name(pantry, "Chicken thighs")["quantity"], 900) and near(by_name(pantry, "Canned tomatoes")["quantity"], 400),
             "pantry quantities after restock")

    # -------------------------------------------------------------- allergen-refused cook
    async def s_allergen(s):
        before = j(await tools.get_pantry())["items"]
        res = j(await tools.cook(recipe_id=s["r_peanut"]))
        need(res.get("http_status") == 409 and errcode(res) == "allergen_conflict", f"cook must be refused: {res}")
        who = json.dumps(res.get("conflicts", []))
        need("peanut" in who.lower() and "robin" in who.lower(), f"the refusal must name the allergen and whose: {who}")
        after = j(await tools.get_pantry())["items"]
        need({i["name"]: i["quantity"] for i in before} == {i["name"]: i["quantity"] for i in after}, "a refused cook wrote nothing")

    # -------------------------------------------------------------- cook
    async def s_cook(s):
        res = j(await tools.cook(recipe_id=s["r_chicken"], meal_plan_id=s["plan_mon"]))
        need(res.get("cook_event_id"), f"cook: {res}")
        s["cook"] = res["cook_event_id"]
        d = {x["name"].lower(): x for x in res.get("deductions", [])}
        need(near(d["chicken thighs"]["after"], 650) and near(d["rice"]["after"], 1800) and near(d["broccoli"]["after"], 450),
             f"deductions (scaled 0.5): {d}")
        need("olive oil" not in d and "salt" not in d, f"staples are never quantity-deducted: {sorted(d)}")
        need(not res.get("shortfalls"), f"no shortfall after the restock: {res.get('shortfalls')}")
        plans = {p["id"]: p for p in j(await tools.get_plan(week_start=s["monday"]))["plans"]}
        need(plans[s["plan_mon"]]["status"] == "cooked" and plans[s["plan_tue"]]["status"] == "planned", f"plan statuses: { {k: v['status'] for k, v in plans.items()} }")
        pantry = j(await tools.get_pantry())["items"]
        pasta = by_name(pantry, "Pasta")
        need(near(pasta["reserved"], 200), f"Tuesday still reserves pasta after Monday cooked: {pasta}")

    # -------------------------------------------------------------- taste
    async def s_evaluate(s):
        res = j(await tools.record_evaluation(
            cook_event_id=s["cook"], who="child", rating=2, liked=False, why="picked the broccoli out",
            exposures=[{"person_id": s["people_Robin"], "subject": "broccoli", "reaction": "refused"}]))
        if res.get("http_status") == 404 and errcode(res) == "not_found":
            raise Fail("POST /evaluations does not exist on this service (pantry-taste routes not present)")
        need(res.get("evaluation", {}).get("id") and len(res.get("exposures", [])) == 1, f"evaluation: {res}")
        need(res["exposures"][0]["reaction"] == "refused", f"exposure: {res['exposures']}")

    async def s_guidance(s):
        res = j(await tools.get_guidance(theme="weeknight"))
        if res.get("http_status") == 404 and errcode(res) == "not_found":
            raise Fail("POST /guidance does not exist on this service (pantry-taste routes not present)")
        cds = res.get("child_cooldowns", [])
        need([c["subject"].lower() for c in cds] == ["broccoli"], f"guidance must show the broccoli cooldown: {cds}")
        until = datetime.datetime.fromisoformat(cds[0]["until"].replace("Z", "+00:00"))
        days = (until - datetime.datetime.now(datetime.timezone.utc)).total_seconds() / 86400
        need(6.5 < days <= 7.01, f"cooldown must run ~7 days (child_cooldown_days default), got {days:.2f}")
        need(any("peanut" == a["allergen"].lower() and "robin" in a["who"].lower() for a in res.get("allergens_excluded", [])),
             f"Robin's peanut allergy must be excluded: {res.get('allergens_excluded')}")
        trend = [t for t in res.get("child_trends", []) if t["subject"].lower() == "broccoli"]
        need(trend and trend[0]["exposures"].get("refused") == 1, f"child trend: {res.get('child_trends')}")

    # -------------------------------------------------------------- csv round trip
    async def s_csv(s):
        before = j(await tools.get_pantry())["items"]
        text = await tools.export_pantry(format="csv")
        need("```csv" in text, f"expected the inline CSV fallback (no OWUI file store here): {text[:200]!r}")
        body = text.split("```csv\n", 1)[1].rsplit("```", 1)[0]
        rows = list(csv.reader(io.StringIO(body.lstrip("﻿"))))
        need(rows[0][:3] == ["id", "name", "category"] and len(rows) - 1 == len(before),
             f"export must have one row per item ({len(before)}), got {len(rows) - 1}")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "pantry-export.csv")
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                fh.write(body.lstrip("﻿"))
            res = j(await tools.import_pantry(__files__=[{"name": "pantry-export.csv", "path": path}]))
        need(res.get("ok") and res.get("preview_id"), f"import preview: {res}")
        for k in ("changed", "new", "missing", "unmatched", "unconvertible"):
            need(res.get(k) == [], f"an unedited export must re-import to an EMPTY diff, but {k} = {res.get(k)}")
        after = j(await tools.get_pantry())["items"]
        need({i["id"]: (i["quantity"], i["level"]) for i in before} == {i["id"]: (i["quantity"], i["level"]) for i in after},
             "the preview wrote to the pantry")

    plan = [
        ("health: GET /health answers ok + db", s_health, False),
        ("auth: wrong key -> 401, right key -> 200", s_auth, False),
        ("household: 2 adults + child 4.5 y with a peanut allergy, portions 1.0/0.5", s_household, False),
        ("stock: add 10 items (kg->g, unknown name not created)", s_stock, False),
        ("recipes: save household recipes + a second revision", s_recipes, False),
        ("plan: two dinners reserve stock (2.5 portions)", s_plan, False),
        ("shopping list: net of stock, staple_low listed", s_shopping, False),
        ("restock: all EXCEPT one, a substitution, a pack-size actual", s_restock, False),
        ("cook refused: peanut recipe vs the child's allergy, nothing written", s_allergen, False),
        ("cook: scaled deductions, staples untouched, plan cooked", s_cook, False),
        ("evaluate: child REFUSED broccoli", s_evaluate, True),
        ("guidance: shows the broccoli COOLDOWN + the peanut exclusion", s_guidance, True),
        ("csv: export -> import = empty diff, nothing written", s_csv, False),
    ]
    for name, fn, is_taste in plan:
        if is_taste and taste == "skip":
            print(f"[SKIP] {name} (--taste skip)", flush=True)
            continue
        await step(name, fn, st)

    failed = [r for r in results if not r[0]]
    print(f"RESULT: {len(results) - len(failed)} passed, {len(failed)} failed", flush=True)
    return len(failed)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--taste", choices=("run", "skip"), default="run")
    args = ap.parse_args()
    sys.exit(min(asyncio.run(main(args.taste)), 125))
