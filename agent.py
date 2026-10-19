from anthropic import Anthropic, AsyncAnthropic
client = Anthropic()           # synchronous; reads ANTHROPIC_API_KEY from the environment
client = Anthropic(max_retries=3, timeout=60.0)   # built-in retries with backoff, request timeout

resp = client.messages.create(model=..., max_tokens=..., system=..., messages=[...], tools=[...])
client.messages.stream(...)          # stream tokens as they're generated
client.messages.count_tokens(...)    # count a prompt's tokens without running it


for block in message.content:
    if block.type == "text":
        print(block.text)


            