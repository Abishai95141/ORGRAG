# ADR-009: Permit pricing API in Python, not Go

Status: Accepted. Decided by Priya Raman, November 2026.

## Context

Book a Mooring needs a small API that prices a permit from the berth length, the season and the vessel type. Sam Doyle proposed Go because the latency budget for the price preview is 150 ms at the 95th percentile.

The GDS Way names Java, Python, Ruby and JavaScript/Node.js as the core backend languages. It keeps Go for platform engineering work: operators, controllers, command-line tools and routing.

## Decision

We'll write the pricing API in Python 3.12, as a module of the existing Django application, exposed at /api/price.

## Consequences

- One language for all of Book a Mooring's application code, so anyone on the team can review it.
- Sam will profile the Python version. If the 95th percentile goes over 150 ms we'll cache prices per berth band before we reconsider the language.
- The first measurement, on staging, was 41 ms at the 95th percentile.
