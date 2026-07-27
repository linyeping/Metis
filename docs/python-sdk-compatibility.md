# Python SDK compatibility policy

The PyPI distribution is `metis-agent-sdk`; consumers import `metis`. Package versions follow the Metis product release, while `metis.SDK_API_VERSION` identifies the stable public SDK contract.

## API level 1

The following surfaces are stable:

- `Agent`, `Agent.run()`, and `Agent.run_to_completion()`
- `AgentEvent`, `AgentEventKind`, and `AgentEvent.as_dict()`
- `AgentResult` and `AgentRunError`
- the `metis.agent_event.v1` event envelope and the meaning of existing fields

Compatible releases may add optional constructor or method parameters, new event kinds, new mapping fields, and new result fields with defaults. Removing a public name, making an optional argument required, or changing an existing field's meaning requires a documented deprecation period or a new SDK API level.

Provider quirks, model availability, built-in tools, and individual tool schemas evolve with the Metis runtime. Applications should capability-check those features instead of treating them as a frozen Python ABI.

Metis supports the Python versions declared in package metadata and tests the oldest and newest declared interpreter in CI. Type annotations are shipped through `py.typed`; the public package and example project are checked with mypy strict mode before publishing.
