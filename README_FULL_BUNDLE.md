# Trinetra AUV — Active Stabilization + Sensor Fusion Backend Link

This bundle contains the updated Ground Station UI and `sea_backend.py` together.

## 1. Active Stabilization

The previous Active Stabilization work is preserved:

- `PATH` mode: fixed-leg Pure Pursuit plus current-rejection / crab-angle correction.
- `HOLD` mode: GPS station hold when the mission is stopped and the vehicle is armed.
- Manual non-neutral thruster commands cancel stabilization.
- Neutral UI heartbeats do not overwrite station-hold thrust.
- The UI has an **ACTIVE STABILIZATION: ON/OFF** control and backend-confirmed status.

## 2. Sensor Fusion is now connected to the backend

The Sensor Fusion page already combines:

1. GPS + AHRS heading
2. RM3100 magnetometer anomaly
3. Ping360 sonar range/bearing
4. Camera Detection target bearing/range

It supports staged handover or weighted 2-D Kalman fusion.

### Data path

```text
Backend sensors
  GPS / AHRS / RM3100 / Ping360
            |
            v
      Ground Station UI
            + local camera detector
            |
            v
      Sensor Fusion page
      staged or Kalman fusion
            |
            |  $FUSE,...
            v
        sea_backend.py
      validate + timestamp
      store fused target
            |
            |  ACK,FUSE,...
            v
      UI BACKEND status
```

### UI -> backend protocol

Valid target:

```text
$FUSE,1,<range_m>,<relative_bearing_deg>,<sigma_m>,<sources>,<stage>
```

Example:

```text
$FUSE,1,2.35,-12.5,0.18,GSC,APPROACH
```

No valid fused target:

```text
$FUSE,0,,,,,
```

Status request:

```text
$FUSE,STATUS
```

Backend replies with:

```text
ACK,FUSE,<valid>,<range>,<bearing>,<sigma>,<sources>,<stage>,AGE=<seconds>
```

Malformed fused targets return `ERR,FUSE,...`.

## 3. UI behaviour

- **Send fused target to vehicle** is ON by default and persisted in QSettings.
- The Sensor Fusion page shows:
  - `BACKEND: CONNECTED · FIX`
  - `BACKEND: CONNECTED · NO FIX`
  - `BACKEND: WAITING FOR ACK`
  - `BACKEND: ERROR ...`
  - `BACKEND: DISCONNECTED`
- On connection the UI sends `$FUSE,STATUS` to verify the protocol.
- High-rate `$FUSE` packets are **coalesced**: only the newest fused target is waiting for TX, so stale targets are not replayed after a slow RF/TCP link.
- Switching fused-target transmission OFF sends `$FUSE,0` to invalidate the backend copy.

## 4. Backend behaviour

`VehicleState` now stores:

- `fusion_valid`
- `fusion_range_m`
- `fusion_bearing_rel_deg`
- `fusion_sigma_m`
- `fusion_sources`
- `fusion_stage`
- `last_fusion_rx`

The fused target is considered fresh for 1.5 s. The backend validates range, bearing and uncertainty before accepting a target.

The fused state is also present in JSON telemetry and backend CSV disk logs. The legacy/standard 25-field GCS CSV telemetry packet is intentionally unchanged for compatibility.

## Important control-authority note

The fused target is now fully connected, validated and available onboard, but this bundle does **not silently give Sensor Fusion direct thruster authority**. Existing waypoint, Active Stabilization and docking safety controllers remain the motor-command owners. This avoids an unexpected new actuator path simply from opening/checking the Sensor Fusion page.

If fused docking control is desired, it should be enabled as an explicit separate control mode so it can have arming, stale-data timeout, operator override and neutral-thrust interlocks.

## Validation performed

- Backend Python syntax compile: PASS
- Full UI Python syntax compile: PASS
- `$FUSE` valid target receive + ACK: PASS
- `$FUSE,STATUS`: PASS
- `$FUSE,0` clear target: PASS
- Invalid negative range rejection: PASS
- UI `ACK,FUSE` parser: PASS with lightweight Qt stub
- UI simulator FUSE round trip: PASS
- Latest-only `$FUSE` TX coalescing: PASS

The full PySide6 GUI was not launched inside the build container because PySide6 is not installed there; source compilation and protocol logic tests passed.

## 7-decimal GPS coordinate precision update

Latitude and longitude are preserved/formatted to 7 decimal places across the backend and UI coordinate paths: telemetry serialization, vehicle/UI logs, simulator data, dashboard display, waypoint map/console/table, mission save/load normalization, waypoint upload protocol, docking setup/protocol, and docking visualizations. Internal navigation math continues to use Python floating-point values without rounding intermediate calculations.

The backend accepts incoming GPS text at whatever precision the Teensy/GPS telemetry provides. To obtain real end-to-end 7-decimal source precision, the Teensy firmware must also transmit latitude/longitude with at least 7 decimal places; formatting downstream cannot recreate digits that were already truncated upstream.

The safe AUTO surge settings requested in the subsequent control fix are also retained in this bundle: linear authority +/-150 PWM, approach cap 150 PWM, station-hold surge cap 150 PWM, and T1 slew 5 PWM per 20 Hz tick.
