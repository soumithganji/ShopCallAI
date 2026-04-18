"""Tool schemas passed to the LLM for function calling."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "request_location",
            "description": "Ask the user for their location when it hasn't been provided yet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why location is needed (shown to user)",
                    }
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_product_question",
            "description": (
                "Ask the user a clarifying question about the product "
                "to gather enough detail for searching and calling stores."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_stores",
            "description": (
                "Search You.com for local retailers that carry the product near the user's location."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string", "description": "Product name/type"},
                    "location": {"type": "string", "description": "City or area"},
                    "product_context": {
                        "type": "string",
                        "description": "Additional product details to refine the search",
                    },
                },
                "required": ["product", "location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_stores",
            "description": (
                "Trigger VoiceRun AI calls to the found stores to check product availability."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string"},
                    "product_details": {
                        "type": "string",
                        "description": "Brand, size, quantity, any preferences gathered from user",
                    },
                },
                "required": ["product", "product_details"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_results",
            "description": "Generate and return the final report to the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Formatted summary of call results",
                    }
                },
                "required": ["summary"],
            },
        },
    },
]
