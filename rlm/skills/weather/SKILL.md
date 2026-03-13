+++
name = "weather"
description = "Get current weather and forecasts via wttr.in. Use when: user asks about weather, temperature, or forecasts for any location. No API key needed. NOT for: historical weather, severe weather alerts, or aviation/marine weather."
tags = ["tempo", "clima", "temperatura", "previsão", "chuva", "meteorologia", "weather", "sol", "vento", "umidade"]
priority = "contextual"

[sif]
signature = "weather(location: str = '') -> str"
prompt_hint = "Use para clima atual e previsão simples de uma cidade antes de viagem, rota ou evento."
short_sig = "weather(loc)\u2192str"
compose = ["calendar", "maps", "email"]
examples_min = ["consultar clima atual e previsão curta de uma cidade"]
codex = "lambda loc='Sao Paulo': __import__('urllib.request',fromlist=['x']).urlopen('https://wttr.in/'+__import__('urllib.parse',fromlist=['x']).quote(loc)+'?format=3',timeout=8).read().decode()"
impl = """
def weather(location=''):
    import urllib.request, urllib.parse
    loc = urllib.parse.quote_plus(location.strip()) if location.strip() else 'auto'
    url = f"https://wttr.in/{loc}?format=3"
    try:
        req = urllib.request.urlopen(url, timeout=8)
        return req.read().decode('utf-8', errors='replace').strip()
    except Exception as e:
        return f"N\u00e3o foi poss\u00edvel obter clima para '{location}': {e}"
"""

[requires]
# bins = ["curl"] removido: impl usa urllib stdlib

[runtime]
estimated_cost = 0.15
risk_level = "low"
side_effects = ["http_request"]
postconditions = ["weather_observation_returned"]
fallback_policy = "reply_with_unavailable_notice"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "weather forecast temperature rain wind climate city"
example_queries = ["qual o clima em São Paulo", "vai chover amanhã em Lisboa"]
+++

# Weather Skill

Current weather and forecasts using wttr.in (no API key required).

## When to Use

✅ **USE when:**
- "What's the weather in São Paulo?"
- "Will it rain tomorrow in London?"
- "Temperature this week in Tokyo"

❌ **DON'T use when:**
- Historical climate data → weather archives
- Hyper-local sensors → specialized APIs
- Aviation METAR → use aviationweather.gov

## REPL Usage

No MCP server needed — use subprocess directly:

```python
import subprocess

# One-line summary
result = subprocess.run(
    ["curl", "-s", "wttr.in/London?format=3"],
    capture_output=True, text=True
)
print(result.stdout)

# Full 3-day forecast
result = subprocess.run(
    ["curl", "-s", "wttr.in/SaoPaulo"],
    capture_output=True, text=True
)
print(result.stdout)

# JSON format for structured data
import json, requests
data = requests.get("https://wttr.in/Tokyo?format=j1").json()
temp_c = data["current_condition"][0]["temp_C"]
print(f"Tokyo: {temp_c}°C")
```

## Location Formats

- City name: `London`, `New+York`, `SaoPaulo`
- Airport code: `LHR`, `JFK`, `GRU`
- Coordinates: `-23.55,-46.63`
