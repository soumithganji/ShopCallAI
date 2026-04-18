"""
VoiceRun agent — deployed via: vr deploy

Dynamic variables injected at call time:
  - product:        e.g. "duct tape"
  - product_details: e.g. "any brand, at least 2 rolls"
  - store_name:     e.g. "Ace Hardware Brooklyn"
"""
from primfunctions.events import Event, StartEvent, TextEvent, TextToSpeechEvent
from primfunctions.context import Context
from primfunctions.completions import configure_provider, generate_chat_completion


async def handler(event: Event, context: Context):
    product = context.variables.get("product", "the item")
    product_details = context.variables.get("product_details", "")
    store_name = context.variables.get("store_name", "the store")

    if isinstance(event, StartEvent):
        configure_provider("anthropic", voicerun_managed=True)

        detail_phrase = f" — {product_details}" if product_details else ""
        context.set_data("stage", "asked_availability")
        yield TextToSpeechEvent(
            text=(
                f"Hi, I'm calling to check if {store_name} has {product}{detail_phrase} in stock. "
                "Do you currently carry that?"
            ),
            voice="kore",
        )

    if isinstance(event, TextEvent):
        stage = context.get_data("stage") or "asked_availability"
        user_said = event.data.get("text", "")

        if stage == "asked_availability":
            # Use LLM to interpret yes/no naturally
            classification = await generate_chat_completion({
                "provider": "anthropic",
                "model": "claude-haiku-4-5",
                "messages": [{
                    "role": "user",
                    "content": (
                        f"The store employee said: \"{user_said}\"\n"
                        f"We asked if they have {product} in stock.\n"
                        "Reply with exactly one word: YES, NO, or UNCLEAR"
                    ),
                }],
            })

            verdict = (classification.message.content or "").strip().upper()

            if verdict == "YES":
                context.set_data("stage", "asked_price")
                yield TextToSpeechEvent(
                    text=f"Great! What's the price per unit, and roughly how many do you have?",
                    voice="kore",
                )
            elif verdict == "NO":
                context.set_data("stage", "done")
                yield TextToSpeechEvent(
                    text="Okay, thanks for letting me know! Have a great day.",
                    voice="kore",
                )
            else:
                yield TextToSpeechEvent(
                    text=f"Sorry, just to confirm — do you currently have {product} available for purchase today?",
                    voice="kore",
                )

        elif stage == "asked_price":
            context.set_data("price_info", user_said)
            context.set_data("stage", "done")
            yield TextToSpeechEvent(
                text="Perfect, thank you! A customer will be in shortly. Have a great day!",
                voice="kore",
            )
