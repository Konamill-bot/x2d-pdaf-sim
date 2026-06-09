"""ISP pipeline-latency model.

Real-camera AF pipeline: sensor expose -> sensor readout -> ISP
demosaic/subsample -> PDAF row processing -> decision -> motor cmd.
There is non-zero latency from the moment a frame is exposed to the
moment its disparity affects the lens. At 60 fps this is typically
1-3 frames (~16-50 ms) depending on ISP scheduling.

We model this as a fixed-delay buffer wrapped around any policy:
measurements at sim frame k are fed to the policy as if they came
from frame k - LATENCY. The policy's predict-step (e.g. Kalman) is
then run for LATENCY extra frames so its state estimate is for the
current physical frame, not the stale-measurement frame.

This is what Sony / Canon / Nikon's published predictive-AF maths do
under the hood. It lets the temporal-prior policies stay correct
in the presence of pipeline delay rather than drifting because they
think every measurement is current.
"""
from __future__ import annotations
from collections import deque


class LatencyBuffer:
    """Holds the last N measurements; pop returns the measurement that
    is NOW arriving at the policy (i.e. was made N frames ago)."""

    def __init__(self, n_frames: int):
        self.n = max(0, int(n_frames))
        self.buf = deque()

    def push_pop(self, measurement):
        """Push current measurement, return what arrives at policy now.

        Returns None until the buffer fills (policy gets no input for
        the first N frames, modelling cold-start pipeline fill)."""
        self.buf.append(measurement)
        if len(self.buf) > self.n:
            return self.buf.popleft()
        return None
