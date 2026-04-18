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
        "Format:\n"
        "- One line per store with status icon (✓ in stock, ✗ out of stock or unreachable)\n"
        "- Include price and address if available\n"
        "- End with a 1-line recommendation (best option or next steps)\n"
        "- Keep it under 150 words total\n"
        "- No markdown headers, just plain structured text"
    )

    msg = await chat([{"role": "user", "content": prompt}], temperature=0.2)
    return msg.content.strip()
