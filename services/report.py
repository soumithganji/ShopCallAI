from llm import chat


async def generate_report(
    product: str,
    location: str,
    call_results: list[dict],
) -> str:
    completed = [r for r in call_results if r["status"] == "completed"]
    unreachable = [r for r in call_results if r["status"] == "unreachable"]

    results_summary = []
    for r in call_results:
        store = r["store"]
        entry = {
            "store": store.get("name"),
            "address": store.get("address"),
            "status": r["status"],
            "outcome": r.get("outcome"),
            "price_info": r.get("price_info"),
            "attempts": r.get("attempts"),
        }
        results_summary.append(entry)

    prompt = (
        f"Write a short, clear report for a user who asked to find '{product}' near {location}.\n\n"
        f"Call results: {results_summary}\n\n"
        "Rules for each store line:\n"
        "- outcome='yes' → ✓ In stock (include price_info if present)\n"
        "- outcome='no' → ✗ Out of stock\n"
        "- outcome='unknown' and status='completed' → ~ Reached, stock unknown\n"
        "- status='no_answer' → 📵 No answer\n"
        "- status='busy' → 📵 Line busy\n"
        "- status in (unreachable, failed, timeout) → ✗ Couldn't reach\n"
        "- status='skipped' → skip this store entirely\n"
        "Format: one line per store: [icon] [Store Name] | [address] | [status detail]\n"
        "End with a 1-line recommendation.\n"
        "No markdown headers. Under 150 words."
    )

    msg = await chat([{"role": "user", "content": prompt}], temperature=0.2)
    return msg.content.strip()
