# Sensors

A sensor observes the lifecycle and emits a signal. It never blocks, never approves, and never mutates runner-owned state — that separation is what keeps a sensor cheap to add and safe to ignore.

Use a sensor when you want to *measure* something before you are confident enough to *enforce* it. A rule that starts as a sensor accumulates evidence about how often it would have fired; that evidence is what justifies promoting it to a blocking policy or retiring it.

| Property | Sensor | Policy rule |
|---|---|---|
| Effect | Records a signal | Blocks the transition |
| Fires at | Any stage | A gate or Task verification |
| Cost of a false positive | Noise in a report | A stalled Intent |

Loop metrics are computed from committed artifacts by `ai-sdlc loop-metrics` and written to `.ai-sdlc/metrics/loop-metrics.json`. Add a sensor here when you need a signal that metric does not already carry, and record in the lesson registry which lesson the sensor is gathering evidence for.
