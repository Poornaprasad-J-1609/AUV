# Active Stabilization — Trinetra TS100 AUV

This UI build works with `sea_backend_active_stabilization.py`.

## Operator control

The navigation control panel now contains a global **ACTIVE STABILIZATION: ON/OFF** button.

Backend protocol:

- `$STABILIZE,ON`
- `$STABILIZE,OFF`
- `$STABILIZE,STATUS`

The vehicle acknowledges with `ACK,STABILIZE,...` so the button remains synchronized even when the telemetry link is using CSV instead of JSON.

## What ON does

### During a waypoint route — PATH mode

Each active leg remains the fixed line from the previous waypoint (or mission-start position for leg 1) to the active waypoint. Cross-track error is measured against that fixed line.

The guidance law combines:

1. geometric line-of-sight / Pure-Pursuit cross-track correction, and
2. an integral crab-angle correction.

The integral term is important for a steady water current: it can retain a non-zero crab angle after the cross-track error has returned close to zero, so the AUV keeps counter-steering against the current instead of being pushed off the line again.

### Route stopped — HOLD mode

If the vehicle is armed and Active Stabilization is ON, stopping the route captures the current GPS position and heading.

- Inside the hold deadband, T1 stays neutral and T2 maintains the captured heading.
- If current pushes the AUV outside the outer hold radius, it turns toward the captured GPS point and uses limited T1 surge to return.
- Once it re-enters the inner radius, it returns to heading-hold mode.

### Manual override

Any non-neutral manual thruster/joystick command immediately disables Active Stabilization. Neutral UI heartbeat packets are ignored while station hold owns the thrusters, so the UI and hold controller do not fight each other.

### Safety behavior

- DISARM always prevents stabilization thrust.
- Emergency Stop in the UI explicitly sends `$STABILIZE,OFF` before the normal disarm/stop sequence.
- Stale GPS or AHRS in HOLD mode commands neutral thrust and reports `HOLD_SENSOR_WAIT`.
- Station hold does not run during docking.

## Main tuning constants in the backend

```python
ACTIVE_STAB_XTE_DEADBAND_M = 0.25
ACTIVE_STAB_XTE_KI_DEG_PER_M_S = 0.80
ACTIVE_STAB_XTE_INTEGRAL_LIMIT_M_S = 30.0
ACTIVE_STAB_MAX_CRAB_DEG = 45.0

ACTIVE_STAB_HOLD_ENTER_M = 2.0
ACTIVE_STAB_HOLD_EXIT_M = 1.0
ACTIVE_STAB_HOLD_MAX_SURGE_PWM = 180
ACTIVE_STAB_HOLD_MIN_SURGE_PWM = 45
ACTIVE_STAB_HOLD_MAX_YAW_PWM = 100
```

Start conservatively in a controlled tank/pond test. GPS accuracy and thruster geometry limit how tightly surface position can actually be held; the controller cannot guarantee mathematically zero deviation.
