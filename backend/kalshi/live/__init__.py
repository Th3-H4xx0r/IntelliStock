"""Live in-match monitoring for Kalshi soccer (Kalshi-price-only).

While a match is in-play, the engine watches the live market, infers material
events from price action, re-reads commentary on those events, computes a hybrid
live fair value, and takes two-way action under in-play risk caps. The decision
math (clock, event detection, fair value, decision) is pure + unit-tested; only
``monitor`` touches the client/DB.
"""
