---
name: framework-nextjs
description: "Next.js App Router doctrine for this repository: server versus client components, the data boundary, type generation, and the dev-server rules that cost a debugging session when broken. Loaded ON DEMAND by an agent working in a frontend lane — never preloaded."
---

# Next.js doctrine

**Loaded on demand.** The rules that are wrong most often are already in your
prompt's technology card. This is the depth behind them.

## Components

**App Router only.** Server components by default; reach for `"use client"` only
where the component genuinely needs interactivity — state, effects, event handlers.
A client component that only renders props has given up server rendering for nothing.

## The data boundary

Fetch directly in server components. On the client, use the project's data layer
rather than raw `fetch`.

**Mock at the API-client boundary, never at `fetch`.** A test that mocks `fetch`
asserts against the transport, so it keeps passing when the client contract changes
underneath it — which is precisely the regression it existed to catch.

## Types

Strict TypeScript: a type error is a build failure, not a warning.

**An API shape change needs its client types regenerated.** Skipping it leaves the
frontend compiling against a contract the backend no longer honours — and it compiles
cleanly, because the stale types are internally consistent. If your task changes a
response shape, the regeneration is part of it or it is a successor task, never an
assumption that someone will notice.

## Dev servers

The dev servers are already running. **Never start another, and never run a
production build while one is live** — they share a build output directory and the
build corrupts it for both. Recovering means deleting the directory and restarting,
which costs more than the build you were trying to run.
