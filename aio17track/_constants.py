"""Internal numeric constants. Not part of the public API."""

# Tolerance when deciding a token-bucket token is available. Absorbs float
# refill rounding error: sleeping 1/3 s and refilling at 3 tokens/s computes
# 0.9999999999999999 tokens, which an exact >= 1.0 check rejects — leaving a
# residual deficit whose sleep is smaller than the clock's ulp, so acquire()
# would spin forever without making progress.
EPSILON = 1e-9

# Lower bound for any throttle sleep, so every wait visibly advances the
# clock (real or fake) and the acquire loop is guaranteed to make progress.
MIN_SLEEP_SECONDS = 1e-6
