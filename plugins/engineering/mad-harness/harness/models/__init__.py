"""Model routing for the harness: agent role, model tier and provider, kept apart.

Nothing here talks to a model. This package answers one question — *given an agent
and a task, what should the dispatcher run?* — and answers it in one place so the
dispatcher, the config check and the tests cannot disagree about precedence.
"""
