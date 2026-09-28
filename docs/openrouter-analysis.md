# Transcript analysis through OpenRouter

Chosen configuration:

```dotenv
LLM=openrouter:anthropic/claude-sonnet-5.5
OPENROUTER_API_KEY=<private funded account key>
OPENROUTER_FALLBACK_MODEL=openai/gpt-6-luna
```

The key can also be saved in the encrypted admin settings. Both backend and worker
must receive the configuration. Existing deployments retain their explicit LLM
setting until an approved switch.

OpenRouter receives Sonnet as the primary `model` and Luna in the `models`
fallback list. It handles provider and model failover. SupoClip requests strict
native JSON schema output, requires compatible endpoints, excludes providers
that collect data, uses low reasoning effort, and caps each response at 8,192
tokens. Existing schema repair retries and the overall 600-second analysis
deadline remain in effect. The model actually returned is logged, without the
transcript or credentials. Local timestamp and clip-duration validation remains.

The installed PydanticAI profile predates Sonnet 5.5, so the adapter explicitly
enables its advertised JSON schema support. Native output avoids relying on a
Sonnet-specific tool-calling setup when OpenRouter selects Luna.

Offline tests exercise the real SDK request encoding and parse responses naming
either model. They cannot prove remote failover, model quality, account limits,
or that current provider endpoints accept every parameter. Before deployment,
run both models against a cached transcript with a funded key, check actual
usage and returned clips, and complete a rendered-video test.

No automatic credit purchase is configured. OpenRouter is a shared gateway;
model fallback does not protect against an outage of OpenRouter itself. Paid
credit purchases and production deployment require the owner's approval.

References:
- https://openrouter.ai/docs/guides/routing/model-fallbacks
- https://openrouter.ai/docs/guides/routing/provider-selection
- https://openrouter.ai/api/v1/models
