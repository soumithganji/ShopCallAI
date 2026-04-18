import re
import asyncio
import json
import urllib.request
from primfunctions.events import Event, StartEvent, TextEvent, TextToSpeechEvent, StopEvent
from primfunctions.context import Context
from primfunctions.completions import configure_provider, generate_chat_completion


async def _post_outcome(url: str, store_phone: str, in_stock: str, price_info: str):
    if not url:
        return
    def _do_post():
        try:
            data = json.dumps({"store_phone": store_phone, "in_stock": in_stock, "price_info": price_info}).encode()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass
    await asyncio.get_event_loop().run_in_executor(None, _do_post)

_GREETING = re.compile(
    r"^\s*(hello+|hi+|hey+|yes\?*|yeah\?*|yep\?*|uh+|um+|speak(ing)?|"
    r"who('?s| is) (this|calling)|can i help|how can i|what('?s| is) up|good (morning|afternoon|evening))\s*[.!?]?\s*$",
    re.IGNORECASE,
)
_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|we do|we have|in stock|carry that|got it|absolutely)\b", re.IGNORECASE)
_NO = re.compile(r"\b(no|nope|don'?t|do not|out of stock|don'?t carry|don'?t have|sold out|not available)\b", re.IGNORECASE)
_VOICEMAIL = re.compile(
    r"(leave a message|after the (beep|tone)|not (available|in)|reached the voicemail|"
    r"no one is available|unable to take your call|please (leave|record)|"
    r"you (have reached|'ve reached)|at the (beep|tone)|record your message)",
    re.IGNORECASE,
)


async def handler(event: Event, context: Context):
    product = context.variables.get("product", "the item")
    product_details = context.variables.get("product_details", "")
    store_name = context.variables.get("store_name", "the store")
    outcome_url = context.variables.get("outcome_webhook_url", "")
    store_phone = context.variables.get("store_phone", "")

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

        # Hang up on voicemail
        if _VOICEMAIL.search(user_said) or len(user_said) > 300:
            yield StopEvent(closing_speech="")

        # Configure provider here, not in StartEvent
        configure_provider("anthropic", voicerun_managed=True)

        if stage == "asked_availability":
            detail_phrase = f", {product_details}" if product_details else ""
            reask = f"Hi! I'm calling to check if you have {product}{detail_phrase} in stock. Do you carry that?"

            # Fast-path: YES/NO checked before GREETING to avoid "yes?" ambiguity
            if _YES.search(user_said) and not _NO.search(user_said):
                context.set_data("stage", "asked_price")
                context.set_testing_metadata("in_stock", "yes")
                await _post_outcome(outcome_url, store_phone, "yes", "")
                yield TextToSpeechEvent(text="Great! What's the price, and how many do you have?", voice="kore")
            elif _NO.search(user_said) and not _YES.search(user_said):
                context.set_data("stage", "done")
                context.set_testing_metadata("in_stock", "no")
                await _post_outcome(outcome_url, store_phone, "no", "")
                yield StopEvent(closing_speech="Okay, thank you! Have a great day.")
            elif _GREETING.match(user_said):
                yield TextToSpeechEvent(text=reask, voice="kore")
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
                    context.set_testing_metadata("in_stock", "yes")
                    await _post_outcome(outcome_url, store_phone, "yes", "")
                    yield TextToSpeechEvent(text="Great! What's the price, and how many do you have?", voice="kore")
                elif "NO" in verdict:
                    context.set_data("stage", "done")
                    context.set_testing_metadata("in_stock", "no")
                    await _post_outcome(outcome_url, store_phone, "no", "")
                    yield StopEvent(closing_speech="Okay, thank you! Have a great day.")
                else:
                    yield TextToSpeechEvent(text=reask, voice="kore")

        elif stage == "asked_price":
            if not user_said.strip():
                yield TextToSpeechEvent(text="Sorry, could you repeat the price?", voice="kore")
            else:
                context.set_data("price_info", user_said)
                context.set_testing_metadata("price_info", user_said)
                context.set_data("stage", "done")
                await _post_outcome(outcome_url, store_phone, "yes", user_said)
                yield StopEvent(closing_speech="Perfect, thank you! Have a great day!")
