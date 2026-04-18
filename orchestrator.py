"""
State-driven orchestrator. Flow controlled by session state.
LLM used only for: extracting product/location intent, clarification, report.
"""
import asyncio
import re
import config
from llm import chat_json
from agents.location_agent import parse_location_from_text
from agents.search_agent import find_stores
from agents.call_agent import call_all_stores
from agents.report_agent import generate_report

# Products that never need clarification
_SIMPLE_PRODUCTS = re.compile(
    r"\b(duct tape|tape|batteries|battery|notebook|pen|pencil|staples|"
    r"paper|scissors|glue|bandage|aspirin|ibuprofen|advil|tylenol|"
    r"lighter|matches|zip ties|extension cord|light bulb)\b",
    re.IGNORECASE,
)


def new_session() -> dict:
    return {
        "messages": [],
        "location": None,
        "product": None,
        "product_details": "",
        "asked_clarification": False,
        "stores": None,
        "call_results": None,
    }


async def process_message(user_text: str, session: dict, progress: dict | None = None) -> str:
    session["messages"].append({"role": "user", "content": user_text})

    # Parallelize location + product extraction on first message
    tasks = []
    if not session["location"]:
        tasks.append(("location", parse_location_from_text(user_text)))
    if not session["product"]:
        tasks.append(("product", _extract_product(user_text)))

    if tasks:
        results = await asyncio.gather(*[t[1] for t in tasks])
        for (key, _), value in zip(tasks, results):
            if key == "location" and value:
                session["location"] = value
            elif key == "product" and value:
                session["product"] = value

    # Step 1: need location
    if not session["location"]:
        q = "What's your location? (city + state, e.g. 'Brooklyn NY')"
        session["messages"].append({"role": "assistant", "content": q})
        return q

    # Step 2: need product
    if not session["product"]:
        q = "What are you looking for?"
        session["messages"].append({"role": "assistant", "content": q})
        return q

    # Step 3: clarification — skip for simple products, use regex not LLM
    if not session["asked_clarification"] and not session["product_details"]:
        session["asked_clarification"] = True
        if not _SIMPLE_PRODUCTS.search(session["product"]):
            clarification = await _ask_clarification(session["product"], user_text)
            if clarification:
                session["messages"].append({"role": "assistant", "content": clarification})
                return clarification

    # Capture details if user just answered clarification
    if session["asked_clarification"] and not session["stores"] and not session["product_details"]:
        session["product_details"] = user_text

    # Step 4: find stores
    if session["stores"] is None:
        loc = session["location"]
        city = loc.get("city") or loc["display"].split(",")[0]

        session["stores"] = await find_stores(
            session["product"],
            loc["display"],
            session["product_details"],
            lat=loc.get("lat"),
            lng=loc.get("lng"),
        )

        if not session["stores"]:
            msg = f"No stores with phone numbers found for '{session['product']}' near {city}. Try a broader product name or different area."
            session["messages"].append({"role": "assistant", "content": msg})
            return msg

        if progress is not None:
            progress["stores"] = [
                {"name": s.get("name", ""), "address": s.get("address", ""), "phone": s.get("phone", ""), "status": "pending"}
                for s in session["stores"]
            ]

    # Step 5: call stores
    if session["call_results"] is None:
        if progress is not None:
            progress["phase"] = "calling"

        async def _on_status(store, status):
            if progress is None:
                return
            for entry in progress.get("stores", []):
                if entry["name"] == store.get("name"):
                    entry["status"] = status
                    break

        session["call_results"] = await call_all_stores(
            session["stores"],
            session["product"],
            session["product_details"],
            max_parallel=len(session["stores"]),
            on_status=_on_status,
        )

        if progress is not None:
            progress["phase"] = "done"
            # Update cards with final outcomes
            for result in session["call_results"]:
                store_name = result["store"].get("name", "")
                outcome = result.get("outcome", "unknown")
                call_status = result.get("status", "unknown")
                for entry in progress.get("stores", []):
                    if entry["name"] == store_name:
                        if call_status == "completed":
                            entry["status"] = "in_stock" if outcome == "yes" else ("out_of_stock" if outcome == "no" else "completed")
                        else:
                            entry["status"] = call_status
                        if result.get("price_info"):
                            entry["price_info"] = result["price_info"]
                        break

    # Step 6: report
    report = await generate_report(
        session["product"],
        session["location"]["display"],
        session["call_results"],
    )
    session["messages"].append({"role": "assistant", "content": report})
    return report


async def _extract_product(user_text: str) -> str | None:
    result = await chat_json([{
        "role": "user",
        "content": (
            f'Extract the product the user wants to buy: "{user_text}"\n'
            'Return JSON: {"product": "product name or null"}'
        ),
    }])
    p = result.get("product")
    return p if p and p != "null" else None


async def _ask_clarification(product: str, user_text: str) -> str | None:
    result = await chat_json([{
        "role": "user",
        "content": (
            f"User wants to buy: '{product}'. Message: \"{user_text}\"\n"
            "One clarifying question needed? (size/brand/type — only if critical for store search)\n"
            'Return JSON: {"question": "short question or null"}'
        ),
    }])
    q = result.get("question")
    return q if q and q != "null" else None
