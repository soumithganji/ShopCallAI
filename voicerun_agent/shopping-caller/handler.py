import re
from primfunctions.events import Event, StartEvent, TextEvent, TextToSpeechEvent, StopEvent
from primfunctions.context import Context
from primfunctions.completions import configure_provider, generate_chat_completion

_GREETING = re.compile(
    r"^\s*(hello+|hi+|hey+|yes\?*|yeah\?*|yep\?*|uh+|um+|speak(ing)?|"
    r"who('?s| is) (this|calling)|can i help|how can i|what('?s| is) up|good (morning|afternoon|evening))\s*[.!?]?\s*$",
    re.IGNORECASE,
)
_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|we do|we have|in stock|carry that|got it|absolutely)\b", re.IGNORECASE)
_NO = re.compile(r"\b(no|nope|don'?t|do not|out of stock|don'?t carry|don'?t have|sold out|not available)\b", re.IGNORECASE)


async def handler(event: Event, context: Context):
    product = context.variables.get("product", "the item")
    product_details = context.variables.get("product_details", "")
    store_name = context.variables.get("store_name", "the store")

    if isinstance(event, StartEvent):
        # TTS first — configure_provider called lazily in TextEvent to avoid blocking
        detail_phrase = f", {product_details}" if product_details else ""
        context.set_data("stage", "asked_availability")
        yield TextToSpeechEvent(
            text=f"Hi! I'm calling to check if you have {product}{detail_phrase} in stock. Do you carry that?",
            voice="kore",
        )

    if isinstance(event, TextEvent):
        stage = context.get_data("stage") or "asked_availability"
        user_said = event.data.get("text", "")

        # Configure provider here, not in StartEvent
        configure_provider("anthropic", voicerun_managed=True)

        if stage == "asked_availability":
            detail_phrase = f", {product_details}" if product_details else ""
            reask = f"Hi! I'm calling to check if you have {product}{detail_phrase} in stock. Do you carry that?"

            # Fast-path: no LLM needed for obvious cases
            if _GREETING.match(user_said):
                yield TextToSpeechEvent(text=reask, voice="kore")
            elif _YES.search(user_said) and not _NO.search(user_said):
                context.set_data("stage", "asked_price")
                yield TextToSpeechEvent(text="Great! What's the price, and how many do you have?", voice="kore")
            elif _NO.search(user_said) and not _YES.search(user_said):
                context.set_data("stage", "done")
                yield StopEvent(closing_speech="Okay, thank you! Have a great day.")
            else:
                # Ambiguous — use LLM
                configure_provider("anthropic", voicerun_managed=True)
                resp = await generate_chat_completion({
                    "provider": "anthropic",
                    "model": "claude-haiku-4-5-20251001",
                    "messages": [{
                        "role": "user",
                        "content": (
                            f'Store employee said: "{user_said}" when asked if they have {product}. '
                            'Reply with one word: YES, NO, or GREETING'
                        ),
                    }],
                })
                verdict = (resp.message.content or "GREETING").strip().upper()
                if "YES" in verdict:
                    context.set_data("stage", "asked_price")
                    yield TextToSpeechEvent(text="Great! What's the price, and how many do you have?", voice="kore")
                elif "NO" in verdict:
                    context.set_data("stage", "done")
                    yield StopEvent(closing_speech="Okay, thank you! Have a great day.")
                else:
                    yield TextToSpeechEvent(text=reask, voice="kore")

        elif stage == "asked_price":
            context.set_data("price_info", user_said)
            context.set_data("stage", "done")
            yield StopEvent(closing_speech="Perfect, thank you! Have a great day!")
