#!/usr/bin/env python3
"""
sea_backend.py - Unified Autonomous Navigation & Telemetry Backend for Trinetra TS100 AUV
========================================================================================
Integrates the proven automatic navigation and PID waypoint following algorithms from
Surface_Level_Phase1_v2 with the low-latency hardware drivers and UI protocol handling
required by the NIOT Marine Ground Station UI.

Key Capabilities:
-----------------
1. Autonomous Navigation Engine:
   - Haversine geodesic distance & target bearing calculation (spherical trigonometry).
   - Shortest angular error calculation normalized to [-180, +180] degrees.
   - Reversed AHRS yaw direction, calibrated to the AUV nose heading.
   - Dual-loop PID controllers:
       * Angular PID (Heading Alignment) -> Differential yaw / sway thrust (T2).
       * Linear PID (Distance Closing)   -> Surge thrust (T1).
   - Multi-phase navigation state machine:
       * Phase 1: Turn-to-target alignment when |heading_error| > angle_threshold (default 40 deg).
       * Phase 2: Forward cruising when aligned, holding heading.
       * Phase 3: Waypoint arrival detection when distance < distance_threshold (default 3.0 m).
       * Phase 4: Route stepping through uploaded waypoints and mission completion.

2. Full Bidirectional UI Protocol Integration (src/connection.py & src/app.py):
   - Waypoint Upload: Receives "$WP,<idx>,<lat>,<lon>" and "$WP,END", validates route,
     and immediately replies with "ACK,WP\n" to enable Arming and Start in the UI.
   - Arm / Disarm: Handles "$ARM" and "$DISARM" commands with safety interlocks.
   - Mission Execution: Handles "$START" (starts autonomous loop), "$STOP" (halts & neutrals thrusters),
     and "$RTH" (returns to home position).
   - Manual Override: Seamlessly handles "CMD,p1,p2,p3,light,camera" and "$MANUAL,p1,p2,p3"
     when in manual mode or when operator intervenes.
   - Surface Level Phase 1 Compatibility: Parses the legacy 11-element Python literal array:
     [operating_mode, start_run_mode, no_wp, waypoints, lin_pid, ang_pid, fwd, lat, vert, light, cam].
   - Telemetry Stream: Emits high-rate compact JSON telemetry packets with all navigation and sensor readouts,
     plus "LOG,..." mission progress lines directly displayed in the UI Mission Logs table.

3. Low-Latency Hardware & Sensor Workers:
   - TeensyLink: Serial communication with Teensy 4.1 (/dev/ttyACM0 @ 115200) for chamber DHT22,
     RM3100 magnetometer, GPS telemetry (lat/lon/alt/sat/hdop/speed/course/UTC), thruster PWM, lights, and camera.
   - AhrsLink: Xsens Xbus/MTData2 binary communication with AHRS IMU (/dev/ttyUSB0 @ 115200), decoding XDI quaternion 0x2010 into roll/pitch/yaw.
   - BmsLink: SocketCAN worker for CAN-bus 48V/52V BMS pack.
   - Ping360Link: UDP socket listener on port 12345 for acoustic scanning sonar.
   - Multi-Transport SurfaceLink: Supports Serial (RF/P8 UART), TCP Socket Server, and UDP Client/Server.
"""

from __future__ import annotations

import argparse
import hashlib
import ast
import csv
import json
import math
import os
import queue
import signal
import socket
import struct
import subprocess
import threading
import time
from urllib.parse import urlparse
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

try:
    import serial
except ImportError:
    serial = None

try:
    import can
except ImportError:
    can = None

try:
    from brping import Ping360, definitions
    HAVE_BRPING = True
except ImportError:
    Ping360 = None
    definitions = None
    HAVE_BRPING = False


# =============================================================================
# OPERATIONAL DEFAULTS & HARDWARE CONFIGURATION
# =============================================================================

NEUTRAL_PWM = 1500
MIN_PWM = 1100
MAX_PWM = 1900

# Default Jetson hardware ports
DEFAULT_TEENSY_PORT = "COM3" if os.name == "nt" else "/dev/ttyACM0"   # Teensy 4.1 microcontroller
DEFAULT_TEENSY_BAUD = 115200

DEFAULT_AHRS_PORT = "/dev/ttyUSB0"     # AHRS IMU
DEFAULT_AHRS_BAUD = 115200

DEFAULT_RF_PORT = "/dev/ttyTHS1"       # P8 / RF link to Surface GCS
DEFAULT_RF_BAUD = 57600

DEFAULT_CAN_IFACE = "can0"              # SocketCAN BMS interface

# Ping360 Scanning Sonar Network & Auto-Transmit Defaults (from working brping script)
DEFAULT_SONAR_IP = "192.168.1.20"       # Ping360 Ethernet/UDP IP address
DEFAULT_SONAR_PORT = 12345             # Ping360 UDP communication port
DEFAULT_SONAR_SERIAL = None            # Direct Ping360 serial port (fallback: /dev/ttyUSB0)
DEFAULT_SONAR_BAUD = 115200            # Ping360 serial baud rate
DEFAULT_SONAR_START_GRAD = 100         # 200 gradians = 180.0 degrees
DEFAULT_SONAR_STOP_GRAD = 300          # 399 gradians = 359.1 degrees
DEFAULT_SONAR_SAMPLE_PERIOD = 500      # 500 * 25ns = 12.5us per sample
DEFAULT_SONAR_NUM_SAMPLES = 1000       # 1000 samples (~9.375m range)
DEFAULT_SONAR_TRANSMIT_DUR = 80        # 80 us pulse duration
DEFAULT_SONAR_GAIN = 1                 # 1 = Normal/Medium
MIN_VALID_SONAR_RANGE = 0.2            # meters: Ping360 near-field ring-down zone
SONAR_INTENSITY_THRESHOLD = 200         # 0-255 raw intensity echo threshold
SONAR_MIN_ECHO_WIDTH = 10               # consecutive samples required to count as a real echo
DEFAULT_TCP_PORT = 8888                 # TCP telemetry/command server port
DEFAULT_UDP_PORT = 8889                 # UDP telemetry/command server port

# Earth Radius for Geodesic navigation (meters)
EARTH_RADIUS = 6371000.0

# PID Navigation Constants (from Surface_Level_Phase1_v2 params.py)
DEFAULT_ANGLE_THRESHOLD = 15.0         # degrees: point nose at waypoint before thrust
DEFAULT_DISTANCE_THRESHOLD = 3.0       # meters: waypoint arrival tolerance radius
DEFAULT_BASE_SPEED = 1500              # neutral PWM

DEFAULT_LINEAR_KP = 7.0
DEFAULT_LINEAR_KD = 0.0
DEFAULT_LINEAR_KI = 0.0

DEFAULT_ANGULAR_KP = 2.0
DEFAULT_ANGULAR_KD = 0.0
DEFAULT_ANGULAR_KI = 0.0

DEFAULT_MIN_LINEAR_PWM = -150
DEFAULT_MAX_LINEAR_PWM = 150          # autonomous surge authority: 1500 +/- 150 PWM
DEFAULT_MIN_ANGULAR_PWM = -100
DEFAULT_MAX_ANGULAR_PWM = 100

GCS_TIMEOUT = 3.0                      # GCS link status only; onboard AUTO continues on telemetry loss
CONTINUE_ON_GCS_LOSS = True            # Requires working onboard GPS, AHRS, Teensy, and checkpoint storage
RESUME_LINK_STABLE_S = 2.0             # require uninterrupted communications before automatic recovery
AUTO_RESUME_MAX_OUTAGE_S = 10.0        # longer outages require explicit operator RESUME
AUTO_RESUME_MAX_DRIFT_M = 5.0          # do not autonomously resume after large position changes
MIN_RESUME_GPS_SATS = 6
MISSION_CHECKPOINT_PERIOD_S = 1.0
NAV_SENSOR_TIMEOUT = 2.0               # seconds: age limit for LIVE GPS/AHRS guidance
# A short GPS outage is NOT an emergency-stop condition on an already-running
# open-water route.  Bridge only a *bounded* interval with the last good GPS
# speed and the live AHRS heading; never infer waypoint arrival without GPS.
# These are deliberately conservative.  They do not provide underwater INS.
GPS_BRIDGE_MAX_OUTAGE_S = 6.0           # total age of the last *valid* GPS fix
GPS_BRIDGE_MAX_TRAVEL_M = 2.0           # distance integrated with the last GPS speed
GPS_BRIDGE_MAX_SPEED_MPS = 0.8          # refuse bridging at higher measured speeds
GPS_BRIDGE_UNKNOWN_SPEED_S = 3.0       # shorter grace when no usable GPS speed exists
GPS_BRIDGE_MIN_WAYPOINT_M = 4.0        # do not dead-reckon near a waypoint/dock
GPS_BRIDGE_MAX_SURGE_PWM = 65          # decreased propulsion authority during outage
GPS_BRIDGE_MAX_YAW_PWM = 50
GPS_REACQUIRE_STABLE_S = 0.5           # continuous healthy fix before returning to GPS
GPS_AUTO_RESUME_MAX_PAUSE_S = 20.0     # long loss requires explicit operator recovery
FUSION_RX_TIMEOUT_S = 1.5             # UI fused-target link freshness timeout
NAV_APPROACH_DISTANCE_M = 30.0          # begin slowing early enough to reach every waypoint
NAV_APPROACH_MAX_PWM = 150              # approach surge cap: 1500 +/- 150 PWM
NAV_AWAY_WINDOW_S = 8.0                 # compare GPS distance over this interval
NAV_AWAY_THRESHOLD_M = 5.0              # stop after sustained movement away
AUTONAV_T1_SLEW_PWM = 5                 # soft surge ramp: 5 PWM/tick @ 20 Hz = 100 PWM/s
AUTONAV_T2_SLEW_PWM = 10                # smooth yaw change per 20 Hz control tick

# Pure Pursuit path following.  The guidance point is placed this far ahead
# along the remaining waypoint polyline.  A short lookahead gives tighter
# path tracking; 2.0 m was requested for the current AUV trials.
PURE_PURSUIT_LOOKAHEAD_M = 2.0          # 12-sat GPS (~1-2 m noise): lookahead must be well above noise
# Intermediate waypoints are considered captured once the vehicle is close
# enough to hand over to the next path segment.  Keeping this below the
# lookahead distance lets Pure Pursuit start bending into the next segment
# before the waypoint is formally advanced.
PURE_PURSUIT_WAYPOINT_CAPTURE_M = 1.0
# If the vehicle passes an intermediate waypoint slightly wide, accept it
# only after it has first approached within this corridor.
PURE_PURSUIT_PASS_CORRIDOR_M = 2.0

# Heading-loop fixes (straight-line tracking)
TURN_EXIT_THRESHOLD_DEG = 8.0      # hysteresis: TURN enters above angle_threshold, exits below this
YAW_DEADZONE_DEG = 1.0             # no yaw command inside +/- this error
YAW_MIN_PWM = 30                   # minimum |T2| offset so small errors beat the ESC deadband
                                   # (set to your ESC deadband + ~5 PWM, measured on the bench)
NAV_MIN_SURGE_PWM = 60             # minimum forward offset in FORWARD so the linear P term
                                   # (7 x 3 m = 21 PWM near the WP) never stalls inside the ESC deadband
YAW_CMD_SIGN = -1.0                # -1: +heading error (target clockwise) -> T2 < 1500 (original behaviour)
                                   # flip with --invert-yaw-cmd if the AUV turns the wrong way

# Active horizontal stabilization / current rejection.
# PATH mode keeps each waypoint leg referenced to the original straight line.
# The geometric Pure-Pursuit correction removes instantaneous cross-track error,
# while the integral term learns a persistent crab angle that rejects a steady
# cross-current instead of settling with a constant lateral offset.
ACTIVE_STAB_XTE_DEADBAND_M = 0.25
ACTIVE_STAB_XTE_KI_DEG_PER_M_S = 0.80
ACTIVE_STAB_XTE_INTEGRAL_LIMIT_M_S = 30.0
ACTIVE_STAB_MAX_CRAB_DEG = 45.0

# Station-hold hysteresis. GPS on the surface is typically noisier than an INS,
# so the controller does not chase every sub-metre GPS fluctuation.
ACTIVE_STAB_HOLD_ENTER_M = 2.0
ACTIVE_STAB_HOLD_EXIT_M = 1.0
ACTIVE_STAB_HOLD_MAX_SURGE_PWM = 150
ACTIVE_STAB_HOLD_MIN_SURGE_PWM = 45
ACTIVE_STAB_HOLD_MAX_YAW_PWM = 100
ACTIVE_STAB_HOLD_HEADING_DEADZONE_DEG = 1.0

# Runtime precision/docking parameters. These are updated by the GCS $CONFIG packet.
DOCK_LINE_LENGTH_M = 10.0
DOCK_LOOKAHEAD_M = 1.5
DOCK_SPEED_SCALE = 0.50
DOCK_SLOWDOWN_DISTANCE_M = 3.0
DOCK_MIN_SPEED_SCALE = 0.20
DOCK_MIN_SURGE_PWM = 30            # minimum positive T1 offset after docking speed scaling


# =============================================================================
# AHRS HEADING OFFSET
# =============================================================================

# AHRS calibration for the PHYSICAL NOSE of the AUV.
# The previous code added another 180 degrees, which made the navigation
# reference correspond to the stern.  Keep the measured mounting/display
# calibration, but do NOT swap front and rear.
#
# heading = (AHRS_HEADING_OFFSET_DEG - raw_yaw) mod 360
#
# SINGLE SOURCE OF TRUTH: heading_calibration.json (in the backend log folder).
# There is NO hard-coded offset and NO command-line override any more.
# Calibrate on the vehicle (disarmed, fully assembled, nose on a known heading),
# from the GCS (Hardware Setup > Equipment Control > AHRS Heading Calibration)
# or from a terminal:
#   $CALHEADING,<deg>     -> nose pointing a known compass heading (0 = North)
#   $CALNORTH             -> same as $CALHEADING,0
#   $CALSTATUS            -> report offset / raw / corrected heading / calibrated
# Until a calibration file exists the heading is UNCALIBRATED (offset 0) and a
# warning is sent to the GCS on every START / RTH.
AHRS_HEADING_OFFSET_DEG = 0.0
HEADING_CALIBRATED = False
HEADING_CAL_PATH: Optional[str] = None


def correct_heading(raw_angle: float) -> float:
    """Convert Xsens raw yaw to the AUV physical-nose heading [0, 360)."""
    return (AHRS_HEADING_OFFSET_DEG - raw_angle) % 360.0


def load_heading_calibration(path: str) -> Optional[float]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            value = float(json.load(fh)["ahrs_heading_offset_deg"])
        return value % 360.0 if math.isfinite(value) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_heading_calibration(path: str, offset_deg: float, raw_deg: float, ref_deg: float) -> bool:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"ahrs_heading_offset_deg": round(offset_deg, 2),
                       "raw_yaw_at_cal_deg": round(raw_deg, 2),
                       "reference_heading_deg": round(ref_deg, 2),
                       "saved": time.strftime("%Y-%m-%d %H:%M:%S")}, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True
    except OSError as exc:
        print(f"[AHRS] Heading calibration save failed: {exc}")
        return False


# =============================================================================
# MATHEMATICAL & PID NAVIGATION ALGORITHMS (from algorithms.py)
# =============================================================================

def finite(value: object, default: Optional[float] = None) -> Optional[float]:
    """Return a JSON-safe finite float, or default."""
    try:
        if value is None or value == "":
            return default
        val = float(value)
        return val if math.isfinite(val) else default
    except (TypeError, ValueError):
        return default


def safe_float(value: object, default: Optional[float] = None) -> Optional[float]:
    """Safely convert any object/string to float."""
    return finite(value, default)


def safe_int(value: object, default: Optional[int] = None) -> Optional[int]:
    """Safely convert any object/string to integer."""
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def haversine_and_bearing(current: Tuple[float, float], target: Tuple[float, float]) -> Tuple[float, float]:
    """
    Computes geodesic distance in meters and target bearing in degrees.
    Formula identical to Surface_Level_Phase1_v2 algorithms.py.
    """
    phi1 = math.radians(current[0])
    phi2 = math.radians(target[0])
    lambda1 = math.radians(current[1])
    lambda2 = math.radians(target[1])

    dphi = (phi2 - phi1) / 2.0
    dlambda = (lambda2 - lambda1) / 2.0

    a = (math.sin(dphi)**2) + math.cos(phi1) * math.cos(phi2) * (math.sin(dlambda)**2)
    a = min(1.0, max(0.0, a))  # Clamp for domain safety
    distance = 2.0 * EARTH_RADIUS * math.asin(math.sqrt(a))

    y = math.sin(lambda2 - lambda1) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(lambda2 - lambda1)
    bearing_deg = math.degrees(math.atan2(y, x))
    normalised_bearing = (bearing_deg + 180.0) % 360.0 - 180.0

    return distance, normalised_bearing


def destination_point(start: Tuple[float, float], bearing_deg: float, distance_m: float) -> Tuple[float, float]:
    """Move ``distance_m`` from a GPS coordinate along an initial bearing."""
    if distance_m <= 0.0:
        return start

    lat1 = math.radians(start[0])
    lon1 = math.radians(start[1])
    brg = math.radians(bearing_deg)
    angular_distance = distance_m / EARTH_RADIUS

    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular_distance)
        + math.cos(lat1) * math.sin(angular_distance) * math.cos(brg)
    )
    lon2 = lon1 + math.atan2(
        math.sin(brg) * math.sin(angular_distance) * math.cos(lat1),
        math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
    )
    lon2 = (lon2 + math.pi) % (2.0 * math.pi) - math.pi
    return math.degrees(lat2), math.degrees(lon2)


def pure_pursuit_target(current: Tuple[float, float],
                        remaining_waypoints: List[Tuple[float, float]],
                        lookahead_m: float = PURE_PURSUIT_LOOKAHEAD_M) -> Tuple[float, float]:
    """Return a lookahead point on the remaining waypoint polyline.

    The path begins at the current GPS position, continues through the active
    waypoint, then through each later waypoint.  If the active waypoint is
    closer than the lookahead distance, the target naturally moves onto the
    following segment, which smooths corners instead of aiming at the waypoint
    centre until the last instant.
    """
    if not remaining_waypoints:
        return current

    remaining = max(0.0, float(lookahead_m))
    segment_start = current

    for segment_end in remaining_waypoints:
        segment_distance, segment_bearing = haversine_and_bearing(segment_start, segment_end)
        if segment_distance >= remaining:
            return destination_point(segment_start, segment_bearing, remaining)
        remaining -= segment_distance
        segment_start = segment_end

    # Less than one lookahead remains in the route: pursue the final waypoint.
    return remaining_waypoints[-1]



def pure_pursuit_target_leg(current: Tuple[float, float],
                            leg_start: Tuple[float, float],
                            remaining_waypoints: List[Tuple[float, float]],
                            lookahead_m: float = PURE_PURSUIT_LOOKAHEAD_M) -> Tuple[Tuple[float, float], float]:
    """True Pure Pursuit with cross-track correction.

    The AUV is projected onto the FIXED leg (leg_start -> active waypoint) and
    the guidance point is placed ``lookahead_m`` further along the route.  If
    the vehicle drifts sideways it steers back onto the original straight line
    instead of re-drawing a new line from its drifted position.

    Returns (guidance_point, cross_track_m).  cross_track_m > 0 = right of leg.
    """
    if not remaining_waypoints:
        return current, 0.0
    lat_s = EARTH_RADIUS * math.pi / 180.0
    lon_s = lat_s * math.cos(math.radians(current[0]))

    def enu(p):
        return ((p[1] - current[1]) * lon_s, (p[0] - current[0]) * lat_s)

    def geo(e, n):
        return (current[0] + n / lat_s, current[1] + e / lon_s)

    pts = [enu(leg_start)] + [enu(w) for w in remaining_waypoints]
    (ax, ay), (bx, by) = pts[0], pts[1]
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 < 1e-6:
        return pure_pursuit_target(current, remaining_waypoints, lookahead_m), 0.0
    # Vehicle is the ENU origin; project it onto the leg.
    t = max(0.0, min(1.0, (-ax * dx - ay * dy) / seg2))
    start = (ax + t * dx, ay + t * dy)
    cross_track = (dx * (-ay) - dy * (-ax)) / math.sqrt(seg2)  # z of leg x (vehicle - A)
    cross_track = -cross_track  # positive = vehicle right of leg direction

    remaining = max(0.0, float(lookahead_m))
    for end in pts[1:]:
        seg = math.hypot(end[0] - start[0], end[1] - start[1])
        if seg >= remaining and seg > 1e-6:
            f = remaining / seg
            return geo(start[0] + f * (end[0] - start[0]),
                       start[1] + f * (end[1] - start[1])), cross_track
        remaining -= seg
        start = end
    return remaining_waypoints[-1], cross_track


def intermediate_waypoint_captured(current: Tuple[float, float],
                                   waypoint: Tuple[float, float],
                                   leg_start: Tuple[float, float],
                                   closest_distance_m: float,
                                   capture_m: float = PURE_PURSUIT_WAYPOINT_CAPTURE_M,
                                   pass_corridor_m: float = PURE_PURSUIT_PASS_CORRIDOR_M) -> Tuple[bool, str]:
    """Capture an intermediate WP on proximity OR verified passage.

    Passing is accepted only if the vehicle approached within a bounded
    corridor AND is now beyond the perpendicular plane at the waypoint
    along the inbound leg. This avoids an endless orbit around an unreachable
    1 m GPS radius and avoids skipping a waypoint on a distant trajectory.
    """
    distance, _ = haversine_and_bearing(current, waypoint)
    if distance <= capture_m:
        return True, "RADIUS"
    if closest_distance_m > pass_corridor_m or distance > pass_corridor_m:
        return False, "NOT_CLOSE_ENOUGH"
    # Small local ENU vectors relative to waypoint (suitable for metre-scale paths).
    lat_scale = EARTH_RADIUS * math.pi / 180.0
    lon_scale = lat_scale * math.cos(math.radians(waypoint[0]))
    inbound_e = (waypoint[1] - leg_start[1]) * lon_scale
    inbound_n = (waypoint[0] - leg_start[0]) * lat_scale
    current_e = (current[1] - waypoint[1]) * lon_scale
    current_n = (current[0] - waypoint[0]) * lat_scale
    leg_len = math.hypot(inbound_e, inbound_n)
    if leg_len < 1.0:
        return False, "SHORT_LEG"
    past_plane_m = (current_e * inbound_e + current_n * inbound_n) / leg_len
    return (past_plane_m >= 0.5, "PASSED" if past_plane_m >= 0.5 else "APPROACHING")


def shortest_angle_error(current_deg: float, target_deg: float) -> float:
    """Computes shortest angular difference wrapped to [-180, +180] degrees."""
    error = target_deg - current_deg
    return (error + 180.0) % 360.0 - 180.0


def valid_coords(coords: Tuple[float, float]) -> bool:
    """Reject invalid geographic coordinates before they reach navigation."""
    lat, lon = coords
    return (math.isfinite(lat) and math.isfinite(lon)
            and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0
            and coords != (0.0, 0.0))


class MissionCheckpoint:
    """Crash-safe mission progress file stored locally on the vehicle.

    Telemetry CSVs are diagnostic logs, not authoritative route checkpoints:
    they may be buffered, incomplete, or from a different mission.  An atomic
    JSON checkpoint stores the exact route and active index at each transition.
    """
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.lock = threading.Lock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    @staticmethod
    def digest(route):
        canonical = json.dumps(route, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def save(self, route, index, status, gps=None, reason=""):
        route = [[float(lat), float(lon)] for lat, lon in route]
        if not route or not all(valid_coords(tuple(w)) for w in route):
            return False
        record = {
            "version": 1, "saved_utc": time.time(), "route": route,
            "route_sha256": self.digest(route), "active_index": int(index),
            "status": status, "reason": str(reason),
            "last_valid_gps": list(gps) if gps and valid_coords(gps) else None,
        }
        temp = self.path + ".tmp"
        with self.lock:
            try:
                with open(temp, "w", encoding="utf-8") as f:
                    json.dump(record, f, separators=(",", ":"), allow_nan=False)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp, self.path)
                if os.name == "posix":
                    fd = os.open(os.path.dirname(self.path), os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                return True
            except OSError as exc:
                print(f"[MISSION] CHECKPOINT WRITE FAILED: {exc}")
                return False

    def load(self):
        with self.lock:
            try:
                with open(self.path, encoding="utf-8") as f:
                    record = json.load(f)
                route = record["route"]
                idx = record["active_index"]
                if (record.get("version") != 1 or not isinstance(route, list) or not route
                    or not all(len(w) == 2 and valid_coords(tuple(w)) for w in route)
                    or not isinstance(idx, int) or not 0 <= idx <= len(route)
                    or self.digest(route) != record.get("route_sha256")):
                    raise ValueError("Invalid mission checkpoint")
                return record
            except (OSError, ValueError, KeyError, TypeError) as exc:
                print(f"[MISSION] Checkpoint unavailable/invalid: {exc}")
                return None


class PIDController:
    """
    Dual-axis PID controller implementing the exact algorithm from algorithms.py.
    """
    def __init__(self,
                 linear_gains: List[float] = None,
                 angular_gains: List[float] = None,
                 angle_threshold: float = DEFAULT_ANGLE_THRESHOLD,
                 distance_threshold: float = DEFAULT_DISTANCE_THRESHOLD):
        self.linear_coeffs = list(linear_gains) if linear_gains else [DEFAULT_LINEAR_KP, DEFAULT_LINEAR_KD, DEFAULT_LINEAR_KI]
        self.angular_coeffs = list(angular_gains) if angular_gains else [DEFAULT_ANGULAR_KP, DEFAULT_ANGULAR_KD, DEFAULT_ANGULAR_KI]
        self.angle_threshold = angle_threshold
        self.distance_threshold = distance_threshold

        self.linear_errors: List[float] = []
        self.angular_errors: List[float] = []
        self.target_reached = False
        self.turning = True   # start in TURN so the nose is aligned before surge

    def reset(self) -> None:
        """Clear error history and reset target_reached flag."""
        self.linear_errors.clear()
        self.angular_errors.clear()
        self.target_reached = False
        self.turning = True

    def set_gains(self, angular: Optional[List[float]] = None, linear: Optional[List[float]] = None) -> None:
        """Update PID gains for angular and linear loops."""
        if angular and len(angular) == 3:
            self.angular_coeffs = [float(x) for x in angular]
        if linear and len(linear) == 3:
            self.linear_coeffs = [float(x) for x in linear]

    def _calculate_pid_step(self, coeffs: List[float], error: float, history: List[float]) -> float:
        """
        Calculates u(t) = kp*e(t) + ki*sum(e) + kd*(e_{k-1} - e_k)
        Maintains a rolling window of up to 10 past error values.
        """
        history.append(error)
        if len(history) > 10:
            history.pop(0)

        kp, kd, ki = coeffs[0], coeffs[1], coeffs[2]
        integral = sum(history)
        derivative = (history[-2] - history[-1]) if len(history) >= 2 else 0.0

        u_t = (kp * error) + (ki * integral) + (kd * derivative)
        return u_t

    def compute_step(self,
                     current_coords: Tuple[float, float],
                     target_coords: Tuple[float, float],
                     current_heading_deg: float,
                     limits: Tuple[Tuple[float, float], Tuple[float, float]],
                     guidance_coords: Optional[Tuple[float, float]] = None,
                     arrival_threshold: Optional[float] = None) -> Tuple[float, float, float, float, str]:
        """
        Execute one navigation control step.

        ``target_coords`` is the real waypoint used for distance/arrival and
        surge control. ``guidance_coords`` may be a Pure Pursuit lookahead point
        used only for heading/yaw control.

        Returns: (linear_pwm_delta, angular_pwm_delta, distance_to_target,
                  guidance_bearing, direction)
        """
        error_distance, _ = haversine_and_bearing(current_coords, target_coords)
        steering_target = guidance_coords if guidance_coords is not None else target_coords
        _, target_bearing = haversine_and_bearing(current_coords, steering_target)
        error_angle = shortest_angle_error(current_heading_deg, target_bearing)

        # Arrival is evaluated against the real waypoint, never against the
        # moving Pure Pursuit target.
        threshold = self.distance_threshold if arrival_threshold is None else max(0.0, float(arrival_threshold))
        if error_distance <= threshold:
            self.target_reached = True
            return 0.0, 0.0, error_distance, target_bearing, "ARRIVED"

        # Angular PID
        orient_u_t = self._calculate_pid_step(self.angular_coeffs, error_angle, self.angular_errors)
        ang_min, ang_max = limits[0]
        limit_orient_u_t = max(ang_min, min(ang_max, orient_u_t))
        # Small heading errors must still produce real thrust: a P-only loop
        # gives 2 x 5 deg = 10 PWM, which sits inside the ESC deadband and
        # leaves a permanent heading error (curved track).
        if abs(error_angle) <= YAW_DEADZONE_DEG:
            limit_orient_u_t = 0.0
        elif abs(limit_orient_u_t) < YAW_MIN_PWM:
            limit_orient_u_t = math.copysign(YAW_MIN_PWM, error_angle)

        # Linear PID
        dist_u_t = self._calculate_pid_step(self.linear_coeffs, error_distance, self.linear_errors)
        lin_min, lin_max = limits[1]
        limit_dist_u_t = max(lin_min, min(lin_max, dist_u_t))

        # Turn first, then advance.  Do not apply surge while the physical nose
        # is outside the heading tolerance; otherwise the vehicle can travel
        # sideways/backwards while it is still trying to acquire the waypoint.
        self.target_reached = False
        angular_command = YAW_CMD_SIGN * limit_orient_u_t

        # Hysteresis: enter TURN above angle_threshold, return to FORWARD only
        # once below TURN_EXIT_THRESHOLD_DEG (prevents chatter at the threshold).
        if self.turning:
            if abs(error_angle) < min(TURN_EXIT_THRESHOLD_DEG, self.angle_threshold):
                self.turning = False
        elif abs(error_angle) > self.angle_threshold:
            self.turning = True

        if self.turning:
            return (
                0.0,
                angular_command,
                error_distance,
                target_bearing,
                "TURN"
            )

        if 0.0 <= limit_dist_u_t < NAV_MIN_SURGE_PWM:
            limit_dist_u_t = min(NAV_MIN_SURGE_PWM, lin_max)
        return (
            limit_dist_u_t,
            angular_command,
            error_distance,
            target_bearing,
            "FORWARD"
        )



# =============================================================================
# VEHICLE STATE
# =============================================================================

@dataclass
class VehicleState:
    """Thread-safe unified vehicle telemetry and operational state."""
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    started: float = field(default_factory=time.monotonic, repr=False)
    sequence: int = 0
    incoming_log_queue: object = field(default=None, repr=False)

    def log_incoming(self, source: str, payload: object) -> None:
        event_queue = self.incoming_log_queue
        if event_queue is not None:
            event_queue.put((time.time(), source, payload))

    # Operational Modes (0 = Manual, 1 = Automatic, 2 = Config)
    operating_mode: int = 0
    start_run_mode: int = 0
    is_armed: bool = False
    mission_completed: bool = False
    mission_paused: bool = False
    mission_pause_reason: str = ""
    mission_resume_pending: bool = False
    mission_recovered: bool = False
    gps_bridge_active: bool = False     # transient navigation degradation, NOT a latched pause
    last_checkpoint_gps: Optional[Tuple[float, float]] = None
    gcs_connected: bool = False
    last_heartbeat_rx: float = 0.0

    # Waypoints & Navigation
    waypoints: List[Tuple[float, float]] = field(default_factory=list)
    dock_waypoints: List[Tuple[float, float]] = field(default_factory=lambda: [(13.0827000, 80.2709000), (13.0827000, 80.2707000)])
    gps_docking_active: bool = False
    current_wp_index: int = 0
    home_coords: Optional[Tuple[float, float]] = None
    distance_to_target: float = 0.0
    target_bearing: float = 0.0
    heading_error: float = 0.0
    nav_direction: str = "STANDBY"

    # Active stabilization / current-rejection state.
    active_stabilization: bool = False
    stabilization_mode: str = "OFF"
    stabilization_hold_coords: Optional[Tuple[float, float]] = None
    stabilization_hold_heading: Optional[float] = None
    cross_track_m: float = 0.0
    stabilization_correction_deg: float = 0.0
    stabilization_hold_distance_m: float = 0.0

    # Sensor-fusion target received from the Ground Station UI.  This is a
    # validated shared target estimate (GPS/MAG/SONAR/CAMERA); it is kept
    # separate from the raw sensor fields so existing navigation remains safe.
    fusion_valid: bool = False
    fusion_range_m: Optional[float] = None
    fusion_bearing_rel_deg: Optional[float] = None
    fusion_sigma_m: Optional[float] = None
    fusion_sources: str = ""
    fusion_stage: str = "NO_FIX"
    last_fusion_rx: float = 0.0

    # Teensy firmware values
    temp: Optional[float] = None
    hum: Optional[float] = None
    pressure: Optional[float] = None
    magX: Optional[float] = None
    magY: Optional[float] = None
    magZ: Optional[float] = None
    magB: Optional[float] = None
    rm3100_valid: bool = True
    t1: int = NEUTRAL_PWM
    t2: int = NEUTRAL_PWM
    t3: int = NEUTRAL_PWM
    light: int = 0
    camera: int = 0
    failsafe: int = 0
    valid: int = 0
    cmds: int = 0
    altitude: float = 0.0
    hdop: float = 0.0
    speed_kmph: float = 0.0
    last_good_gps_speed_kmph: float = 0.0
    course_deg: float = 0.0
    gps_valid: int = 0
    gps_status: str = "FIX"
    utc: str = ""

    # Sensor values
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    raw_yaw: float = 0.0
    latitude: float = 0.0
    longitude: float = 0.0
    no_of_satellites: int = 0
    battery_voltage: float = 0.0
    battery_current: float = 0.0
    battery_power: float = 0.0
    battery_temp: float = 0.0
    soc: int = 0
    soh: int = 100
    sonar_range: Optional[float] = None
    sonar_bearing: Optional[float] = None
    sonar_scan_angle: float = 0.0
    sonar_enabled: bool = False

    # Video Recording State
    is_recording: bool = False
    recording_file: str = ""

    # Docking status and operator guidance
    is_docking: bool = False
    dock_cmd: str = "NULL"
    dock_axis: str = "stall"
    dock_heading: Optional[float] = None
    dock_err: Optional[float] = None
    dock_steering: Optional[float] = None
    dock_range: Optional[float] = None
    dock_mag: Optional[float] = None
    dock_quality: Optional[float] = None
    dock_base_ready: bool = False
    dock_cal_progress: int = 0
    dock_stage: str = "STANDBY"
    dock_guideline: str = "GPS docking standby. Set the approach and dock waypoints before engaging."

    # Link Health
    last_teensy_rx: float = 0.0
    last_ahrs_rx: float = 0.0
    last_gps_rx: float = 0.0
    last_command_rx: float = 0.0
    last_sonar_rx: float = 0.0
    last_sonar_contact: float = 0.0

    def set_waypoints(self, wps: List[Tuple[float, float]]) -> None:
        if not wps or not all(valid_coords(w) for w in wps):
            raise ValueError("waypoints must contain valid geographic coordinates")
        with self.lock:
            self.waypoints = list(wps)
            self.current_wp_index = 0
            self.mission_completed = False

    def update_teensy(self, values: list[str]) -> None:
        if not values or len(values) < 2:
            return
        values = [v.strip() for v in values]
        self.log_incoming("TEENSY", {"fields": list(values)})
        with self.lock:
            # Current 26-field continuous telemetry format also carries RPY.
            # The first 23 fields retain compatibility with earlier firmware.
            if len(values) >= 23:
                t = safe_float(values[0])
                h = safe_float(values[1])
                p1 = safe_int(values[2])
                p2 = safe_int(values[3])
                p3 = safe_int(values[4])
                lit = safe_int(values[5])
                cam = safe_int(values[6])
                fs = safe_int(values[7])
                v = safe_int(values[8])
                cmd_cnt = safe_int(values[9])
                gps_str = str(values[10]).strip()
                lat = safe_float(values[11])
                lon = safe_float(values[12])
                alt = safe_float(values[13])
                sats = safe_int(values[14])
                hdop = safe_float(values[15])
                spd = safe_float(values[16])
                course = safe_float(values[17])
                utc_str = str(values[18]).strip()
                mx = safe_float(values[19])
                my = safe_float(values[20])
                mz = safe_float(values[21])
                mb = safe_float(values[22])

                if t is not None: self.temp = t
                if h is not None: self.hum = h
                if p1 is not None and 1000 <= p1 <= 2000: self.t1 = p1
                if p2 is not None and 1000 <= p2 <= 2000: self.t2 = p2
                if p3 is not None and 1000 <= p3 <= 2000: self.t3 = p3
                if lit is not None: self.light = 1 if lit else 0
                if cam is not None: self.camera = 1 if cam else 0
                if fs is not None: self.failsafe = 1 if fs else 0
                # Firmware VALID mask:
                # bit 0 = temperature, bit 1 = humidity,
                # bit 2 = RM3100 (0x04), bit 3 = GPS (0x08).
                if v is not None:
                    self.valid = v
                    self.rm3100_valid = bool(v & 0x04)
                    gps_status_up = gps_str.upper()
                    explicit_no_fix = (
                        ("NO" in gps_status_up and "FIX" in gps_status_up)
                        or any(word in gps_status_up for word in
                               ("INVALID", "LOST", "DISCONNECT", "OFFLINE", "SEARCH"))
                    )
                    self.gps_valid = int(not explicit_no_fix and
                                         bool((v & 0x08) or
                                              ("FIX" in gps_status_up and "NO" not in gps_status_up)))

                if cmd_cnt is not None: self.cmds = cmd_cnt
                if gps_str: self.gps_status = gps_str
                if utc_str: self.utc = utc_str

                if alt is not None: self.altitude = alt
                if hdop is not None: self.hdop = hdop
                if spd is not None: self.speed_kmph = spd
                if course is not None: self.course_deg = course

                # GPS is now received ONLY through the Teensy's telemetry.
                # The Teensy itself reads GPS on Serial1 and embeds the result
                # in the 23-field telemetry packet.
                gps_has_position = (
                    lat is not None and lon is not None
                    and valid_coords((lat, lon))
                )
                is_gps_connected = bool(self.gps_valid and gps_has_position)

                if is_gps_connected:
                    self.latitude = lat
                    self.longitude = lon
                    self.no_of_satellites = max(0, sats) if sats is not None else 0
                    if spd is not None and math.isfinite(spd) and spd >= 0.0:
                        self.last_good_gps_speed_kmph = spd
                    self.last_gps_rx = time.monotonic()
                    if self.home_coords is None:
                        self.home_coords = (self.latitude, self.longitude)
                else:
                    self.gps_valid = 0
                    # Keep the last VALID coordinates as a last-known fix.
                    # The gps_valid flag tells UI consumers this is NOT live.
                    # In particular, never let (0,0) become a navigation fix.
                    self.no_of_satellites = max(0, sats) if sats is not None else 0
                    # Do not refresh last_gps_rx on an invalid GPS packet:
                    # it is the age of a real position fix, not Teensy uptime.

                if mx is not None: self.magX = mx
                if my is not None: self.magY = my
                if mz is not None: self.magZ = mz
                if mb is not None:
                    self.magB = mb
                elif mx is not None and my is not None and mz is not None:
                    self.magB = round(math.sqrt(mx**2 + my**2 + mz**2), 3)

                if mx == 0.0 and my == 0.0 and mz == 0.0 and (mb is None or mb == 0.0):
                    self.rm3100_valid = False
                elif v is not None and (v & 4) == 0:
                    self.rm3100_valid = False
                else:
                    self.rm3100_valid = True

            # Format B: Legacy 22-element format
            elif len(values) >= 22:
                t = safe_float(values[0])
                h = safe_float(values[1])
                if t is not None: self.temp = t
                if h is not None: self.hum = h

                mx = safe_float(values[2])
                my = safe_float(values[3])
                mz = safe_float(values[4])
                mb = safe_float(values[5])
                if mx is not None: self.magX = mx
                if my is not None: self.magY = my
                if mz is not None: self.magZ = mz
                if mb is not None: self.magB = mb
                elif self.magX is not None and self.magY is not None and self.magZ is not None:
                    self.magB = round(math.sqrt(self.magX**2 + self.magY**2 + self.magZ**2), 2)
                if mx == 0.0 and my == 0.0 and mz == 0.0 and (mb is None or mb == 0.0):
                    self.rm3100_valid = False
                else:
                    self.rm3100_valid = True

                p1 = safe_int(values[6])
                p2 = safe_int(values[7])
                p3 = safe_int(values[8])
                if p1 is not None and 1000 <= p1 <= 2000: self.t1 = p1
                if p2 is not None and 1000 <= p2 <= 2000: self.t2 = p2
                if p3 is not None and 1000 <= p3 <= 2000: self.t3 = p3

                l = safe_int(values[9])
                if l is not None: self.light = 1 if l else 0
                c = safe_int(values[10])
                if c is not None: self.camera = 1 if c else 0
                fs = safe_int(values[11])
                if fs is not None: self.failsafe = 1 if fs else 0

                v = safe_int(values[12])
                if v is not None:
                    self.valid = v
                    if (v & 0x08) == 0: self.rm3100_valid = False
                cmd_cnt = safe_int(values[13])
                if cmd_cnt is not None: self.cmds = cmd_cnt

                lat = safe_float(values[14])
                lon = safe_float(values[15])
                alt = safe_float(values[16])
                sats = safe_int(values[17])
                hdop = safe_float(values[18])
                spd = safe_float(values[19])
                course = safe_float(values[20])
                gv = safe_int(values[21])

                if alt is not None: self.altitude = alt
                if hdop is not None: self.hdop = hdop
                if spd is not None: self.speed_kmph = spd
                if course is not None: self.course_deg = course

                is_gps_connected = ((gv == 1) if gv is not None else ((self.valid & 8) != 0))
                is_gps_connected = bool(is_gps_connected and lat is not None and lon is not None
                                        and valid_coords((lat, lon)))
                self.gps_valid = 1 if is_gps_connected else 0

                if is_gps_connected and lat is not None and lon is not None and (abs(lat) > 0.0001 or abs(lon) > 0.0001):
                    self.latitude = lat
                    self.longitude = lon
                    self.no_of_satellites = max(0, sats) if sats is not None else 0
                    if spd is not None and math.isfinite(spd) and spd >= 0.0:
                        self.last_good_gps_speed_kmph = spd
                    self.last_gps_rx = time.monotonic()
                    if self.home_coords is None and self.latitude != 0.0 and self.longitude != 0.0:
                        self.home_coords = (self.latitude, self.longitude)
                else:
                    self.no_of_satellites = max(0, sats) if sats is not None else 0
                    # Do not overwrite the last valid fix or its timestamp.

            self.last_teensy_rx = time.monotonic()

    def update_ahrs(self, roll: float, pitch: float, yaw: float, apply_calibration: bool = True) -> None:
        self.log_incoming("AHRS", {"roll": roll, "pitch": pitch, "yaw": yaw, "apply_calibration": apply_calibration})
        with self.lock:
            self.roll = finite(roll, self.roll)
            self.pitch = finite(pitch, self.pitch)
            raw = finite(yaw, self.yaw)
            self.raw_yaw = raw
            self.yaw = correct_heading(raw) if apply_calibration else raw
            self.last_ahrs_rx = time.monotonic()

    def update_gps(self, latitude: float, longitude: float, satellites: int) -> None:
        self.log_incoming("GPS", {"latitude": latitude, "longitude": longitude, "satellites": satellites})
        with self.lock:
            self.no_of_satellites = max(0, int(satellites))
            coords = (finite(latitude, 0.0), finite(longitude, 0.0))
            self.gps_valid = int(self.no_of_satellites >= 4 and valid_coords(coords))
            if self.gps_valid:
                self.latitude, self.longitude = coords
                self.last_gps_rx = time.monotonic()
                if self.home_coords is None:
                    self.home_coords = coords

    def update_bms(self, voltage: float, current: float, soc: int, soh: int, temp: float) -> None:
        self.log_incoming("BMS", {"voltage": voltage, "current": current, "soc": soc, "soh": soh, "temperature": temp})
        with self.lock:
            self.battery_voltage = finite(voltage, self.battery_voltage)
            self.battery_current = finite(current, self.battery_current)
            self.battery_power = round(self.battery_voltage * self.battery_current, 2)
            self.soc = max(0, min(100, int(soc)))
            self.soh = max(0, min(100, int(soh)))
            self.battery_temp = finite(temp, self.battery_temp)

    def update_sonar(self, distance: object, bearing: object, scan_angle: object) -> None:
        self.log_incoming("PING360", {"distance": distance, "bearing": bearing, "scan_angle": scan_angle})
        d = finite(distance)
        b = finite(bearing)
        s = finite(scan_angle)
        now = time.monotonic()
        with self.lock:
            if d is not None and d >= MIN_VALID_SONAR_RANGE:
                self.sonar_range = d
                self.sonar_bearing = b % 360.0 if b is not None else (s % 360.0 if s is not None else None)
                self.last_sonar_contact = now
            elif self.last_sonar_contact > 0 and (now - self.last_sonar_contact > 3.0):
                # Only clear contact after 3 seconds of no echo
                self.sonar_range = None
                self.sonar_bearing = None
            self.sonar_scan_angle = s % 360.0 if s is not None else 0.0
            self.last_sonar_rx = now

    def json_packet(self) -> bytes:
        """Build compact, complete JSON packet compatible with src/connection.py."""
        with self.lock:
            self.sequence += 1
            # UI telemetry is LIVE GPS, whereas latitude/longitude stored in
            # VehicleState may deliberately retain the last good fix for a
            # short navigation bridge. Keep those semantics separate.
            gps_live = bool(self.gps_valid and
                            valid_coords((self.latitude, self.longitude)) and
                            self.last_gps_rx > 0.0 and
                            time.monotonic() - self.last_gps_rx <= NAV_SENSOR_TIMEOUT)
            ui_lat = self.latitude if gps_live else 0.0
            ui_lon = self.longitude if gps_live else 0.0
            ui_sats = self.no_of_satellites if gps_live else 0
            mode_label = "AUTO" if self.operating_mode == 1 else "MANUAL" if self.operating_mode == 0 else "CONFIG"
            t_val = round(self.temp, 2) if self.temp is not None else 0.0
            h_val = round(self.hum, 2) if self.hum is not None else 0.0
            mx_val = round(self.magX, 1) if self.magX is not None else 0.0
            my_val = round(self.magY, 1) if self.magY is not None else 0.0
            mz_val = round(self.magZ, 1) if self.magZ is not None else 0.0
            mb_val = round(self.magB, 1) if self.magB is not None else round(math.sqrt(mx_val**2 + my_val**2 + mz_val**2), 1)

            data = {
                "seq": self.sequence,
                "up_ms": int((time.monotonic() - self.started) * 1000),
                "mode": mode_label,
                "is_armed": 1 if self.is_armed else 0,
                "running": self.start_run_mode,
                "mission_paused": int(self.mission_paused),
                "mission_pause_reason": self.mission_pause_reason,
                "mission_recovered": int(self.mission_recovered),
                "gps_bridge_active": int(self.gps_bridge_active),
                "gcs_connected": int(self.gcs_connected),
                "roll": round(self.roll, 2),
                "pitch": round(self.pitch, 2),
                "yaw": round(self.yaw, 2),
                "raw_yaw": round(self.raw_yaw, 2),
                "heading_offset_deg": round(AHRS_HEADING_OFFSET_DEG, 2),
                "heading_calibrated": 1 if HEADING_CALIBRATED else 0,
                "latitude": round(ui_lat, 7),
                "longitude": round(ui_lon, 7),
                "satellites": int(ui_sats),
                "no_of_satellites": int(ui_sats),
                "last_known_gps_latitude": (round(self.latitude, 7) if
                                            valid_coords((self.latitude, self.longitude)) else None),
                "last_known_gps_longitude": (round(self.longitude, 7) if
                                             valid_coords((self.latitude, self.longitude)) else None),
                "temp": t_val,
                "hum": h_val,
                "magX": mx_val,
                "magY": my_val,
                "magZ": mz_val,
                "magB": mb_val,
                "battery_voltage": round(self.battery_voltage, 2),
                "battery_current": round(self.battery_current, 2),
                "battery_power": round(self.battery_power, 2),
                "battery_temp": round(self.battery_temp, 1),
                "soc": int(self.soc),
                "soh": int(self.soh),
                "t1": int(self.t1),
                "t2": int(self.t2),
                "t3": int(self.t3),
                "pwm1": int(self.t1),
                "pwm2": int(self.t2),
                "pwm3": int(self.t3),
                "light": int(self.light),
                "camera": int(self.camera),
                "failsafe": int(self.failsafe),
                "rm3100_valid": 1 if self.rm3100_valid else 0,
                "gps_valid": int(gps_live),
                "gps_fix_age_s": (round(max(0.0, time.monotonic() - self.last_gps_rx), 2)
                                  if self.last_gps_rx > 0.0 else None),
                "teensy_valid": int(self.valid),
                "teensy_cmds": int(self.cmds),
                "altitude": round(self.altitude, 2),
                "alt_m": round(self.altitude, 2),
                "hdop": round(self.hdop, 2),
                "speed_kmph": round(self.speed_kmph, 2),
                "course_deg": round(self.course_deg, 2),
                "sonar_range": round(self.sonar_range, 2) if self.sonar_range is not None else None,
                "sonar_bearing": round(self.sonar_bearing, 1) if self.sonar_bearing is not None else None,
                "sonar_scan_angle": round(self.sonar_scan_angle, 1),
                "sonar_active": 1 if (self.sonar_enabled or (time.monotonic() - self.last_sonar_rx < 4.0 and self.last_sonar_rx > 0)) else 0,
                "distance": round(self.distance_to_target, 2),
                "target_wp": self.current_wp_index + 1 if self.waypoints else 0,
                "total_wps": len(self.waypoints),
                "nav_dir": self.nav_direction,
                "target_bearing": round(self.target_bearing, 1),
                "heading_err": round(self.heading_error, 1),
                "active_stabilization": 1 if self.active_stabilization else 0,
                "stabilization_mode": self.stabilization_mode,
                "cross_track_m": round(self.cross_track_m, 3),
                "stabilization_correction_deg": round(self.stabilization_correction_deg, 2),
                "stabilization_hold_distance_m": round(self.stabilization_hold_distance_m, 3),
                "fusion_valid": 1 if (self.fusion_valid and self.last_fusion_rx > 0.0 and
                                             time.monotonic() - self.last_fusion_rx <= FUSION_RX_TIMEOUT_S) else 0,
                "fusion_range_m": round(self.fusion_range_m, 3) if self.fusion_range_m is not None else None,
                "fusion_bearing_rel_deg": round(self.fusion_bearing_rel_deg, 2) if self.fusion_bearing_rel_deg is not None else None,
                "fusion_sigma_m": round(self.fusion_sigma_m, 3) if self.fusion_sigma_m is not None else None,
                "fusion_sources": self.fusion_sources,
                "fusion_stage": self.fusion_stage,
                "fusion_age_s": round(max(0.0, time.monotonic() - self.last_fusion_rx), 3) if self.last_fusion_rx > 0.0 else None,
                "rec": 1 if self.is_recording else 0,
                "rec_file": self.recording_file or "",
                "docking": 1 if self.is_docking else 0,
                "dock_cmd": self.dock_cmd,
                "dock_axis": self.dock_axis,
                "dock_range": round(self.dock_range, 2) if self.dock_range is not None else None,
                "dock_err": round(self.dock_err, 1) if self.dock_err is not None else None,
                "dock_heading": round(self.dock_heading, 1) if self.dock_heading is not None else None,
                "dock_mag": round(self.dock_mag, 2) if self.dock_mag is not None else None,
                "dock_quality": round(self.dock_quality, 2) if self.dock_quality is not None else None,
                "dock_base_ready": 1 if self.dock_base_ready else 0,
                "dock_cal_progress": int(self.dock_cal_progress),
                "dock_stage": self.dock_stage,
                "dock_guideline": self.dock_guideline,
                "gps": self.gps_status if gps_live else "NO_FIX",
                "gps_status": self.gps_status if gps_live else "NO_FIX",
                "utc": self.utc or time.strftime("%H:%M:%S"),
                "time_utc": self.utc or time.strftime("%H:%M:%S"),
                "mx": round(mx_val, 3),
                "my": round(my_val, 3),
                "mz": round(mz_val, 3),
                "mag_total": round(mb_val, 3),
                "sat": int(self.no_of_satellites),
                "cmd": int(self.cmds),
                "speed": round(self.speed_kmph, 2),
                "course": round(self.course_deg, 2),
                "alt": round(self.altitude, 2),
            }
        return (json.dumps(data, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")

    def teensy_packet(self) -> bytes:
        """
        Build the exact 23-field Teensy telemetry format received from firmware:

        TEMP,HUM,PWM1,PWM2,PWM3,LIGHT,CAMERA,FAILSAFE,VALID,CMD,
        GPS_STATUS,LAT,LON,ALT,SAT,HDOP,SPEED,COURSE,UTC,MX,MY,MZ,MAG_TOTAL

        AHRS RPY is intentionally NOT taken from Teensy. It comes exclusively
        from the Xsens Xbus MTData2 reader.
        """
        with self.lock:
            gps_live = bool(self.gps_valid and
                            valid_coords((self.latitude, self.longitude)) and
                            self.last_gps_rx > 0.0 and
                            time.monotonic() - self.last_gps_rx <= NAV_SENSOR_TIMEOUT)
            t = self.temp
            h = self.hum
            p1 = int(self.t1)
            p2 = int(self.t2)
            p3 = int(self.t3)
            lit = int(self.light)
            cam = int(self.camera)
            fs = int(self.failsafe)
            v = (int(self.valid) & ~0x08) | (0x08 if gps_live else 0)
            cmds = int(self.cmds)
            gps_str = (self.gps_status if self.gps_status else "FIX") if gps_live else "NO_FIX"
            lat = self.latitude if gps_live else 0.0
            lon = self.longitude if gps_live else 0.0
            alt = self.altitude
            sats = int(self.no_of_satellites) if gps_live else 0
            hdop = self.hdop
            spd = self.speed_kmph
            crs = self.course_deg
            utc_str = self.utc if self.utc else ""
            mx = self.magX
            my = self.magY
            mz = self.magZ
            mb = self.magB

            def f(value, digits=3):
                return "nan" if value is None or not math.isfinite(float(value)) else f"{float(value):.{digits}f}"

            line = (
                f"{f(t,2)},{f(h,2)},{p1},{p2},{p3},{lit},{cam},{fs},{v},{cmds},"
                f"{gps_str},{f(lat,7)},{f(lon,7)},{f(alt,2)},{sats},{f(hdop,2)},"
                f"{f(spd,2)},{f(crs,2)},{utc_str},{f(mx,3)},{f(my,3)},{f(mz,3)},{f(mb,3)}\n"
            )
            return line.encode("ascii", errors="replace")

    def csv_packet(self) -> bytes:
        """
        Build the EXACT 25-field telemetry packet sent to the GCS/P8 link:

        roll,pitch,yaw,latitude,longitude,no_of_satellites,temperature,humidity,
        battery_volt,battery_current,battery_power,battery_temp,battery_soc,
        battery_soh,pwm1,pwm2,pwm3,light_status,camera_status,sonar_range,
        sonar_bearing,magx,magy,magz,magB

        Field order is fixed. Missing sensor values are transmitted as ``nan``;
        fields are never removed or shifted.
        """
        with self.lock:
            gps_live = bool(self.gps_valid and
                            valid_coords((self.latitude, self.longitude)) and
                            self.last_gps_rx > 0.0 and
                            time.monotonic() - self.last_gps_rx <= NAV_SENSOR_TIMEOUT)
            values = [
                self.roll,
                self.pitch,
                self.yaw,
                self.latitude if gps_live else 0.0,
                self.longitude if gps_live else 0.0,
                self.no_of_satellites if gps_live else 0,
                self.temp,
                self.hum,
                self.battery_voltage,
                self.battery_current,
                self.battery_power,
                self.battery_temp,
                self.soc,
                self.soh,
                self.t1,
                self.t2,
                self.t3,
                self.light,
                self.camera,
                self.sonar_range,
                self.sonar_bearing,
                self.magX,
                self.magY,
                self.magZ,
                self.magB,
            ]

            def fmt(value, digits):
                if value is None:
                    return "nan"
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    return "nan"
                if not math.isfinite(value):
                    return "nan"
                return f"{value:.{digits}f}"

            fields = [
                fmt(self.roll, 2),
                fmt(self.pitch, 2),
                fmt(self.yaw, 2),
                fmt(values[3], 7),
                fmt(values[4], 7),
                str(int(values[5])),
                fmt(self.temp, 2),
                fmt(self.hum, 2),
                fmt(self.battery_voltage, 2),
                fmt(self.battery_current, 2),
                fmt(self.battery_power, 2),
                fmt(self.battery_temp, 2),
                str(int(self.soc)) if self.soc is not None else "nan",
                str(int(self.soh)) if self.soh is not None else "nan",
                str(int(self.t1)) if self.t1 is not None else "nan",
                str(int(self.t2)) if self.t2 is not None else "nan",
                str(int(self.t3)) if self.t3 is not None else "nan",
                str(int(self.light)) if self.light is not None else "nan",
                str(int(self.camera)) if self.camera is not None else "nan",
                fmt(self.sonar_range, 2),
                fmt(self.sonar_bearing, 2),
                fmt(self.magX, 3),
                fmt(self.magY, 3),
                fmt(self.magZ, 3),
                fmt(self.magB, 3),
            ]

        return (",".join(fields) + "\n").encode("ascii")


# =============================================================================
# HARDWARE SENSOR WORKERS
# =============================================================================

class TeensyLink(threading.Thread):
    """Full-duplex serial owner of Teensy 4.1 port."""
    def __init__(self, port: str, baud: int, state: VehicleState, stop: threading.Event):
        super().__init__(daemon=True, name="TeensyWorker")
        self.port, self.baud, self.state, self.stop = port, baud, state, stop
        self.serial = None
        self.write_lock = threading.Lock()

    def send_command(self, fwd: int = 1500, lat: int = 1500, vert: int = 1500, light: int = 0, camera: int = 0) -> bool:
        cmd = (
            max(MIN_PWM, min(MAX_PWM, int(fwd))),
            max(MIN_PWM, min(MAX_PWM, int(lat))),
            max(MIN_PWM, min(MAX_PWM, int(vert))),
            1 if light else 0,
            1 if camera else 0,
        )
        payload = ("%d,%d,%d,%d,%d\n" % cmd).encode("ascii")
        with self.write_lock:
            if not self.serial or not self.serial.is_open:
                return False
            try:
                self.serial.write(payload)
                return True
            except Exception:
                return False

    def run(self) -> None:
        if not serial:
            return
        while not self.stop.is_set():
            try:
                with serial.Serial(self.port, self.baud, timeout=0, write_timeout=0.5) as ser:
                    with self.write_lock:
                        self.serial = ser
                    print(f"[TEENSY] Connected on {self.port} @ {self.baud}")
                    rx = bytearray()
                    while not self.stop.is_set():
                        waiting = ser.in_waiting
                        if waiting:
                            rx.extend(ser.read(waiting))
                            while b"\n" in rx:
                                raw, _, rx = rx.partition(b"\n")
                                line = raw.decode("ascii", errors="ignore").strip()
                                if line and not line.startswith("#") and line != "READY":
                                    try:
                                        self.state.update_teensy(line.split(","))
                                    except Exception:
                                        pass
                        else:
                            time.sleep(0.002)
            except Exception as e:
                with self.write_lock:
                    self.serial = None
                print(f"[TEENSY] Connection failed on {self.port}: {e}; retrying in 1 s...")
                self.stop.wait(1.0)


# =============================================================================
# XSENS XBUS / MTDATA2 AHRS DECODER
# =============================================================================

XSENS_SYNC = 0xFA
XSENS_MTDATA2_MID = 0x36
XSENS_XDI_QUATERNION = 0x2010


def xsens_extract_xbus_packets(buffer: bytearray):
    """Extract complete, checksum-valid Xbus packets from a byte buffer."""
    packets = []

    while True:
        try:
            start = buffer.index(XSENS_SYNC)
        except ValueError:
            buffer.clear()
            break

        if start > 0:
            del buffer[:start]

        # Minimum normal Xbus packet:
        # FA BUS MID LEN PAYLOAD CHECKSUM
        if len(buffer) < 5:
            break

        bus_id = buffer[1]
        mid = buffer[2]
        length_byte = buffer[3]

        if length_byte == 0xFF:
            # Extended length: FA BUS MID FF LEN_MSB LEN_LSB ... CHECKSUM
            if len(buffer) < 7:
                break
            payload_len = (buffer[4] << 8) | buffer[5]
            header_len = 6
        else:
            payload_len = length_byte
            header_len = 4

        total_len = header_len + payload_len + 1
        if len(buffer) < total_len:
            break

        frame = bytes(buffer[:total_len])
        del buffer[:total_len]

        # Xbus checksum: sum(BUS_ID..CHECKSUM) must be zero modulo 256.
        if (sum(frame[1:]) & 0xFF) != 0:
            continue

        packets.append((bus_id, mid, frame[header_len:-1]))

    return packets


def xsens_parse_quaternion(payload: bytes):
    """Return Xsens MTData2 quaternion as [w, x, y, z], or None."""
    i = 0

    while i + 3 <= len(payload):
        data_id = int.from_bytes(payload[i:i + 2], "big")
        size = payload[i + 2]
        i += 3

        if i + size > len(payload):
            break

        data = payload[i:i + size]
        i += size

        # XDI Quaternion is 0x2010. The low nibble can contain the XDI data
        # format/sub-format bits, so use the same mask as the standalone
        # Xsens reader.
        if (data_id & 0xFFF0) == XSENS_XDI_QUATERNION and size >= 16:
            return struct.unpack(">4f", data[:16])

    return None


def xsens_normalize_quaternion(quat):
    w, x, y, z = (float(v) for v in quat)
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm < 1e-8 or not math.isfinite(norm):
        return None
    return w / norm, x / norm, y / norm, z / norm


def xsens_quaternion_to_rotation_matrix(quat_wxyz):
    """Same quaternion -> rotation matrix math as the standalone Xsens reader."""
    w, x, y, z = quat_wxyz
    return (
        (1.0 - 2.0 * (y * y + z * z),
         2.0 * (x * y - z * w),
         2.0 * (x * z + y * w)),
        (2.0 * (x * y + z * w),
         1.0 - 2.0 * (x * x + z * z),
         2.0 * (y * z - x * w)),
        (2.0 * (x * z - y * w),
         2.0 * (y * z + x * w),
         1.0 - 2.0 * (x * x + y * y)),
    )


def xsens_unit_vector(v):
    n = math.sqrt(sum(float(x) * float(x) for x in v))
    if n < 1e-12 or not math.isfinite(n):
        return None
    return tuple(float(x) / n for x in v)


def xsens_dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def xsens_cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def xsens_force_right_hand_x_forward_z_up(rotation_raw):
    """Same axis reconstruction as the standalone Xsens reader."""
    x_axis = xsens_unit_vector((rotation_raw[0][0], rotation_raw[1][0], rotation_raw[2][0]))
    z_axis = xsens_unit_vector((rotation_raw[0][2], rotation_raw[1][2], rotation_raw[2][2]))

    if x_axis is None or z_axis is None:
        return None

    projection = xsens_dot(z_axis, x_axis)
    z_axis = xsens_unit_vector(tuple(z_axis[i] - projection * x_axis[i] for i in range(3)))
    if z_axis is None:
        return None

    y_axis = xsens_unit_vector(xsens_cross(z_axis, x_axis))
    if y_axis is None:
        return None

    z_axis = xsens_unit_vector(xsens_cross(x_axis, y_axis))
    if z_axis is None:
        return None

    rotation = (
        (x_axis[0], y_axis[0], z_axis[0]),
        (x_axis[1], y_axis[1], z_axis[1]),
        (x_axis[2], y_axis[2], z_axis[2]),
    )

    # Keep the same right-handedness guarantee as the standalone reader.
    det = (
        rotation[0][0] * (rotation[1][1] * rotation[2][2] - rotation[1][2] * rotation[2][1])
        - rotation[0][1] * (rotation[1][0] * rotation[2][2] - rotation[1][2] * rotation[2][0])
        + rotation[0][2] * (rotation[1][0] * rotation[2][1] - rotation[1][1] * rotation[2][0])
    )

    if det < 0.0:
        y_axis = tuple(-v for v in y_axis)
        z_axis = xsens_unit_vector(xsens_cross(x_axis, y_axis))
        if z_axis is None:
            return None
        rotation = (
            (x_axis[0], y_axis[0], z_axis[0]),
            (x_axis[1], y_axis[1], z_axis[1]),
            (x_axis[2], y_axis[2], z_axis[2]),
        )

    return rotation


def xsens_rotmat_to_rpy_deg(rotation):
    """Same XYZ roll/pitch/yaw extraction as the standalone Xsens reader."""
    roll = math.atan2(rotation[2][1], rotation[2][2])
    pitch = math.atan2(
        -rotation[2][0],
        math.sqrt(rotation[2][1] ** 2 + rotation[2][2] ** 2),
    )
    yaw = math.atan2(rotation[1][0], rotation[0][0])
    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


def xsens_quaternion_to_rpy(quat):
    normalized = xsens_normalize_quaternion(quat)
    if normalized is None:
        return None

    rotation = xsens_quaternion_to_rotation_matrix(normalized)
    rotation = xsens_force_right_hand_x_forward_z_up(rotation)
    if rotation is None:
        return None

    # sensor_to_base_rotation is identity in the user's Xsens configuration,
    # so no additional mount transform is required here.
    return xsens_rotmat_to_rpy_deg(rotation)


def xsens_send_measurement_mode(ser):
    """Switch the Xsens into measurement mode using the Xbus command frame."""
    ser.reset_input_buffer()
    ser.write(bytes.fromhex("FA FF 10 00 F1"))
    ser.flush()
    time.sleep(0.3)


class AhrsLink(threading.Thread):
    """Xsens MTi Xbus/MTData2 AHRS reader.

    The AHRS is a binary Xbus device, not an ASCII NMEA/CSV device.
    RPY is calculated from the XDI_QUATERNION (0x2010) item contained in
    MTData2 (MID 0x36), using the same orientation math as ahrs.py.
    """
    def __init__(self, port: str, baud: int, state: VehicleState, stop: threading.Event, calibrate: bool = True):
        super().__init__(daemon=True, name="AhrsWorker")
        self.port = port
        self.baud = baud
        self.state = state
        self.stop = stop
        self.calibrate = calibrate
        self.rx_buffer = bytearray()
        self.packet_count = 0
        self.bad_packets = 0

    def run(self) -> None:
        if not serial:
            print("[AHRS] pyserial not available; Xbus AHRS disabled")
            return

        while not self.stop.is_set():
            try:
                with serial.Serial(self.port, self.baud, timeout=0.001) as ser:
                    self.rx_buffer.clear()
                    self.packet_count = 0
                    self.bad_packets = 0

                    print(f"[AHRS] Xsens Xbus connected on {self.port} @ {self.baud}")
                    xsens_send_measurement_mode(ser)
                    print("[AHRS] Xsens measurement mode enabled (MTData2 / quaternion)")

                    while not self.stop.is_set():
                        waiting = int(getattr(ser, "in_waiting", 0))
                        if waiting > 0:
                            data = ser.read(waiting)
                            if data:
                                self.rx_buffer.extend(data)

                                for bus_id, mid, payload in xsens_extract_xbus_packets(self.rx_buffer):
                                    if mid != XSENS_MTDATA2_MID:
                                        continue

                                    quat = xsens_parse_quaternion(payload)
                                    if quat is None:
                                        continue

                                    rpy = xsens_quaternion_to_rpy(quat)
                                    if rpy is None:
                                        continue

                                    roll, pitch, raw_yaw = rpy
                                    self.packet_count += 1

                                    # Keep AHRS as the sole source of roll/pitch/yaw.
                                    # Heading calibration is applied only to yaw, exactly
                                    # as the existing VehicleState.update_ahrs() path does.
                                    self.state.update_ahrs(
                                        roll,
                                        pitch,
                                        raw_yaw,
                                        self.calibrate,
                                    )

                                    self.state.log_incoming("AHRS_XBUS", {
                                        "bus_id": bus_id,
                                        "mid": mid,
                                        "quat_w": quat[0],
                                        "quat_x": quat[1],
                                        "quat_y": quat[2],
                                        "quat_z": quat[3],
                                        "roll": roll,
                                        "pitch": pitch,
                                        "raw_yaw": raw_yaw,
                                        "packet_count": self.packet_count,
                                    })
                        else:
                            time.sleep(0.001)

            except Exception as e:
                print(f"[AHRS] Xbus port error on {self.port}: {e}; retrying in 1 s...")
                self.stop.wait(1.0)


class BmsLink(threading.Thread):
    """SocketCAN BMS receiver supporting both 0x1801A1xx and 0x18900140 telemetry protocols."""
    def __init__(self, iface: str, state: VehicleState, stop: threading.Event):
        super().__init__(daemon=True, name="BmsWorker")
        self.iface, self.state, self.stop = iface, state, stop

    def run(self) -> None:
        if not can:
            print("[BMS] python-can not available; BMS telemetry disabled")
            return
        while not self.stop.is_set():
            try:
                with can.Bus(channel=self.iface, interface="socketcan") as bus:
                    print(f"[BMS] Connected on {self.iface}")
                    while not self.stop.is_set():
                        msg = bus.recv(timeout=1.0)
                        if msg is None:
                            continue
                        data = msg.data
                        self.state.log_incoming("BMS_CAN_RAW", {
                            "arbitration_id": msg.arbitration_id,
                            "extended_id": bool(msg.is_extended_id),
                            "data": list(data),
                            "timestamp": getattr(msg, "timestamp", None)
                        })

                        # Protocol A: 0x1801A1FC / 0x1801A1FE / 0x1801A200 (Standard dry test node)
                        if msg.arbitration_id == 0x1801A1FC and len(data) >= 6:
                            raw_voltage = int.from_bytes(data[0:2], "little")
                            volts = raw_voltage * (0.01 if raw_voltage > 1000 else 0.1)
                            amps = int.from_bytes(data[2:6], "little", signed=True) * 0.1
                            self.state.update_bms(volts, amps, self.state.soc, self.state.soh, self.state.battery_temp)
                        elif msg.arbitration_id == 0x1801A1FE and len(data) >= 6:
                            soc_val = int(data[4])
                            soh_val = int(data[5]) if len(data) > 5 else 100
                            self.state.update_bms(self.state.battery_voltage, self.state.battery_current,
                                                  soc_val, soh_val, self.state.battery_temp)
                        elif msg.arbitration_id == 0x1801A200 and len(data) >= 8:
                            valid_temps = [finite(t) for t in struct.unpack("<8b", bytes(data[:8]))]
                            valid_temps = [t for t in valid_temps if t is not None]
                            b_temp = round(sum(valid_temps) / len(valid_temps), 1) if valid_temps else self.state.battery_temp
                            self.state.update_bms(self.state.battery_voltage, self.state.battery_current,
                                                  self.state.soc, self.state.soh, b_temp)

                        # Protocol B: 0x18900140 / 0x18920140 (Surface Phase 1 BMS)
                        elif msg.arbitration_id == 0x18900140 and len(data) >= 8:
                            volts = struct.unpack_from(">H", data, 0)[0] * 0.1
                            raw_curr = struct.unpack_from(">h", data, 2)[0] * 0.1
                            soc_val = int(struct.unpack_from(">H", data, 4)[0] * 0.1)
                            soh_val = int(struct.unpack_from(">H", data, 6)[0] * 0.1)
                            self.state.update_bms(volts, raw_curr, soc_val, soh_val, self.state.battery_temp)
                        elif msg.arbitration_id == 0x18920140 and len(data) >= 4:
                            t1 = struct.unpack_from(">h", data, 0)[0] * 0.1
                            self.state.update_bms(self.state.battery_voltage, self.state.battery_current,
                                                  self.state.soc, self.state.soh, t1)
            except Exception as e:
                print(f"[BMS] CAN error on {self.iface}: {e}; retrying in 2 s...")
                self.stop.wait(2.0)


def compute_ping360_range(sample_period: int, number_of_samples: int) -> float:
    """Ping360 max scan range in meters for a sample period/count (speed of sound = 1500 m/s)."""
    return sample_period * 25e-9 * number_of_samples * 1500 / 2.0


def find_obstacle_in_scan(samples: list, max_range: float, min_range: float = 0.2, threshold: int = 50) -> Optional[float]:
    """
    Locates the closest significant obstacle echo return in a Ping360 1D acoustic intensity array.
    Ignores initial acoustic ring-down / transducer housing cross-talk (< min_range).
    """
    if not samples or max_range <= 0.0:
        return None
    n = len(samples)
    range_per_sample = max_range / float(n)
    min_idx = max(3, int(min_range / range_per_sample))

    # Exclude near-field acoustic ring-down
    if min_idx >= n:
        return None
    valid_samples = samples[min_idx:]
    if not valid_samples:
        return None

    max_val = max(valid_samples)
    if max_val >= threshold:
        peak_idx = min_idx + valid_samples.index(max_val)
        return round(peak_idx * range_per_sample, 2)
    return None


def make_ping360_transducer_cmd(angle: int, gain: int = 1, sample_period: int = 80, num_samples: int = 200, transmit_duration: int = 32) -> bytes:
    """Construct a BlueRobotics Ping-protocol Message 2300 (TRANSDUCER request)."""
    payload = struct.pack("<BBHHHHH", 1, int(gain), int(angle) % 400, int(transmit_duration), int(sample_period), 750, int(num_samples))
    payload_len = len(payload)
    msg_id = 2300
    header = struct.pack("<2sHHBB", b"BR", payload_len, msg_id, 0, 1)
    msg = header + payload
    checksum = sum(msg) & 0xFFFF
    return msg + struct.pack("<H", checksum)


def parse_ping360_response(raw_bytes: bytes) -> Optional[dict]:
    """Parse a BlueRobotics Ping-protocol Message 2301 (DEVICE_DATA response)."""
    if len(raw_bytes) < 10 or raw_bytes[:2] != b"BR":
        return None
    payload_len, msg_id, src_id, dst_id = struct.unpack_from("<HHBB", raw_bytes, 2)
    if msg_id not in (2300, 2301, 2302) or len(raw_bytes) < 8 + payload_len + 2:
        return None
    payload = raw_bytes[8:8 + payload_len]
    chk_calc = sum(raw_bytes[:8 + payload_len]) & 0xFFFF
    chk_recv = struct.unpack_from("<H", raw_bytes, 8 + payload_len)[0]
    if chk_calc != chk_recv or len(payload) < 14:
        return None
    mode, gain, angle, tx_dur, sample_period, tx_freq, num_samples, data_len = struct.unpack_from("<BBHHHHHH", payload, 0)
    data = list(payload[14:14 + data_len])
    return {
        "angle": angle,
        "sample_period": sample_period,
        "number_of_samples": num_samples,
        "data": data,
        "angle_deg": angle * 0.9
    }


class Ping360Link(threading.Thread):
    """
    Continuous Ping360 scanning sonar communication worker.
    Connects to Ping360 at SONAR_IP:SONAR_PORT via brping (or native UDP socket fallback),
    configures auto-transmit sweep (200-399 grad / 180°-359.1°),
    processes real-time acoustic echo profiles, resolves closest obstacle range & bearing,
    and updates VehicleState for the GCS UI and mission logs.
    """
    def __init__(self,
                 state: VehicleState,
                 stop: threading.Event,
                 ip: str = DEFAULT_SONAR_IP,
                 port: int = DEFAULT_SONAR_PORT,
                 start_grad: int = DEFAULT_SONAR_START_GRAD,
                 stop_grad: int = DEFAULT_SONAR_STOP_GRAD,
                 sample_period: int = DEFAULT_SONAR_SAMPLE_PERIOD,
                 num_samples: int = DEFAULT_SONAR_NUM_SAMPLES,
                 gain: int = DEFAULT_SONAR_GAIN,
                 transmit_duration: int = DEFAULT_SONAR_TRANSMIT_DUR,
                 surface_emit_log: Optional[Callable[[str], None]] = None):
        super().__init__(daemon=True, name="Ping360Worker")
        self.state = state
        self.stop = stop
        self.ip = ip
        self.port = port
        self.start_grad = start_grad
        self.stop_grad = stop_grad
        self.sample_period = sample_period
        self.num_samples = num_samples
        self.gain = gain
        self.transmit_duration = transmit_duration
        self.emit_log = surface_emit_log
        self.enabled = threading.Event()
        # Start idle; the GCS $SONAR,START command enables scanning explicitly.
        self._force_reinit = False

    def activate(self) -> None:
        """Activate scanning sonar transmission and force immediate sweep reset."""
        self.enabled.set()
        self._force_reinit = True
        with self.state.lock:
            self.state.sonar_enabled = True
            self.state.last_sonar_rx = time.monotonic()
        msg = "[SONAR] Ping360 scanning sonar ACTIVATED."
        print(msg)
        if self.emit_log:
            self.emit_log(msg)

    def deactivate(self) -> None:
        """Deactivate scanning sonar, turn off motor, and clear state contacts."""
        self.enabled.clear()
        with self.state.lock:
            self.state.sonar_enabled = False
            self.state.sonar_range = None
            self.state.sonar_bearing = None
        msg = "[SONAR] Ping360 scanning sonar STOPPED."
        print(msg)
        if self.emit_log:
            self.emit_log(msg)

    def run(self) -> None:
        if HAVE_BRPING:
            self._run_brping()
        else:
            self._run_pure_udp()

    def _run_brping(self) -> None:
        print(f"[SONAR] Connecting to Ping360 @ UDP {self.ip}:{self.port} (brping)...")
        while not self.stop.is_set():
            if not self.enabled.is_set():
                self.stop.wait(0.5)
                continue
            sonar = None
            try:
                sonar = Ping360()
                sonar.connect_udp(self.ip, self.port)
                
                init_ok = sonar.initialize()
                if not init_ok:
                    print(f"[SONAR] Ping360 initialize() returned False @ {self.ip}:{self.port} — proceeding with direct auto-transmit configuration...")
                    
                msg = f"[SONAR] Ping360 connected @ {self.ip}:{self.port}! Scanning {self.start_grad * 0.9:.1f}° to {self.stop_grad * 0.9:.1f}° ({self.start_grad}-{self.stop_grad} grad)"
                print(msg)
                if self.emit_log:
                    self.emit_log(msg)
                    
                sonar.control_auto_transmit(
                    mode=1,
                    gain_setting=self.gain,
                    transmit_duration=self.transmit_duration,
                    sample_period=self.sample_period,
                    transmit_frequency=750,
                    number_of_samples=self.num_samples,
                    start_angle=self.start_grad,
                    stop_angle=self.stop_grad,
                    num_steps=1,
                    delay=0
                )
                
                # Speed of sound 1500 m/s
                distance_per_sample = 1500.0 * (self.sample_period * 25e-9) / 2.0
                max_range = self.num_samples * distance_per_sample
                last_log = time.monotonic()
                
                # Message types to accept
                target_msg_ids = []
                if definitions is not None:
                    if hasattr(definitions, 'PING360_AUTO_DEVICE_DATA'):
                        target_msg_ids.append(definitions.PING360_AUTO_DEVICE_DATA)
                    if hasattr(definitions, 'PING360_DEVICE_DATA'):
                        target_msg_ids.append(definitions.PING360_DEVICE_DATA)
                
                last_rx_time = time.monotonic()
                last_cmd_time = time.monotonic()
                self._force_reinit = False
                
                while not self.stop.is_set() and self.enabled.is_set():
                    now = time.monotonic()
                    # Re-send auto-transmit command if no data received for > 2.0 seconds or force_reinit requested
                    if self._force_reinit or (now - last_rx_time > 2.0 and now - last_cmd_time >= 2.0):
                        last_cmd_time = now
                        self._force_reinit = False
                        try:
                            sonar.control_auto_transmit(
                                mode=1,
                                gain_setting=self.gain,
                                transmit_duration=self.transmit_duration,
                                sample_period=self.sample_period,
                                transmit_frequency=750,
                                number_of_samples=self.num_samples,
                                start_angle=self.start_grad,
                                stop_angle=self.stop_grad,
                                num_steps=1,
                                delay=0
                            )
                        except Exception:
                            pass

                    msg = None
                    try:
                        if target_msg_ids:
                            msg = sonar.wait_message(target_msg_ids)
                        else:
                            msg = sonar.wait_message()
                    except Exception:
                        try:
                            msg = sonar.wait_message()
                        except Exception:
                            msg = None
                            
                    if msg is None:
                        continue
                        
                    last_rx_time = time.monotonic()
                    angle_grad = int(getattr(msg, 'angle', 0))
                    angle_deg = round(angle_grad * 0.9, 1)
                    raw_data = getattr(msg, 'data', None)
                    if raw_data is None:
                        continue
                    intensities = list(raw_data)
                    if not intensities:
                        continue
                        
                    s_per = int(getattr(msg, 'sample_period', self.sample_period))
                    d_per_sample = 1500.0 * (s_per * 25e-9) / 2.0
                    m_range = len(intensities) * d_per_sample
                    max_int = max(intensities)
                    
                    obstacle_dist = find_obstacle_in_scan(
                        samples=intensities,
                        max_range=m_range,
                        min_range=MIN_VALID_SONAR_RANGE,
                        threshold=SONAR_INTENSITY_THRESHOLD
                    )
                    
                    bearing_deg = angle_deg if obstacle_dist is not None else None
                    self.state.log_incoming("PING360_SCAN", {
                        "angle_grad": angle_grad,
                        "angle_deg": angle_deg,
                        "sample_period": s_per,
                        "samples": intensities
                    })
                    self.state.update_sonar(obstacle_dist, bearing_deg, angle_deg)
                    
                    now = time.monotonic()
                    if now - last_log >= 0.4:
                        last_log = now
                        dist_txt = f"{obstacle_dist:.2f} m" if obstacle_dist is not None else "open water"
                        print(f"[SONAR] Angle: {angle_deg:5.1f}° | Samples: {len(intensities):4d} | Max Int: {max_int:3d} | Target: {dist_txt}")
                    
            except Exception as e:
                print(f"[SONAR] Ping360 worker error: {e}; retrying in 2 s...")
                self.stop.wait(2.0)
            finally:
                if sonar:
                    try:
                        sonar.control_motor_off()
                    except Exception:
                        pass

    def _run_pure_udp(self) -> None:
        """Pure-Python binary Ping-protocol fallback if brping package is missing."""
        print(f"[SONAR] Connecting to Ping360 @ UDP {self.ip}:{self.port} (native socket)...")
        while not self.stop.is_set():
            if not self.enabled.is_set():
                self.stop.wait(0.5)
                continue
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(2.0)
                
                # Auto-Transmit command (Msg 2602)
                # Payload: <BBHHHHHHBB (mode=1, gain, tx_dur, sample_per, tx_freq=750, num_samples, start_grad, stop_grad, num_steps=1, delay=0)
                payload = struct.pack(
                    "<BBHHHHHHBB",
                    1,
                    int(self.gain),
                    int(self.transmit_duration),
                    int(self.sample_period),
                    750,
                    int(self.num_samples),
                    int(self.start_grad),
                    int(self.stop_grad),
                    1,
                    0
                )
                payload_len = len(payload)
                msg_id = 2602
                header = struct.pack("<2sHHBB", b"BR", payload_len, msg_id, 0, 1)
                cmd = header + payload
                chk = sum(cmd) & 0xFFFF
                sock.sendto(cmd + struct.pack("<H", chk), (self.ip, self.port))
                
                print(f"[SONAR] Ping360 Auto-Transmit command (Msg 2602) sent to {self.ip}:{self.port}")
                if self.emit_log:
                    self.emit_log(f"[SONAR] Ping360 connected @ {self.ip}:{self.port}! Scanning {self.start_grad * 0.9:.1f}° to {self.stop_grad * 0.9:.1f}° ({self.start_grad}-{self.stop_grad} grad)")
                    
                distance_per_sample = 1500.0 * (self.sample_period * 25e-9) / 2.0
                max_range = self.num_samples * distance_per_sample
                last_log = time.monotonic()
                last_cmd_tx = time.monotonic()
                self._force_reinit = False
                
                rx_buf = bytearray()
                while not self.stop.is_set() and self.enabled.is_set():
                    try:
                        if self._force_reinit:
                            self._force_reinit = False
                            sock.sendto(cmd + struct.pack("<H", chk), (self.ip, self.port))
                            last_cmd_tx = time.monotonic()

                        data, _ = sock.recvfrom(65535)
                        if not data:
                            continue
                        rx_buf.extend(data)
                        
                        while len(rx_buf) >= 8:
                            if rx_buf[:2] != b"BR":
                                rx_buf.pop(0)
                                continue
                            p_len, m_id, s_id, d_id = struct.unpack_from("<HHBB", rx_buf, 2)
                            pkt_len = 8 + p_len + 2
                            if len(rx_buf) < pkt_len:
                                break
                            packet = rx_buf[:pkt_len]
                            del rx_buf[:pkt_len]
                            
                            chk_c = sum(packet[:-2]) & 0xFFFF
                            chk_r = struct.unpack_from("<H", packet, pkt_len - 2)[0]
                            if chk_c == chk_r and m_id in (2300, 2301, 2302, 2601):
                                pld = packet[8:8 + p_len]
                                if len(pld) >= 14:
                                    mode, gain, angle, tx_dur, s_per, tx_freq, n_samp, data_len = struct.unpack_from("<BBHHHHHH", pld, 0)
                                    samples = list(pld[14:14 + data_len])
                                    if samples:
                                        angle_deg = round(angle * 0.9, 1)
                                        max_int = max(samples)
                                        d_per_s = 1500.0 * (s_per * 25e-9) / 2.0
                                        m_range = len(samples) * d_per_s
                                        
                                        obstacle_dist = find_obstacle_in_scan(
                                            samples=samples,
                                            max_range=m_range,
                                            min_range=MIN_VALID_SONAR_RANGE,
                                            threshold=SONAR_INTENSITY_THRESHOLD
                                        )
                                        bearing_deg = angle_deg if obstacle_dist is not None else None
                                        self.state.log_incoming("PING360_SCAN", {
                                            "angle_grad": angle,
                                            "angle_deg": angle_deg,
                                            "sample_period": s_per,
                                            "samples": samples
                                        })
                                        self.state.update_sonar(obstacle_dist, bearing_deg, angle_deg)
                                        
                                        now = time.monotonic()
                                        if now - last_log >= 0.4:
                                            last_log = now
                                            dist_txt = f"{obstacle_dist:.2f} m" if obstacle_dist is not None else "open water"
                                            print(f"[SONAR] Angle: {angle_deg:5.1f}° | Samples: {len(samples):4d} | Max Int: {max_int:3d} | Target: {dist_txt}")
                    except socket.timeout:
                        now = time.monotonic()
                        if now - last_cmd_tx >= 2.0:
                            sock.sendto(cmd + struct.pack("<H", chk), (self.ip, self.port))
                            last_cmd_tx = now
            except Exception as e:
                print(f"[SONAR] UDP socket error: {e}; retrying in 2 s...")
                self.stop.wait(2.0)
            finally:
                if sock:
                    try:
                        # Motor off (Msg 2603)
                        off_hdr = struct.pack("<2sHHBB", b"BR", 0, 2603, 0, 1)
                        off_chk = sum(off_hdr) & 0xFFFF
                        sock.sendto(off_hdr + struct.pack("<H", off_chk), (self.ip, self.port))
                    except Exception:
                        pass
                    try:
                        sock.close()
                    except Exception:
                        pass


# =============================================================================
# AUTONOMOUS NAVIGATION ENGINE (Surface_Level_Phase1_v2 logic)
# =============================================================================

class AutoNavigationEngine(threading.Thread):
    """
    Continuous 20Hz autonomous navigation thread running the proven PID algorithms.
    Controls T1 (Surge) and T2 (Sway/Yaw) while respecting safety flags, arming state,
    and GCS operator commands.
    """
    def __init__(self, state: VehicleState, teensy: TeensyLink, stop: threading.Event,
                 surface_emit_log: callable,
                 angle_threshold: float = DEFAULT_ANGLE_THRESHOLD,
                 distance_threshold: float = DEFAULT_DISTANCE_THRESHOLD,
                 checkpoint: Optional[MissionCheckpoint] = None):
        super().__init__(daemon=True, name="AutoNavEngine")
        self.state = state
        self.teensy = teensy
        self.stop = stop
        self.emit_log = surface_emit_log
        self.checkpoint = checkpoint
        self._last_checkpoint_time = 0.0
        self._gcs_link_was_connected = None  # transition logging only; not a navigation interlock
        self._paused_at_monotonic = None
        self._link_stable_since = None
        self._paused_gps = None
        self._neutral_unconfirmed = False
        self._last_neutral_retry = 0.0
        self._gps_resume_stable_since = None
        # A GPS bridge is permitted only after GPS-based guidance was running
        # for this mission; it must NEVER enable blind start on an old fix.
        self._gps_navigation_established = False
        self._gps_bridge_active = False
        self._gps_bridge_coords = None
        self._gps_bridge_last_t = None
        self._gps_bridge_start_fix_t = 0.0
        self._gps_bridge_distance_m = 0.0
        self._gps_bridge_speed_mps = 0.0
        self._gps_fix_returned_since = None
        self.pid = PIDController(angle_threshold=angle_threshold, distance_threshold=PURE_PURSUIT_WAYPOINT_CAPTURE_M)
        self.limits = (
            (DEFAULT_MIN_ANGULAR_PWM, DEFAULT_MAX_ANGULAR_PWM),
            (DEFAULT_MIN_LINEAR_PWM, DEFAULT_MAX_LINEAR_PWM)
        )
        self._progress_window = deque()
        self._progress_wp_index = None
        self._capture_wp_index = None
        self._capture_leg_start = None
        self._cross_track_m = 0.0
        self._last_xte_log = 0.0
        self._closest_wp_distance = float("inf")
        self._last_fwd_pwm = NEUTRAL_PWM
        self._last_lat_pwm = NEUTRAL_PWM

        # Active-stabilization controller memory.
        self._xte_integral_m_s = 0.0
        self._xte_last_gps_rx = None
        self._xte_leg_key = None
        self._hold_recovering = False
        self._station_heading_errors: List[float] = []
        self._hold_pid = PIDController(
            angle_threshold=20.0,
            distance_threshold=ACTIVE_STAB_HOLD_EXIT_M,
        )

    @staticmethod
    def _slew_pwm(previous: int, target: int, maximum_step: int) -> int:
        """Move one PWM command toward its target without abrupt steps."""
        delta = target - previous
        if abs(delta) <= maximum_step:
            return target
        return previous + (maximum_step if delta > 0 else -maximum_step)

    def _reset_pwm_slew(self) -> None:
        self._last_fwd_pwm = NEUTRAL_PWM
        self._last_lat_pwm = NEUTRAL_PWM

    def _reset_gps_bridge(self) -> None:
        """Drop GPS-only dead reckoning; never leave its telemetry flag latched."""
        self._gps_bridge_active = False
        self._gps_bridge_coords = None
        self._gps_bridge_last_t = None
        self._gps_bridge_start_fix_t = 0.0
        self._gps_bridge_distance_m = 0.0
        self._gps_bridge_speed_mps = 0.0
        self._gps_fix_returned_since = None
        with self.state.lock:
            self.state.gps_bridge_active = False

    def _gps_bridge_step(self, coords, heading, last_fix_t, speed_kmph, now):
        """Very short open-water DR estimate using LAST GOOD GPS speed + live AHRS.

        Does NOT certify the position or use its estimates to complete a WP.
        Returns (estimated_coords, reason_to_pause).  It never writes the
        estimated coordinates into raw GPS telemetry or the checkpoint.
        """
        fix_age = now - last_fix_t
        if not (last_fix_t > 0.0 and valid_coords(coords) and math.isfinite(fix_age)):
            return None, "GPS_NO_VALID_HISTORY"
        if fix_age > GPS_BRIDGE_MAX_OUTAGE_S:
            return None, "GPS_OUTAGE_EXCEEDED"

        if not self._gps_bridge_active:
            if not self._gps_navigation_established:
                return None, "GPS_REQUIRED_AT_START"
            if not (math.isfinite(speed_kmph) and speed_kmph >= 0.0):
                return None, "GPS_SPEED_INVALID"
            speed_mps = speed_kmph / 3.6
            if speed_mps > GPS_BRIDGE_MAX_SPEED_MPS:
                return None, "GPS_BRIDGE_SPEED_UNSAFE"
            self._gps_bridge_speed_mps = speed_mps
            self._gps_bridge_coords = coords
            self._gps_bridge_last_t = last_fix_t
            self._gps_bridge_start_fix_t = last_fix_t
            self._gps_bridge_distance_m = 0.0
            self._gps_bridge_active = True
            self._gps_fix_returned_since = None
            with self.state.lock:
                self.state.gps_bridge_active = True
            self._progress_window.clear()
            self.emit_log(
                f"[AUTO] GPS FIX LOST; continuing current leg in LIMITED GPS_BRIDGE "
                f"(max {GPS_BRIDGE_MAX_OUTAGE_S:.1f}s / {GPS_BRIDGE_MAX_TRAVEL_M:.1f}m estimated, "
                f"last speed {speed_mps:.2f}m/s). Waypoint advancement suspended."
            )

        outage_age = now - self._gps_bridge_start_fix_t
        if outage_age > (GPS_BRIDGE_UNKNOWN_SPEED_S if self._gps_bridge_speed_mps < 0.05
                         else GPS_BRIDGE_MAX_OUTAGE_S):
            return None, "GPS_OUTAGE_EXCEEDED"
        dt = max(0.0, now - self._gps_bridge_last_t)
        predicted_distance = self._gps_bridge_speed_mps * dt
        if self._gps_bridge_distance_m + predicted_distance > GPS_BRIDGE_MAX_TRAVEL_M:
            return None, "GPS_BRIDGE_DISTANCE_LIMIT"
        self._gps_bridge_coords = destination_point(
            self._gps_bridge_coords, heading, predicted_distance
        ) if predicted_distance > 0.0 else self._gps_bridge_coords
        self._gps_bridge_distance_m += predicted_distance
        self._gps_bridge_last_t = now
        return self._gps_bridge_coords, None

    def _reset_stabilization_memory(self) -> None:
        """Reset controller memory without changing the operator ON/OFF switch."""
        self._xte_integral_m_s = 0.0
        self._xte_last_gps_rx = None
        self._xte_leg_key = None
        self._hold_recovering = False
        self._station_heading_errors.clear()
        self._hold_pid.reset()

    def _active_stab_guidance(self, current_coords, leg_start, target_coords,
                              cross_track_m, gps_stamp, lookahead_m):
        """Return a fixed-leg LOS target with integral current compensation.

        The geometric correction pulls the AUV back toward the exact segment.
        The integral term learns a persistent crab angle, allowing a steady
        cross-current to be rejected even when cross-track error becomes small.
        """
        leg_key = (tuple(leg_start), tuple(target_coords))
        if self._xte_leg_key != leg_key:
            self._xte_leg_key = leg_key
            self._xte_integral_m_s = 0.0
            self._xte_last_gps_rx = None

        if gps_stamp and gps_stamp != self._xte_last_gps_rx:
            if self._xte_last_gps_rx is None:
                dt = 0.0
            else:
                dt = max(0.0, min(2.0, float(gps_stamp - self._xte_last_gps_rx)))
            self._xte_last_gps_rx = gps_stamp
            if dt > 0.0 and abs(cross_track_m) >= ACTIVE_STAB_XTE_DEADBAND_M:
                self._xte_integral_m_s += cross_track_m * dt
                self._xte_integral_m_s = max(
                    -ACTIVE_STAB_XTE_INTEGRAL_LIMIT_M_S,
                    min(ACTIVE_STAB_XTE_INTEGRAL_LIMIT_M_S, self._xte_integral_m_s),
                )

        _, leg_bearing = haversine_and_bearing(leg_start, target_coords)
        lookahead = max(0.5, float(lookahead_m))
        geometric_deg = math.degrees(math.atan2(cross_track_m, lookahead))
        integral_deg = ACTIVE_STAB_XTE_KI_DEG_PER_M_S * self._xte_integral_m_s
        correction_deg = max(
            -ACTIVE_STAB_MAX_CRAB_DEG,
            min(ACTIVE_STAB_MAX_CRAB_DEG, geometric_deg + integral_deg),
        )

        # cross_track > 0 means RIGHT of the segment -> bias bearing LEFT.
        desired_bearing = (leg_bearing - correction_deg) % 360.0
        guidance_distance = max(lookahead, 3.0)
        guidance = destination_point(current_coords, desired_bearing, guidance_distance)
        return guidance, correction_deg, desired_bearing

    def _station_heading_command(self, heading_error_deg: float) -> float:
        """Yaw-only command used while the captured GPS hold point is satisfied."""
        orient = self._hold_pid._calculate_pid_step(
            self._hold_pid.angular_coeffs, heading_error_deg, self._station_heading_errors
        )
        orient = max(-ACTIVE_STAB_HOLD_MAX_YAW_PWM, min(ACTIVE_STAB_HOLD_MAX_YAW_PWM, orient))
        if abs(heading_error_deg) <= ACTIVE_STAB_HOLD_HEADING_DEADZONE_DEG:
            orient = 0.0
        elif abs(orient) < YAW_MIN_PWM:
            orient = math.copysign(YAW_MIN_PWM, heading_error_deg)
        return YAW_CMD_SIGN * orient

    def _execute_station_hold_step(self) -> None:
        """Hold the captured surface GPS position and heading against current.

        With T1/T2 this is point-and-drive station keeping: inside the GPS
        hysteresis band, yaw holds the captured heading. If current pushes the
        AUV outside the outer radius, it points toward the captured point and
        uses limited surge to return.
        """
        now = time.monotonic()
        with self.state.lock:
            enabled = self.state.active_stabilization
            armed = self.state.is_armed
            mission_running = bool(self.state.start_run_mode)
            docking = self.state.is_docking
            current_coords = (self.state.latitude, self.state.longitude)
            current_heading = self.state.yaw
            gps_valid = self.state.gps_valid
            last_gps_rx = self.state.last_gps_rx
            last_ahrs_rx = self.state.last_ahrs_rx
            hold_coords = self.state.stabilization_hold_coords
            hold_heading = self.state.stabilization_hold_heading
            light, camera = self.state.light, self.state.camera

        if not enabled or not armed or mission_running or docking:
            return

        sensors_ok = (
            gps_valid and valid_coords(current_coords)
            and last_gps_rx > 0.0 and now - last_gps_rx <= NAV_SENSOR_TIMEOUT
            and last_ahrs_rx > 0.0 and now - last_ahrs_rx <= NAV_SENSOR_TIMEOUT
            and math.isfinite(current_heading)
        )
        if not sensors_ok:
            with self.state.lock:
                self.state.stabilization_mode = "HOLD_SENSOR_WAIT"
                self.state.nav_direction = "STAB_HOLD_SENSOR_WAIT"
                self.state.t1 = self.state.t2 = self.state.t3 = NEUTRAL_PWM
            self.teensy.send_command(NEUTRAL_PWM, NEUTRAL_PWM, NEUTRAL_PWM, light, camera)
            self._reset_pwm_slew()
            time.sleep(0.05)
            return

        if hold_coords is None or not valid_coords(hold_coords):
            hold_coords = current_coords
            hold_heading = current_heading
            with self.state.lock:
                self.state.stabilization_hold_coords = hold_coords
                self.state.stabilization_hold_heading = hold_heading
            self._hold_recovering = False
            self._hold_pid.reset()
            self._station_heading_errors.clear()
            self.emit_log(
                f"[STAB] Station-hold reference captured at "
                f"({hold_coords[0]:.7f}, {hold_coords[1]:.7f}), heading {hold_heading:.1f} deg."
            )

        if hold_heading is None or not math.isfinite(hold_heading):
            hold_heading = current_heading
            with self.state.lock:
                self.state.stabilization_hold_heading = hold_heading

        hold_dist, hold_bearing = haversine_and_bearing(current_coords, hold_coords)
        if not self._hold_recovering and hold_dist >= ACTIVE_STAB_HOLD_ENTER_M:
            self._hold_recovering = True
            self._hold_pid.reset()
            self._station_heading_errors.clear()
        elif self._hold_recovering and hold_dist <= ACTIVE_STAB_HOLD_EXIT_M:
            self._hold_recovering = False
            self._hold_pid.reset()
            self._station_heading_errors.clear()

        if self._hold_recovering:
            hold_limits = (
                (-ACTIVE_STAB_HOLD_MAX_YAW_PWM, ACTIVE_STAB_HOLD_MAX_YAW_PWM),
                (0.0, ACTIVE_STAB_HOLD_MAX_SURGE_PWM),
            )
            lin_u_t, ang_u_t, _, bearing, direction = self._hold_pid.compute_step(
                current_coords=current_coords,
                target_coords=hold_coords,
                current_heading_deg=current_heading,
                limits=hold_limits,
                guidance_coords=hold_coords,
                arrival_threshold=ACTIVE_STAB_HOLD_EXIT_M,
            )
            if direction == "ARRIVED":
                self._hold_recovering = False
                lin_u_t = 0.0
                err = shortest_angle_error(current_heading, hold_heading)
                ang_u_t = self._station_heading_command(err)
                direction = "HOLD"
                bearing = hold_heading
            elif direction == "FORWARD" and 0.0 < lin_u_t < ACTIVE_STAB_HOLD_MIN_SURGE_PWM:
                lin_u_t = float(ACTIVE_STAB_HOLD_MIN_SURGE_PWM)
            fwd_pwm = int(NEUTRAL_PWM + lin_u_t)
            lat_pwm = int(NEUTRAL_PWM + ang_u_t)
            nav_dir = "STAB_HOLD_RECOVER" if direction != "HOLD" else "STAB_HOLD"
            heading_target = bearing
        else:
            heading_err = shortest_angle_error(current_heading, hold_heading)
            ang_u_t = self._station_heading_command(heading_err)
            fwd_pwm = NEUTRAL_PWM
            lat_pwm = int(NEUTRAL_PWM + ang_u_t)
            nav_dir = "STAB_HOLD"
            heading_target = hold_heading

        fwd_pwm = max(MIN_PWM, min(MAX_PWM, fwd_pwm))
        lat_pwm = max(MIN_PWM, min(MAX_PWM, lat_pwm))
        fwd_pwm = self._slew_pwm(self._last_fwd_pwm, fwd_pwm, AUTONAV_T1_SLEW_PWM)
        lat_pwm = self._slew_pwm(self._last_lat_pwm, lat_pwm, AUTONAV_T2_SLEW_PWM)
        self._last_fwd_pwm = fwd_pwm
        self._last_lat_pwm = lat_pwm

        with self.state.lock:
            if (not self.state.active_stabilization or not self.state.is_armed
                    or self.state.start_run_mode or self.state.is_docking):
                return
            self.state.stabilization_mode = "HOLD_RECOVER" if self._hold_recovering else "HOLD"
            self.state.stabilization_hold_distance_m = hold_dist
            self.state.cross_track_m = 0.0
            self.state.stabilization_correction_deg = 0.0
            self.state.distance_to_target = hold_dist
            self.state.target_bearing = heading_target
            self.state.heading_error = shortest_angle_error(current_heading, heading_target)
            self.state.nav_direction = nav_dir
            self.state.t1 = fwd_pwm
            self.state.t2 = lat_pwm
            self.state.t3 = NEUTRAL_PWM
            sent = self.teensy.send_command(
                fwd_pwm, lat_pwm, NEUTRAL_PWM, self.state.light, self.state.camera
            )
        if not sent:
            with self.state.lock:
                self.state.stabilization_mode = "HOLD_TEENSY_WAIT"
                self.state.nav_direction = "STAB_HOLD_TEENSY_WAIT"
                self.state.t1 = self.state.t2 = self.state.t3 = NEUTRAL_PWM
            self._reset_pwm_slew()
        time.sleep(0.05)

    def _save_progress(self, status, reason=""):
        if not self.checkpoint:
            return True
        with self.state.lock:
            route = list(self.state.waypoints)
            idx = self.state.current_wp_index
            gps = (self.state.latitude, self.state.longitude)
            gps_ok = bool(self.state.gps_valid and valid_coords(gps) and
                          time.monotonic() - self.state.last_gps_rx <= NAV_SENSOR_TIMEOUT)
        return self.checkpoint.save(route, idx, status, gps if gps_ok else None, reason)

    def _pause(self, reason):
        """Retain route and index. Stop propulsion before attempting recovery."""
        self._reset_gps_bridge()
        with self.state.lock:
            if self.state.mission_paused:
                return
            coords = (self.state.latitude, self.state.longitude)
            # Retain the LAST VALID position even if the receiver is now NO_FIX.
            # This makes GPS recovery drift-checkable instead of permanently
            # disabling automatic resume for intermittent receiver outages.
            history_usable = bool(valid_coords(coords) and self.state.last_gps_rx > 0.0 and
                                  time.monotonic() - self.state.last_gps_rx <=
                                  GPS_BRIDGE_MAX_OUTAGE_S + 2.0)
            self._paused_gps = coords if history_usable else None
            self.state.mission_paused = True
            self.state.mission_pause_reason = reason
            self.state.nav_direction = "PAUSED_" + reason
            self.state.t1 = self.state.t2 = self.state.t3 = NEUTRAL_PWM
            sent = self.teensy.send_command(NEUTRAL_PWM, NEUTRAL_PWM, NEUTRAL_PWM,
                                            self.state.light, self.state.camera)
        self._paused_at_monotonic = time.monotonic()
        self._neutral_unconfirmed = not sent
        self._last_neutral_retry = 0.0
        self._link_stable_since = None
        self._gps_resume_stable_since = None
        self.pid.reset()
        self._progress_window.clear()
        self._reset_pwm_slew()
        saved = self._save_progress("PAUSED", reason)
        self.emit_log(f"[AUTO] PAUSED: {reason}; neutral={'sent' if sent else 'FAILED'}; "
                      f"checkpoint={'saved' if saved else 'FAILED'}. Waypoint preserved.")
        if not sent:
            self.emit_log("[AUTO] CRITICAL: Teensy neutral command unconfirmed. Hardware watchdog must neutralize motors. Operator inspection required before RESUME.")

    def _resume_checks(self, *, require_gcs=True):
        now = time.monotonic()
        with self.state.lock:
            gps = (self.state.latitude, self.state.longitude)
            if not self.state.is_armed:
                return False, "DISARMED"
            if not self.state.gps_valid or not valid_coords(gps) or self.state.no_of_satellites < MIN_RESUME_GPS_SATS:
                return False, "GPS_INVALID"
            if now - self.state.last_gps_rx > NAV_SENSOR_TIMEOUT:
                return False, "GPS_STALE"
            if now - self.state.last_ahrs_rx > NAV_SENSOR_TIMEOUT or not math.isfinite(self.state.yaw):
                return False, "AHRS_STALE"
            if self.state.last_command_rx <= 0 or (require_gcs and
                    now - self.state.last_command_rx > GCS_TIMEOUT):
                return False, "GCS_STALE"
            if self.state.current_wp_index >= len(self.state.waypoints):
                return False, "ROUTE_COMPLETE"
        return True, "OK"

    def _pause_step(self):
        # Reassert neutral only once a second when the serial hardware returns.
        # A failed write is NOT proof that physical thrusters are neutral.
        if self._neutral_unconfirmed and time.monotonic() - self._last_neutral_retry >= 1.0:
            self._last_neutral_retry = time.monotonic()
            with self.state.lock:
                sent = self.teensy.send_command(NEUTRAL_PWM, NEUTRAL_PWM, NEUTRAL_PWM,
                                                 self.state.light, self.state.camera)
            if sent:
                self._neutral_unconfirmed = False
                self.emit_log("[AUTO] Teensy accepted neutral after reconnect; operator RESUME required.")
        with self.state.lock:
            if not self.state.mission_paused:
                return
            pause_reason = self.state.mission_pause_reason
            # GCS reconnect conditions remain unchanged for non-GPS faults.
            if self.state.last_command_rx and time.monotonic() - self.state.last_command_rx <= GCS_TIMEOUT:
                if self._link_stable_since is None:
                    self._link_stable_since = time.monotonic()
            else:
                self._link_stable_since = None
            pending = self.state.mission_resume_pending
            coords_now = (self.state.latitude, self.state.longitude)
            gps_restored = bool(
                self.state.gps_valid and valid_coords(coords_now)
                and self.state.no_of_satellites >= MIN_RESUME_GPS_SATS
                and self.state.last_gps_rx > 0.0
                and time.monotonic() - self.state.last_gps_rx <= NAV_SENSOR_TIMEOUT
            )
        gps_pause = pause_reason in (
            "GPS_FIX_LOST", "GPS_NO_VALID_HISTORY", "GPS_REQUIRED_AT_START",
            "GPS_SPEED_INVALID", "GPS_BRIDGE_SPEED_UNSAFE", "GPS_OUTAGE_EXCEEDED",
            "GPS_BRIDGE_DISTANCE_LIMIT", "GPS_BRIDGE_NEAR_WAYPOINT",
            "GPS_REQUIRED_FOR_DOCKING",
        )
        # A position jump is ambiguous (multipath / moved vessel / bad fix):
        # require an operator to verify the new position before resuming.
        if gps_pause and gps_restored:
            if self._gps_resume_stable_since is None:
                self._gps_resume_stable_since = time.monotonic()
        else:
            self._gps_resume_stable_since = None
        # An already-running mission does not require live GCS telemetry for
        # auto-recovery from a GPS-only pause. Explicit operator RESUME still
        # requires a stable GCS connection.
        checks_ok, reason = self._resume_checks(
            require_gcs=not (gps_pause and CONTINUE_ON_GCS_LOSS and not pending)
        )
        with self.state.lock:
            self.state.gcs_connected = bool(self.state.last_command_rx and
                                            time.monotonic() - self.state.last_command_rx <= GCS_TIMEOUT)
        if self._neutral_unconfirmed or not checks_ok:
            time.sleep(0.05)
            return
        now = time.monotonic()
        stable = self._link_stable_since is not None and now - self._link_stable_since >= RESUME_LINK_STABLE_S
        gps_stable = (self._gps_resume_stable_since is not None and
                      now - self._gps_resume_stable_since >= GPS_REACQUIRE_STABLE_S)
        elapsed = now - self._paused_at_monotonic if self._paused_at_monotonic is not None else float("inf")
        if self._paused_gps:
            with self.state.lock:
                coords = self.state.latitude, self.state.longitude
            drift, _ = haversine_and_bearing(self._paused_gps, coords)
        else:
            drift = float("inf")
        automatic_gcs = (not pending and not self.state.mission_recovered and stable
                         and elapsed <= AUTO_RESUME_MAX_OUTAGE_S
                         and drift <= AUTO_RESUME_MAX_DRIFT_M
                         and pause_reason == "GCS_TIMEOUT")
        automatic_gps = (gps_pause and not pending and not self.state.mission_recovered
                         and gps_stable and elapsed <= GPS_AUTO_RESUME_MAX_PAUSE_S
                         and drift <= AUTO_RESUME_MAX_DRIFT_M)
        automatic = (automatic_gcs or automatic_gps) and not self._neutral_unconfirmed
        # If Teensy command delivery was previously lost, automatic recovery
        # is never permitted even after neutral writes succeed.
        if not (automatic or (pending and stable and not self._neutral_unconfirmed)):
            time.sleep(0.05)
            return
        if not self._save_progress("RUNNING", "RESUMED"):
            self.emit_log("[AUTO] Resume blocked: checkpoint write failed.")
            time.sleep(0.2)
            return
        with self.state.lock:
            if not self.state.mission_paused or not self.state.is_armed:
                return
            self.state.mission_paused = False
            self.state.mission_resume_pending = False
            self.state.mission_recovered = False
            self.state.mission_pause_reason = ""
            self.state.nav_direction = "RESUMING"
            index = self.state.current_wp_index
        self._paused_at_monotonic = None
        self._link_stable_since = None
        self._gps_resume_stable_since = None
        self._paused_gps = None
        self._gps_navigation_established = True
        self._reset_gps_bridge()
        self.pid.reset()
        self._progress_window.clear()
        self._progress_wp_index = None
        self._reset_pwm_slew()
        self.emit_log(f"[AUTO] {'AUTOMATIC GPS RECOVERY' if automatic_gps else 'AUTOMATIC' if automatic else 'OPERATOR'} RESUME "
                      f"from current GPS, target waypoint {index + 1} (2 m Pure Pursuit).")

    def _abort(self, reason: str) -> None:
        """Latch autonomous motion off and request neutral thrust."""
        self._reset_gps_bridge()
        self._gps_navigation_established = False
        with self.state.lock:
            self.state.start_run_mode = 0
            self.state.operating_mode = 0
            self.state.gps_docking_active = False
            self.state.is_docking = False
            self.state.mission_paused = False
            self.state.mission_resume_pending = False
            self.state.nav_direction = reason
            self.state.t1 = self.state.t2 = self.state.t3 = NEUTRAL_PWM
            light, camera = self.state.light, self.state.camera
            # Serialize with navigation's thrust write below.
            sent = self.teensy.send_command(NEUTRAL_PWM, NEUTRAL_PWM, NEUTRAL_PWM, light, camera)
        self.pid.reset()
        self._progress_window.clear()
        self._progress_wp_index = None
        self._reset_pwm_slew()
        self._reset_stabilization_memory()
        self._save_progress("ABORTED", reason)
        self.emit_log(f"[AUTO] ABORT: {reason}. Neutral command {'sent' if sent else 'FAILED'}; restart required.")

    def run(self) -> None:
        print("[AUTONAV] Navigation engine started (20 Hz loop)")
        while not self.stop.is_set():
            # GPS docking shares the regular waypoint controller while retaining
            # the docking status flag used by telemetry and the operator UI.
            gps_dock_route = self.state.is_docking and self.state.gps_docking_active
            mission_active = (
                self.state.operating_mode == 1 and self.state.start_run_mode == 1
                and self.state.is_armed and (not self.state.is_docking or gps_dock_route)
            )
            station_hold_active = (
                self.state.active_stabilization and self.state.is_armed
                and self.state.start_run_mode == 0 and not self.state.is_docking
            )
            if mission_active:
                if self.state.mission_paused:
                    self._pause_step()
                else:
                    self._execute_auto_step()
            elif station_hold_active:
                self.pid.reset()
                self._progress_window.clear()
                self._progress_wp_index = None
                self._capture_wp_index = None
                self._execute_station_hold_step()
            else:
                self.pid.reset()
                self._progress_window.clear()
                self._progress_wp_index = None
                self._capture_wp_index = None   # new START/RTH re-anchors leg 1 at the current position
                self._reset_pwm_slew()
                self._reset_gps_bridge()
                self._gps_navigation_established = False
                self._reset_stabilization_memory()
                with self.state.lock:
                    if not self.state.active_stabilization:
                        self.state.stabilization_mode = "OFF"
                        self.state.cross_track_m = 0.0
                        self.state.stabilization_correction_deg = 0.0
                        self.state.stabilization_hold_distance_m = 0.0
                    elif not self.state.is_armed:
                        self.state.stabilization_mode = "DISARMED"
                time.sleep(0.05)

    def _execute_auto_step(self) -> None:
        with self.state.lock:
            wps = list(self.state.waypoints)
            idx = self.state.current_wp_index
            current_coords = (self.state.latitude, self.state.longitude)
            current_heading = self.state.yaw
            gps_valid = self.state.gps_valid
            last_gps_rx = self.state.last_gps_rx
            last_good_speed_kmph = self.state.last_good_gps_speed_kmph
            gps_satellites = self.state.no_of_satellites
            last_ahrs_rx = self.state.last_ahrs_rx
            last_command_rx = self.state.last_command_rx
        if not wps:
            self._abort("NO_WAYPOINTS")
            return

        if idx >= len(wps):
            if not self.state.mission_completed:
                self.state.mission_completed = True
                self.state.nav_direction = "COMPLETED"
                with self.state.lock:
                    was_gps_docking = self.state.gps_docking_active
                    self.state.gps_docking_active = False
                    if was_gps_docking:
                        self.state.is_docking = False
                        self.state.operating_mode = 0
                        self.state.start_run_mode = 0
                        self.state.dock_stage = "DOCKED"
                        self.state.dock_guideline = "GPS docking route complete. Thrusters are neutral."
                    self.state.t1 = NEUTRAL_PWM
                    self.state.t2 = NEUTRAL_PWM
                    self.state.t3 = NEUTRAL_PWM
                self._save_progress("COMPLETED")
                self.emit_log(f"[AUTO] Mission complete! All {len(wps)} waypoints successfully reached.")
                self.teensy.send_command(NEUTRAL_PWM, NEUTRAL_PWM, NEUTRAL_PWM, self.state.light, self.state.camera)
            time.sleep(0.1)
            return

        now = time.monotonic()
        # Only the ground-station telemetry/control *transport* has gone away.
        # The running mission remains on this vehicle and needs no continuous
        # heartbeat to calculate Pure Pursuit or reach remaining waypoints.
        # Only brief, distance-limited GPS interruptions are bridged; AHRS,
        # Teensy and checkpoint failures retain their existing protections.
        gcs_now_connected = bool(last_command_rx and now - last_command_rx <= GCS_TIMEOUT)
        with self.state.lock:
            self.state.gcs_connected = gcs_now_connected
        if self._gcs_link_was_connected is not None and gcs_now_connected != self._gcs_link_was_connected:
            if gcs_now_connected:
                self.emit_log("[AUTO] GCS LINK RESTORED. Autonomous mission continued onboard; "
                              f"active waypoint {idx + 1}/{len(wps)}.")
            else:
                self.emit_log("[AUTO] GCS TELEMETRY LOST. Continuing already-started mission "
                              f"onboard toward waypoint {idx + 1}/{len(wps)}; "
                              "STOP commands cannot be received until link returns.")
                if not self._save_progress("RUNNING", "GCS_TELEMETRY_LOST"):
                    self._pause("CHECKPOINT_IO_FAILURE")
                    return
        self._gcs_link_was_connected = gcs_now_connected
        # Initial AUTO engagement still requires a received command. After
        # explicit engagement, telemetry loss must not reset the route/index.
        if not CONTINUE_ON_GCS_LOSS and not gcs_now_connected:
            self._pause("GCS_TIMEOUT")
            return
        if not last_command_rx:
            self._pause("NO_INITIAL_GCS_COMMAND")
            return
        if last_ahrs_rx == 0.0 or now - last_ahrs_rx > NAV_SENSOR_TIMEOUT or not math.isfinite(current_heading):
            self._pause("AHRS_STALE")
            return

        gps_fresh = bool(gps_valid and valid_coords(current_coords) and
                         last_gps_rx > 0.0 and now - last_gps_rx <= NAV_SENSOR_TIMEOUT)
        gps_degraded = not gps_fresh
        if self._gps_bridge_active and gps_fresh and gps_satellites >= MIN_RESUME_GPS_SATS:
            # A single good packet is insufficient because the reported issue
            # is the GPS toggling FIX / NO_FIX.  Require continuous good data.
            if self._gps_fix_returned_since is None:
                self._gps_fix_returned_since = now
            if now - self._gps_fix_returned_since < GPS_REACQUIRE_STABLE_S:
                gps_degraded = True
            else:
                err_m, _ = haversine_and_bearing(self._gps_bridge_coords, current_coords)
                if err_m > AUTO_RESUME_MAX_DRIFT_M:
                    self._pause("GPS_REACQUIRE_POSITION_JUMP")
                    return
                self._reset_gps_bridge()
                self.pid.reset()
                self._reset_stabilization_memory()
                self._progress_window.clear()
                self._progress_wp_index = None
                self.emit_log(f"[AUTO] GPS FIX RESTORED (estimate difference {err_m:.1f}m). "
                              f"Resuming live GPS guidance at WP {idx + 1}/{len(wps)}.")
        elif not gps_fresh or gps_satellites < MIN_RESUME_GPS_SATS:
            self._gps_fix_returned_since = None
            if self._gps_bridge_active:
                gps_degraded = True

        if gps_degraded:
            if self.state.gps_docking_active:
                # Close-range docking cannot use coarse dead reckoning.
                self._pause("GPS_REQUIRED_FOR_DOCKING")
                return
            start_fix_t = (self._gps_bridge_start_fix_t if self._gps_bridge_active
                           else last_gps_rx)
            estimated_coords, bridge_error = self._gps_bridge_step(
                current_coords, current_heading, start_fix_t,
                last_good_speed_kmph, now
            )
            if bridge_error:
                self._pause(bridge_error)
                return
            current_coords = estimated_coords
        else:
            self._gps_navigation_established = True

        target_coords = wps[idx]
        if not valid_coords(target_coords):
            self._abort("INVALID_WAYPOINT")
            return

        # Track approach independently of 20 Hz controller ticks; a vessel
        # can physically pass an intermediate GPS waypoint outside a 1 m circle.
        route_key = tuple(wps)
        if self._capture_wp_index != idx or getattr(self, "_capture_route", None) != route_key:
            # New leg OR new route (e.g. RTH replaced the route mid-mission).
            self._capture_route = route_key
            self._capture_wp_index = idx
            self._capture_leg_start = wps[idx - 1] if idx > 0 else current_coords
            self._closest_wp_distance = float("inf")
        current_wp_distance, _ = haversine_and_bearing(current_coords, target_coords)
        if gps_degraded and current_wp_distance <= GPS_BRIDGE_MIN_WAYPOINT_M:
            # Never perform an arrival/corner/dock transition based only on
            # inferred position, and do not drive blind into a close target.
            self._pause("GPS_BRIDGE_NEAR_WAYPOINT")
            return
        if not gps_degraded:
            self._closest_wp_distance = min(self._closest_wp_distance, current_wp_distance)

        # Pure Pursuit guidance: steer toward a point 2 m ahead on the
        # remaining route, while keeping distance/surge control referenced to
        # the real active waypoint.
        active_lookahead = DOCK_LOOKAHEAD_M if self.state.gps_docking_active else PURE_PURSUIT_LOOKAHEAD_M
        guidance_target, cross_track_m = pure_pursuit_target_leg(
            current=current_coords,
            leg_start=self._capture_leg_start,
            remaining_waypoints=wps[idx:],
            lookahead_m=active_lookahead,
        )
        self._cross_track_m = cross_track_m

        with self.state.lock:
            active_stab = bool(self.state.active_stabilization)
        stab_correction_deg = 0.0
        if active_stab:
            # Follow the CURRENT fixed leg exactly instead of cutting the corner
            # into the next waypoint. Integral crab compensation rejects current.
            guidance_target, stab_correction_deg, _ = self._active_stab_guidance(
                current_coords=current_coords,
                leg_start=self._capture_leg_start,
                target_coords=target_coords,
                cross_track_m=cross_track_m,
                gps_stamp=0.0 if gps_degraded else last_gps_rx,
                lookahead_m=active_lookahead,
            )
        else:
            self._reset_stabilization_memory()

        is_final_waypoint = (idx == len(wps) - 1)
        arrival_threshold = PURE_PURSUIT_WAYPOINT_CAPTURE_M

        lin_u_t, ang_u_t, dist, bearing, direction = self.pid.compute_step(
            current_coords=current_coords,
            target_coords=target_coords,
            current_heading_deg=current_heading,
            limits=self.limits,
            guidance_coords=guidance_target,
            arrival_threshold=arrival_threshold,
        )

        # Compare distance only across distinct GPS fixes, not every 20 Hz
        # controller tick. Abort on a sustained increase rather than blindly
        # increasing thrust when the vehicle is moving away.
        if self._progress_wp_index != idx:
            self._progress_window.clear()
            self._progress_wp_index = idx
        if gps_degraded or direction != "FORWARD":
            self._progress_window.clear()
        elif not self._progress_window or last_gps_rx != self._progress_window[-1][2]:
            self._progress_window.append((now, dist, last_gps_rx))
        while self._progress_window and now - self._progress_window[0][0] > NAV_AWAY_WINDOW_S:
            self._progress_window.popleft()
        if (direction == "FORWARD" and len(self._progress_window) >= 2
                and now - self._progress_window[0][0] >= NAV_AWAY_WINDOW_S * 0.75
                and dist - self._progress_window[0][1] >= NAV_AWAY_THRESHOLD_M):
            self._abort("MOVING_AWAY_FROM_WAYPOINT")
            return

        with self.state.lock:
            self.state.distance_to_target = dist
            self.state.target_bearing = bearing
            self.state.heading_error = shortest_angle_error(current_heading, bearing)
            self.state.cross_track_m = cross_track_m
            self.state.stabilization_correction_deg = stab_correction_deg
            self.state.stabilization_hold_distance_m = 0.0
            if active_stab:
                self.state.stabilization_mode = "GPS_BRIDGE" if gps_degraded else "PATH"
                self.state.nav_direction = (
                    f"GPS_BRIDGE_{direction}" if gps_degraded else
                    f"STAB_{direction}" if direction not in ("ARRIVED", "COMPLETED") else direction
                )
            else:
                self.state.stabilization_mode = "OFF"
                self.state.nav_direction = (
                    f"GPS_BRIDGE_{direction}" if gps_degraded else
                    f"PP_{direction}" if direction not in ("ARRIVED", "COMPLETED") else direction
                )

        captured = (not gps_degraded) and (self.pid.target_reached or direction == "ARRIVED")
        capture_reason = "FINAL_RADIUS" if is_final_waypoint else "RADIUS"
        if not gps_degraded and not is_final_waypoint and not captured:
            captured, capture_reason = intermediate_waypoint_captured(
                current_coords, target_coords, self._capture_leg_start,
                self._closest_wp_distance, arrival_threshold,
                PURE_PURSUIT_PASS_CORRIDOR_M,
            )
        if captured:
            # Intermediate Pure Pursuit waypoints are handed off without
            # stopping the vehicle.  Only the final waypoint commands neutral
            # thrust.  This prevents stop-turn-go behaviour at every corner.
            if not is_final_waypoint:
                # Write the NEXT target to durable storage before committing
                # the live waypoint transition (survives a crash at the corner).
                if self.checkpoint and not self.checkpoint.save(wps, idx + 1, "RUNNING", current_coords, "WAYPOINT_CAPTURED"):
                    self._pause("CHECKPOINT_IO_FAILURE")
                    return
                with self.state.lock:
                    if (self.state.operating_mode != 1 or self.state.start_run_mode != 1
                            or not self.state.is_armed or self.state.current_wp_index != idx):
                        return
                    self.state.current_wp_index += 1
                    self.state.nav_direction = "PURE_PURSUIT_NEXT"
                    if self.state.gps_docking_active and self.state.current_wp_index == 1:
                        self.state.dock_stage = "FINAL_APPROACH"
                        self.state.dock_guideline = "Approach waypoint reached. Navigating to the docking station."
                self._save_progress("RUNNING", "WAYPOINT_CAPTURED")
                self.pid.reset()
                self._progress_window.clear()
                self._progress_wp_index = None
                self.emit_log(
                    f"[AUTO] Pure Pursuit captured Waypoint {idx + 1}/{len(wps)} "
                    f"by {capture_reason} (GPS distance {dist:.1f} m); continuing to WP {idx + 2}."
                )
                return

            if self.checkpoint and not self.checkpoint.save(wps, idx + 1, "COMPLETED", current_coords, "FINAL_WAYPOINT_CAPTURED"):
                self._pause("CHECKPOINT_IO_FAILURE")
                return
            with self.state.lock:
                if (self.state.operating_mode != 1 or self.state.start_run_mode != 1
                        or not self.state.is_armed or self.state.current_wp_index != idx):
                    return
                self.state.current_wp_index += 1
                if self.state.gps_docking_active:
                    self.state.dock_stage = "DOCKED"
                self.state.t1 = NEUTRAL_PWM
                self.state.t2 = NEUTRAL_PWM
                self.state.t3 = NEUTRAL_PWM
                sent = self.teensy.send_command(NEUTRAL_PWM, NEUTRAL_PWM, NEUTRAL_PWM, self.state.light, self.state.camera)
            self.pid.reset()
            self._reset_pwm_slew()
            if not sent:
                self._pause("TEENSY_COMMAND_FAILED")
                return
            self.emit_log(f"[AUTO] Arrived at final Waypoint {idx + 1}/{len(wps)}: ({target_coords[0]:.7f}, {target_coords[1]:.7f})")
            time.sleep(0.2)
            return

        # Calculate final PWM outputs.  T1 now commands physical NOSE-first
        # travel.  During TURN, compute_step() returns zero surge, so T1 remains
        # at neutral while T2 acquires the waypoint heading.
        if dist <= NAV_APPROACH_DISTANCE_M:
            lin_u_t = max(-NAV_APPROACH_MAX_PWM, min(lin_u_t, NAV_APPROACH_MAX_PWM))

        if gps_degraded:
            # Do not carry full-speed commands into GPS-denied water.
            lin_u_t = max(0.0, min(lin_u_t, GPS_BRIDGE_MAX_SURGE_PWM))
            ang_u_t = max(-GPS_BRIDGE_MAX_YAW_PWM,
                          min(ang_u_t, GPS_BRIDGE_MAX_YAW_PWM))

        # GPS docking final approach: half normal surge by default, then taper
        # continuously inside the configured slowdown distance. Steering keeps
        # full authority so the vehicle prioritizes staying on the dock axis.
        if self.state.gps_docking_active and direction == "FORWARD":
            speed_scale = DOCK_SPEED_SCALE
            if is_final_waypoint and DOCK_SLOWDOWN_DISTANCE_M > 0 and dist < DOCK_SLOWDOWN_DISTANCE_M:
                frac = max(0.0, min(1.0, dist / DOCK_SLOWDOWN_DISTANCE_M))
                speed_scale = DOCK_MIN_SPEED_SCALE + (DOCK_SPEED_SCALE - DOCK_MIN_SPEED_SCALE) * frac
            lin_u_t *= speed_scale
            if 0.0 < lin_u_t < DOCK_MIN_SURGE_PWM and dist > arrival_threshold:
                lin_u_t = float(DOCK_MIN_SURGE_PWM)

        fwd_pwm = int(DEFAULT_BASE_SPEED + lin_u_t)
        lat_pwm = int(DEFAULT_BASE_SPEED + ang_u_t)
        # T1 = forward linear travel, T2 = yaw correction, T3 = pitch.
        # GPS waypoints provide no pitch target, so hold T3 at neutral.
        vert_pwm = NEUTRAL_PWM

        fwd_pwm = self._slew_pwm(self._last_fwd_pwm, fwd_pwm, AUTONAV_T1_SLEW_PWM)
        lat_pwm = self._slew_pwm(self._last_lat_pwm, lat_pwm, AUTONAV_T2_SLEW_PWM)
        self._last_fwd_pwm = fwd_pwm
        self._last_lat_pwm = lat_pwm

        with self.state.lock:
            # A STOP or DISARM received while computing must win.
            if (self.state.operating_mode != 1 or self.state.start_run_mode != 1
                    or not self.state.is_armed or self.state.current_wp_index != idx):
                return
            self.state.t1 = fwd_pwm
            self.state.t2 = lat_pwm
            self.state.t3 = vert_pwm
            sent = self.teensy.send_command(
                fwd=fwd_pwm, lat=lat_pwm, vert=vert_pwm,
                light=self.state.light, camera=self.state.camera
            )
        if not sent:
            self._pause("TEENSY_COMMAND_FAILED")
            return

        if now - self._last_xte_log >= 1.0:
            self._last_xte_log = now
            self.state.log_incoming("NAV_TRACK", {
                "wp": idx + 1, "dist_m": round(dist, 2), "xte_m": round(self._cross_track_m, 2),
                "heading": round(current_heading, 1), "bearing": round(bearing, 1),
                "stab": int(active_stab), "crab_corr_deg": round(stab_correction_deg, 2),
                "t1": fwd_pwm, "t2": lat_pwm, "mode": direction})

        if now - self._last_checkpoint_time >= MISSION_CHECKPOINT_PERIOD_S:
            self._last_checkpoint_time = now
            if not self._save_progress("RUNNING"):
                self._pause("CHECKPOINT_IO_FAILURE")
        time.sleep(0.05)  # 20 Hz control rate


# =============================================================================
# MAGNETIC HOMING & DOCKING GUIDANCE ENGINE (from mag_homing.py)
# =============================================================================

# Bearing convention from the logs: swept arc runs 180 deg -> 360 deg,
# boresight / centre of the arc sits at 270 deg (or 180.0 deg with offset).
CENTER_DEG = 180.0
DEADBAND_DEG = 20.0     # +/- band treated as straight ahead
MAX_STEER_DEG = 30.0    # docking steering command is limited to +/-30 degrees
STEER_STEP_DEG = 5.0    # only emit ... -10, -5, 0, +5, +10 ... degrees

LOCK_UT = 5.0           # anomaly magnitude to declare a lock
DROP_UT = 3.5           # lose lock below this
CLOSE_FIELD_UT = 15.0   # strong 3-D field with poor bearing => close to dock
CLOSE_HOLD_S = 2.0      # max time a stale steering command may be held
SLEW_DEG_S = 25.0       # max rate of change of the steering command
ASTERN_GUARD_DEG = 25.0 # +/-180 wrap guard: commit instead of flip-flopping

CAL_MEASURED = [175.5, 184.3, 205.4, 262.4, 325.3, 334.7, 356.8]
CAL_TRUE     = [180.0, 210.0, 240.0, 270.0, 300.0, 330.0, 360.0]
LINEARISE = True

BEARING_OFFSET_DEG = -109.0
RANGE_OFFSET_M = -0.3

DOCK_PWM_SPAN = 35      # full authority is 1500 +/- 35
T3_IDLE = 1500
TURN_FIRST_DEG = 20.0   # inside the band we go straight
STOP_RANGE_M = 0.15     # hold station once this close to emitter

RANGE_EXP = 3.67
CAL_RANGE_BEARING = [180.0, 210.0, 240.0, 270.0, 300.0, 330.0, 360.0]
CAL_RANGE_K       = [63.1,  88.8,  99.3,  58.8, 135.8, 187.3, 406.4]
RANGE_MIN_M = 0.3
RANGE_MAX_M = 3.0

MIRROR_STEERING = False
SMOOTH_N = 10           # circular moving-average window on heading
MIN_QUALITY = 0.85      # heading consistency needed before command emitted
BASELINE_N = 100        # samples used for startup Earth-field calibration
BASELINE_QUIET_UT = 0.6 # max per-axis std for calibration window
EARTH_MIN_UT = 25.0     # plausible |B| window for Earth field
EARTH_MAX_UT = 65.0
BASELINE_TAU = 0.002    # slow baseline tracking rate while unlocked


def linearise_mag(measured_deg: float) -> float:
    """Map a raw anomaly angle onto true bearing using the log-derived curve."""
    if not LINEARISE:
        return measured_deg % 360.0
    m = measured_deg % 360.0
    if m < CAL_MEASURED[0] - 90.0:
        m += 360.0
    xs, ys = CAL_MEASURED, CAL_TRUE
    if m <= xs[0]:
        k = (ys[1] - ys[0]) / (xs[1] - xs[0])
        return ys[0] + k * (m - xs[0])
    if m >= xs[-1]:
        k = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
        return ys[-1] + k * (m - xs[-1])
    for i in range(len(xs) - 1):
        if xs[i] <= m <= xs[i + 1]:
            f = (m - xs[i]) / (xs[i + 1] - xs[i])
            return ys[i] + f * (ys[i + 1] - ys[i])
    return m


def estimate_dock_range(bearing_deg: float, mag_ut: float) -> Tuple[Optional[float], str]:
    """Estimate dock range in metres from the 3-D anomaly magnitude."""
    if mag_ut <= 0.0:
        return None, ""
    b = bearing_deg % 360.0
    if b < CAL_RANGE_BEARING[0]:
        b += 360.0
    xs, ys = CAL_RANGE_BEARING, CAL_RANGE_K
    if b <= xs[0]:
        k = ys[0]
    elif b >= xs[-1]:
        k = ys[-1]
    else:
        k = ys[-1]
        for i in range(len(xs) - 1):
            if xs[i] <= b <= xs[i + 1]:
                f = (b - xs[i]) / (xs[i + 1] - xs[i])
                k = ys[i] + f * (ys[i + 1] - ys[i])
                break
    r = (k / mag_ut) ** (1.0 / RANGE_EXP) + RANGE_OFFSET_M
    if r <= 0.0:
        return 0.0, "<"
    if r < RANGE_MIN_M:
        return r, "<"
    if r > RANGE_MAX_M:
        return r, ">"
    return r, ""


def mix_docking_thrusters(cmd: str, err: Optional[float], steering: float, rng: Optional[float],
                          light_val: int = 0, camera_val: int = 0) -> Tuple[int, int, int, int, int, str]:
    """Map the docking guidance state onto (t1, t2, t3, light, camera, axis)."""
    t1 = t2 = NEUTRAL_PWM

    if cmd in ("NULL", "CLOSE") or err is None:
        return t1, t2, T3_IDLE, light_val, camera_val, "stall"

    if rng is not None and rng <= STOP_RANGE_M:
        return t1, t2, T3_IDLE, light_val, camera_val, "hold"

    if abs(err) > TURN_FIRST_DEG:
        sense = -err if MIRROR_STEERING else err
        frac = min(1.0, abs(err) / MAX_STEER_DEG) if MAX_STEER_DEG else 1.0
        delta = max(1, int(round(DOCK_PWM_SPAN * frac)))
        t2 = NEUTRAL_PWM + (delta if sense > 0 else -delta)
        axis = "T2 yaw"
    else:
        t1 = NEUTRAL_PWM + DOCK_PWM_SPAN
        axis = "T1 surge"

    t1 = max(NEUTRAL_PWM - DOCK_PWM_SPAN, min(NEUTRAL_PWM + DOCK_PWM_SPAN, t1))
    t2 = max(NEUTRAL_PWM - DOCK_PWM_SPAN, min(NEUTRAL_PWM + DOCK_PWM_SPAN, t2))
    return t1, t2, T3_IDLE, light_val, camera_val, axis


def wrap180(a: float) -> float:
    """Wrap an angle to (-180, +180]."""
    return (a + 180.0) % 360.0 - 180.0


class CircularMean:
    """Moving average over angles on unit circle."""
    def __init__(self, n: int = SMOOTH_N):
        self.sx = deque(maxlen=n)
        self.sy = deque(maxlen=n)

    def push(self, deg: float) -> None:
        r = math.radians(deg)
        self.sx.append(math.cos(r))
        self.sy.append(math.sin(r))

    def value(self) -> Optional[float]:
        if not self.sx:
            return None
        x = sum(self.sx) / len(self.sx)
        y = sum(self.sy) / len(self.sy)
        if math.hypot(x, y) < 1e-9:
            return None
        return math.degrees(math.atan2(y, x)) % 360.0

    def strength(self) -> float:
        if not self.sx:
            return 0.0
        x = sum(self.sx) / len(self.sx)
        y = sum(self.sy) / len(self.sy)
        return math.hypot(x, y)

    def axial_strength(self) -> float:
        if not self.sx:
            return 0.0
        ang = [2.0 * math.atan2(y, x) for x, y in zip(self.sx, self.sy)]
        cx = sum(math.cos(a) for a in ang) / len(ang)
        cy = sum(math.sin(a) for a in ang) / len(ang)
        return math.hypot(cx, cy)

    def clear(self) -> None:
        self.sx.clear()
        self.sy.clear()


class EarthBaseline:
    """Earth-field reference tracking."""
    def __init__(self, n: int = BASELINE_N, tau: float = BASELINE_TAU):
        self.n = n
        self.tau = tau
        self.buf = deque(maxlen=n)
        self.bx: Optional[float] = None
        self.by: Optional[float] = None
        self.bz: Optional[float] = None
        self.rejects = 0
        self.last_reject_reason = ""

    @property
    def ready(self) -> bool:
        return self.bx is not None

    @property
    def progress(self) -> int:
        if self.ready:
            return 100
        return min(99, int((len(self.buf) / float(self.n)) * 100))

    def reset(self) -> None:
        self.buf.clear()
        self.bx = None
        self.by = None
        self.bz = None
        self.rejects = 0
        self.last_reject_reason = ""

    def force(self, x: float, y: float, z: float) -> None:
        self.bx, self.by, self.bz = x, y, z

    def feed_cal(self, x: float, y: float, z: float) -> bool:
        self.buf.append((x, y, z))
        if len(self.buf) < self.n:
            return False
        k = len(self.buf)
        mx = sum(v[0] for v in self.buf) / k
        my = sum(v[1] for v in self.buf) / k
        mz = sum(v[2] for v in self.buf) / k
        sd = max(
            math.sqrt(sum((v[i] - m) ** 2 for v in self.buf) / k)
            for i, m in ((0, mx), (1, my), (2, mz))
        )
        tot = math.sqrt(mx * mx + my * my + mz * mz)
        if sd > BASELINE_QUIET_UT or not (EARTH_MIN_UT <= tot <= EARTH_MAX_UT):
            if sd > BASELINE_QUIET_UT:
                self.last_reject_reason = f"Window rejected: spread {sd:.2f} > {BASELINE_QUIET_UT} uT. KEEP COIL OFF & VEHICLE STILL!"
            else:
                self.last_reject_reason = f"Window rejected: field {tot:.1f} uT outside [{EARTH_MIN_UT}, {EARTH_MAX_UT}] uT. KEEP COIL OFF!"
            for _ in range(self.n // 2):
                if self.buf:
                    self.buf.popleft()
            self.rejects += 1
            return False
        self.bx, self.by, self.bz = mx, my, mz
        self.buf.clear()
        self.last_reject_reason = ""
        return True

    def track(self, x: float, y: float, z: float) -> None:
        if self.tau <= 0 or not self.ready:
            return
        a = self.tau
        self.bx += a * (x - self.bx)
        self.by += a * (y - self.by)
        self.bz += a * (z - self.bz)

    def anomaly(self, x: float, y: float, z: float) -> Tuple[float, float, float]:
        return (x - self.bx, y - self.by, z - self.bz)


def classify_dock_heading(heading_deg: float) -> Tuple[str, float, float]:
    err = wrap180(linearise_mag(heading_deg) + BEARING_OFFSET_DEG - CENTER_DEG)
    if abs(err) <= DEADBAND_DEG:
        return "FORWARD", err, 0.0
    steering = -err if MIRROR_STEERING else err
    steering = max(-MAX_STEER_DEG, min(MAX_STEER_DEG, steering))
    steering = round(steering / STEER_STEP_DEG) * STEER_STEP_DEG
    if steering == 0.0:
        steering = math.copysign(STEER_STEP_DEG, -err if MIRROR_STEERING else err)
    command = "LEFT" if steering < 0 else "RIGHT"
    return command, err, steering


def is_dock_astern(err: float) -> bool:
    return abs(err) > 180.0 - ASTERN_GUARD_DEG


class MagneticDockingEngine(threading.Thread):
    """
    Autonomous magnetic homing & terminal docking guidance thread (from mag_homing.py).
    Controls T1 (Surge) and T2 (Yaw) to align with and dock into the magnetic beacon coil.
    """
    def __init__(self, state: VehicleState, teensy: TeensyLink, stop: threading.Event,
                 surface_emit_log: Callable[[str], None],
                 rate_hz: float = 10.0):
        super().__init__(daemon=True, name="MagDockingEngine")
        self.state = state
        self.teensy = teensy
        self.stop = stop
        self.emit_log = surface_emit_log
        self.period = 1.0 / max(1.0, min(50.0, rate_hz))
        self.base = EarthBaseline(BASELINE_N, BASELINE_TAU)
        self.smooth = CircularMean(SMOOTH_N)
        self.locked = False
        self.last_good_heading: Optional[float] = None
        self.last_good_err: float = 0.0
        self.last_good_steering: float = 0.0
        self.last_good_t: float = 0.0
        self.astern_sign: float = 0.0
        self.out_steer: float = 0.0
        self.prev_t: float = time.monotonic()
        self.last_log_time: float = 0.0
        self.was_docking: bool = False

    def reset_for_docking(self) -> None:
        """Reset calibration and state for a fresh docking engagement."""
        self.base.reset()
        self.locked = False
        self.smooth.clear()
        self.out_steer = 0.0
        self.astern_sign = 0.0
        self.last_good_heading = None
        self.last_good_err = 0.0
        self.last_good_steering = 0.0
        with self.state.lock:
            self.state.dock_base_ready = False
            self.state.dock_cal_progress = 0
            self.state.dock_stage = "CALIBRATING_COIL_OFF"
            self.state.dock_guideline = "PHASE 1: KEEP BEACON COIL OFF. Capturing Earth magnetic field baseline (100 samples)..."
            self.state.dock_cmd = "CALIBRATING"
            self.state.dock_axis = "stall"
            self.state.dock_range = None
            self.state.dock_mag = None

    def run(self) -> None:
        print("[DOCKING] Magnetic docking guidance engine active (10 Hz loop)")
        while not self.stop.is_set():
            if self.state.is_docking and not self.state.gps_docking_active:
                if not self.was_docking:
                    self.was_docking = True
                    self.reset_for_docking()
                    self.emit_log("[DOCK] Autonomous magnetic docking ENGAGED. Phase 1: Keep beacon coil OFF for baseline capture.")
                self._step_docking()
            else:
                if self.was_docking:
                    self.was_docking = False
                    self.locked = False
                    self.smooth.clear()
                    self.out_steer = 0.0
                    with self.state.lock:
                        self.state.dock_stage = "STANDBY"
                        self.state.dock_guideline = "GPS docking standby. Set the approach and dock waypoints before engaging."
                        self.state.dock_cmd = "NULL"
                        self.state.dock_axis = "stall"
                self.prev_t = time.monotonic()
            time.sleep(self.period)

    def _step_docking(self) -> None:
        with self.state.lock:
            mx, my, mz = self.state.magX, self.state.magY, self.state.magZ

        if mx is None or my is None or mz is None:
            return

        now = time.monotonic()

        # Step 1: Baseline Calibration (COIL MUST BE OFF)
        if not self.base.ready:
            cal_done = self.base.feed_cal(mx, my, mz)
            with self.state.lock:
                self.state.dock_base_ready = self.base.ready
                self.state.dock_cal_progress = self.base.progress
                if self.base.last_reject_reason:
                    self.state.dock_guideline = f"PHASE 1 WARNING: {self.base.last_reject_reason}"
                else:
                    self.state.dock_guideline = f"PHASE 1: KEEP BEACON COIL OFF! Capturing Earth baseline ({self.base.progress}%)..."
                self.state.dock_stage = "CALIBRATING_COIL_OFF"
                self.state.t1 = NEUTRAL_PWM
                self.state.t2 = NEUTRAL_PWM
                self.state.t3 = NEUTRAL_PWM

            if cal_done:
                cal_msg = f"[DOCK] Earth baseline captured: X={self.base.bx:.2f} Y={self.base.by:.2f} Z={self.base.bz:.2f} uT. TURN ON BEACON COIL NOW."
                print(cal_msg)
                self.emit_log(cal_msg)
                with self.state.lock:
                    self.state.dock_stage = "BASELINE_READY_COIL_ON"
                    self.state.dock_guideline = "PHASE 2: EARTH BASELINE CAPTURED! TURN ON / ENERGIZE BEACON COIL NOW TO LOCK."

            # Neutral thrusters during calibration
            self.teensy.send_command(NEUTRAL_PWM, NEUTRAL_PWM, NEUTRAL_PWM, self.state.light, self.state.camera)
            return

        # Step 2: Anomaly vector
        dx, dy, dz = self.base.anomaly(mx, my, mz)
        mag = math.sqrt(dx * dx + dy * dy + dz * dz)
        hmag = math.hypot(dx, dy)

        close_field = (mag >= CLOSE_FIELD_UT and hmag < DROP_UT)

        if not self.locked and hmag >= LOCK_UT:
            self.locked = True
            self.smooth.clear()
            self.emit_log(f"[DOCK] Magnetic beacon LOCKED! |dBh|={hmag:.1f}uT. Autonomous homing active.")
        elif self.locked and hmag < DROP_UT and not close_field:
            self.locked = False
            self.smooth.clear()
            self.emit_log("[DOCK] Lock lost: magnetic field dropped below threshold.")

        # Phase 2 check: if not locked and not close field, we are waiting for beacon coil
        if not self.locked and not close_field:
            self.base.track(mx, my, mz)
            with self.state.lock:
                self.state.dock_stage = "BASELINE_READY_COIL_ON"
                self.state.dock_guideline = f"PHASE 2: TURN ON / ENERGIZE BEACON COIL TO LOCK (|dBh|={hmag:.1f} < {LOCK_UT:.1f} uT)..."
                self.state.dock_cmd = "WAIT_COIL"
                self.state.dock_axis = "stall"
                self.state.dock_mag = hmag
                self.state.dock_range = None
                self.state.t1 = NEUTRAL_PWM
                self.state.t2 = NEUTRAL_PWM
                self.state.t3 = NEUTRAL_PWM
            self.teensy.send_command(NEUTRAL_PWM, NEUTRAL_PWM, NEUTRAL_PWM, self.state.light, self.state.camera)
            return

        # Phase 3: Autonomous Homing & Terminal Docking
        inst = math.degrees(math.atan2(dy, dx)) % 360.0
        heading = None
        if self.locked and hmag >= DROP_UT:
            self.smooth.push(inst)
            heading = self.smooth.value()

        quality = self.smooth.strength()
        stale = (now - self.last_good_t) > CLOSE_HOLD_S

        if close_field or (mag >= CLOSE_FIELD_UT and quality < MIN_QUALITY):
            cmd = "CLOSE"
            heading = self.last_good_heading if self.last_good_heading is not None else inst
            if stale or self.last_good_heading is None:
                err, steering = 0.0, 0.0
                reason = "close/expired"
            else:
                err = self.last_good_err
                steering = self.last_good_steering
                reason = "strong/close"
        elif not self.locked:
            cmd, err, steering = "NULL", 0.0, 0.0
            reason = "vertical" if mag >= LOCK_UT else "no field"
        elif heading is None or quality < MIN_QUALITY:
            cmd, err, steering = "NULL", 0.0, 0.0
            reason = "flip 180" if self.smooth.axial_strength() > 0.80 else "unstable"
        else:
            cmd, err, steering = classify_dock_heading(heading)
            if is_dock_astern(err):
                if self.astern_sign == 0.0:
                    self.astern_sign = math.copysign(1.0, steering or 1.0)
                steering = self.astern_sign * MAX_STEER_DEG
                cmd = "LEFT" if steering < 0 else "RIGHT"
                reason = "astern"
            else:
                self.astern_sign = 0.0
                reason = ""
            self.last_good_heading = heading
            self.last_good_err = err
            self.last_good_steering = steering
            self.last_good_t = now

        # Slew rate limit steering
        dt = max(1e-3, now - self.prev_t)
        self.prev_t = now
        max_step = SLEW_DEG_S * dt
        self.out_steer += max(-max_step, min(max_step, steering - self.out_steer))
        steering = round(self.out_steer / STEER_STEP_DEG) * STEER_STEP_DEG

        if cmd == "NULL" or heading is None:
            rng, rng_flag = None, ""
        else:
            rng, rng_flag = estimate_dock_range(linearise_mag(heading), mag)

        t1, t2, t3, lt, cam, axis = mix_docking_thrusters(
            cmd, None if cmd in ("NULL", "CLOSE") else err, steering, rng,
            self.state.light, self.state.camera
        )

        is_docked = (axis == "hold") or (rng is not None and rng <= STOP_RANGE_M)
        if is_docked:
            stage_str = "DOCKED"
            guide_str = f"PHASE 3 COMPLETE: VEHICLE DOCKED (<{STOP_RANGE_M}m). Holding station."
        else:
            stage_str = "HOMING_LOCKED"
            if axis == "T2 yaw":
                guide_str = f"PHASE 3: YAW ALIGNMENT -> Turn {cmd} ({err:+.1f}°) to center beacon bearing."
            elif axis == "T1 surge":
                rng_txt = f"({rng:.2f}m)" if rng is not None else ""
                guide_str = f"PHASE 3: SURGE RUN-IN -> Forward drive {rng_txt} to dock cone."
            else:
                guide_str = f"PHASE 3: HOMING -> Cmd: {cmd} | Bearing: {heading:.1f}°" if heading is not None else "PHASE 3: HOMING ACTIVE."

        with self.state.lock:
            self.state.t1 = t1
            self.state.t2 = t2
            self.state.t3 = t3
            self.state.dock_cmd = cmd
            self.state.dock_axis = axis
            self.state.dock_heading = heading
            self.state.dock_err = err
            self.state.dock_steering = steering
            self.state.dock_range = rng
            self.state.dock_mag = hmag
            self.state.dock_quality = quality
            self.state.dock_stage = stage_str
            self.state.dock_guideline = guide_str

        # Write to Teensy
        self.teensy.send_command(t1, t2, t3, self.state.light, self.state.camera)

        # Periodic mission logs
        if now - self.last_log_time >= 2.0:
            self.last_log_time = now
            rng_str = f"{rng:.2f}m" if rng is not None else "--m"
            hdg_str = f"{heading:.1f}°" if heading is not None else "--°"
            if axis == "hold":
                self.emit_log(f"[DOCK] Arrived at dock station ({rng_str})! Holding station.")
            elif cmd != "NULL":
                self.emit_log(f"[DOCK] Cmd: {cmd} | Axis: {axis} | Rng: {rng_str} | Hdg: {hdg_str} | Err: {err:+.1f}°")



# =============================================================================
# RTSP CAMERA RECORDER (camera_rec.py ffmpeg loop)
# =============================================================================

DEFAULT_RTSP_URL = "rtsp://192.168.1.168:554/stream_1"

def get_default_recordings_dir() -> str:
    """
    Resolves the canonical video recordings folder on the backend.
    Checks local script directory and parent directory, placing recordings
    in a dedicated 'recordings' directory.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(script_dir)
    for candidate in [
        os.path.join(script_dir, "recordings"),
        os.path.join(parent_dir, "recordings"),
    ]:
        if os.path.isdir(candidate):
            return candidate

    if os.path.exists(os.path.join(parent_dir, "src")) or os.path.exists(os.path.join(parent_dir, "backend")):
        target = os.path.join(parent_dir, "recordings")
    else:
        target = os.path.join(script_dir, "recordings")
    os.makedirs(target, exist_ok=True)
    return target

class CameraRecorder:
    """
    Manages background video recording directly from the camera RTSP stream via ffmpeg.
    Replicates the proven logic from camera_rec.py on the Jetson tegra board:
    - Turns ON camera power via Teensy/state
    - Waits 15 seconds for camera startup & RTSP stream initialization
    - Queries next available recording{number}.mp4
    - Launches ffmpeg without re-encoding (-c:v copy -an)
    - Cleanly finalizes MP4 headers on stop
    """
    def __init__(self,
                 state: VehicleState,
                 output_dir: Optional[str] = None,
                 default_rtsp: str = DEFAULT_RTSP_URL,
                 teensy: Optional[TeensyLink] = None):
        self.state = state
        self.teensy = teensy
        self.default_rtsp = default_rtsp
        if not output_dir:
            self.output_dir = get_default_recordings_dir()
        else:
            self.output_dir = os.path.abspath(output_dir)
            os.makedirs(self.output_dir, exist_ok=True)
        print(f"[CAMERA] Video recordings directory: {self.output_dir}")
        self.process: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()
        self.warmup_thread: Optional[threading.Thread] = None
        self.cancel_warmup = threading.Event()
        self.is_warming_up = False

    def start_recording(self, rtsp_url: Optional[str] = None, emit_log: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
        with self.lock:
            # If already active and running
            if self.process and self.process.poll() is None:
                return False, f"Already recording: {os.path.basename(self.state.recording_file)}"

            if self.is_warming_up:
                return False, "Camera is already warming up (15s startup in progress)"

            url = rtsp_url.strip() if (rtsp_url and rtsp_url.strip()) else self.default_rtsp
            os.makedirs(self.output_dir, exist_ok=True)

            # 1. Turn ON camera power in state and Teensy
            with self.state.lock:
                self.state.camera = 1
            if self.teensy:
                self.teensy.send_command(self.state.t1, self.state.t2, self.state.t3, self.state.light, 1)

            # Find next available recording number matching user's camera_rec.py logic
            number = 1
            while os.path.exists(os.path.join(self.output_dir, f"recording{number}.mp4")):
                number += 1

            output_file = os.path.join(self.output_dir, f"recording{number}.mp4")

            self.cancel_warmup.clear()
            self.is_warming_up = True

            def _warmup_and_record_worker():
                warmup_msg = f"[CAM] Camera powered ON. Waiting 15s for camera bootup & RTSP stream ({url})..."
                print(warmup_msg)
                if emit_log:
                    emit_log(warmup_msg)

                # Wait 15 seconds initial boot time (can be interrupted by cancel_warmup)
                if self.cancel_warmup.wait(15.0):
                    print("[CAM] Camera recording warmup cancelled.")
                    with self.lock:
                        self.is_warming_up = False
                    return

                # Parse host and port to probe readiness
                try:
                    parsed_url = urlparse(url)
                    host = parsed_url.hostname or "192.168.1.168"
                    port = parsed_url.port or 554
                except Exception:
                    host = "192.168.1.168"
                    port = 554

                # Probe TCP port 554 for up to 15 additional seconds to guarantee camera RTSP server is active
                probe_start = time.monotonic()
                port_ready = False
                while time.monotonic() - probe_start < 15.0:
                    if self.cancel_warmup.is_set():
                        with self.lock:
                            self.is_warming_up = False
                        return
                    try:
                        with socket.create_connection((host, port), timeout=1.0):
                            port_ready = True
                            break
                    except Exception:
                        time.sleep(1.0)

                if port_ready:
                    ready_msg = f"[CAM] Camera RTSP port {host}:{port} is open and ready. Starting recording..."
                    print(ready_msg)
                    if emit_log:
                        emit_log(ready_msg)
                else:
                    warn_msg = f"[CAM WARN] RTSP port {host}:{port} not answering yet. Attempting ffmpeg anyway..."
                    print(warn_msg)
                    if emit_log:
                        emit_log(warn_msg)

                with self.lock:
                    if self.cancel_warmup.is_set():
                        self.is_warming_up = False
                        return

                    cmd = [
                        "ffmpeg",
                        "-y",
                        "-rtsp_transport", "tcp",
                        "-stimeout", "5000000",
                        "-i", url,
                        "-an",
                        "-c:v", "copy",
                        output_file
                    ]

                    try:
                        self.process = subprocess.Popen(
                            cmd,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE
                        )
                        with self.state.lock:
                            self.state.is_recording = True
                            self.state.recording_file = output_file
                        self.is_warming_up = False
                        success_msg = f"[CAM] 15s warmup complete. Recording started: {output_file} (PID {self.process.pid})"
                        print(success_msg)
                        if emit_log:
                            emit_log(success_msg)

                        # Background monitor thread for ffmpeg stderr
                        def _monitor_ffmpeg(proc=self.process, fn=output_file):
                            _, err_data = proc.communicate()
                            ret = proc.returncode
                            if ret != 0 and ret != 255:
                                err_str = err_data.decode("utf-8", errors="ignore") if err_data else ""
                                last_err = [l.strip() for l in err_str.splitlines() if l.strip()][-1] if err_str else f"Exit code {ret}"
                                warn_msg = f"[CAM ERROR] ffmpeg stream closed: {last_err}"
                                print(warn_msg)
                                if emit_log:
                                    emit_log(warn_msg)
                                with self.state.lock:
                                    if self.state.recording_file == fn:
                                        self.state.is_recording = False
                                        self.state.recording_file = ""

                        threading.Thread(target=_monitor_ffmpeg, daemon=True, name="FfmpegMonitor").start()

                    except Exception as e:
                        with self.state.lock:
                            self.state.is_recording = False
                            self.state.recording_file = ""
                        self.is_warming_up = False
                        err_msg = f"[CAM] Failed to launch ffmpeg: {e}"
                        print(err_msg)
                        if emit_log:
                            emit_log(err_msg)

            self.warmup_thread = threading.Thread(
                target=_warmup_and_record_worker,
                daemon=True,
                name="CameraWarmupThread"
            )
            self.warmup_thread.start()
            return True, "Camera powered ON. 15s warmup started."

    def stop_recording(self) -> Tuple[bool, str]:
        with self.lock:
            # If in warmup phase, cancel warmup
            if self.is_warming_up:
                self.cancel_warmup.set()
                self.is_warming_up = False
                with self.state.lock:
                    self.state.is_recording = False
                    self.state.recording_file = ""
                print("[CAM] Camera recording warmup cancelled.")
                return True, "Recording warmup cancelled"

            if not self.process or self.process.poll() is not None:
                with self.state.lock:
                    self.state.is_recording = False
                    saved = self.state.recording_file
                    self.state.recording_file = ""
                self.process = None
                return False, "Not recording"

            saved_file = self.state.recording_file
            print(f"[CAMERA] Stopping recording: {saved_file}...")
            try:
                # Send 'q' to ffmpeg stdin for clean MP4 atom finalization
                if self.process.stdin:
                    try:
                        self.process.stdin.write(b"q\n")
                        self.process.stdin.flush()
                    except Exception:
                        pass

                try:
                    self.process.wait(timeout=2.5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
            except Exception as e:
                print(f"[CAMERA] Error while stopping ffmpeg: {e}")
                try:
                    self.process.kill()
                except Exception:
                    pass

            self.process = None
            with self.state.lock:
                self.state.is_recording = False
                self.state.recording_file = ""
            print(f"[CAMERA] Recording saved: {saved_file}")
            return True, saved_file


# =============================================================================
# GROUND STATION UI COMMUNICATION & PROTOCOL HANDLER
# =============================================================================

class SurfaceLink:
    """
    Unified Ground Station Link supporting Serial (P8 / RF link), TCP socket, and UDP.
    Accepts all commands emitted by src/app.py and transmits JSON telemetry & event logs.
    """
    def __init__(self,
                 state: VehicleState,
                 teensy: TeensyLink,
                 stop: threading.Event,
                 recorder: Optional[CameraRecorder] = None,
                 sonar: Optional[Ping360Link] = None,
                 port: str = DEFAULT_RF_PORT,
                 baud: int = DEFAULT_RF_BAUD,
                 telemetry_hz: float = 4.0,
                 tcp_port: int = DEFAULT_TCP_PORT,
                 telemetry_format: str = "json",
                 checkpoint: Optional[MissionCheckpoint] = None):
        self.checkpoint = checkpoint
        self.state = state
        self.teensy = teensy
        self.stop = stop
        self.recorder = recorder or CameraRecorder(state, teensy=teensy)
        self.sonar = sonar
        self.port = port
        self.baud = baud
        self.period = 1.0 / max(1.0, min(20.0, telemetry_hz))
        self.tcp_port = tcp_port
        self.telemetry_format = telemetry_format.lower()
        self.log_queue: List[str] = []
        self.tx_lock = threading.Lock()
        self.active_clients = []

    def queue_log(self, message: str) -> None:
        """Queue mission log message to be emitted to GCS."""
        clean = message.strip()
        print(f"[MISSION LOG] {clean}")
        with self.tx_lock:
            self.log_queue.append(clean)

    def _manual_override_stabilization(self, emit_reply: callable) -> bool:
        """Disable station/path stabilization before accepting non-neutral manual thrust."""
        with self.state.lock:
            if not self.state.active_stabilization:
                return False
            self.state.active_stabilization = False
            self.state.stabilization_mode = "OFF"
            self.state.stabilization_hold_coords = None
            self.state.stabilization_hold_heading = None
            self.state.cross_track_m = 0.0
            self.state.stabilization_correction_deg = 0.0
            self.state.stabilization_hold_distance_m = 0.0
        self.queue_log("[STAB] Active stabilization disabled by manual thruster override.")
        emit_reply(b"ACK,STABILIZE,OFF,MANUAL_OVERRIDE\n")
        return True

    def _station_hold_owns_thrusters(self) -> bool:
        with self.state.lock:
            return bool(
                self.state.active_stabilization and self.state.is_armed
                and self.state.start_run_mode == 0 and not self.state.is_docking
            )

    def handle_command_line(self, line: str, emit_reply: callable) -> None:
        """
        Parses all incoming command dialects from the Ground Station UI:
        1. "$WP,idx,lat,lon" and "$WP,END" (Route Upload)
        2. "$ARM" / "$DISARM" (Vehicle Arming)
        3. "$START" / "$STOP" / "$RTH" (Mission Navigation Execution)
        4. "CMD,p1,p2,p3,light,camera" (Standard Heartbeat / Manual Control)
        5. "$MANUAL,p1,p2,p3" (Manual Thrusters)
        6. Legacy 11-element Python literal list from Surface Level Phase 1.
        7. "$STABILIZE,ON|OFF|STATUS" (straight-line current rejection + station hold).
        8. "$FUSE,<valid>,<range>,<bearing>,<sigma>,<sources>,<stage>" (UI sensor fusion).
        """
        global AHRS_HEADING_OFFSET_DEG, HEADING_CALIBRATED, PURE_PURSUIT_LOOKAHEAD_M, PURE_PURSUIT_WAYPOINT_CAPTURE_M, PURE_PURSUIT_PASS_CORRIDOR_M, YAW_DEADZONE_DEG, DOCK_LINE_LENGTH_M, DOCK_LOOKAHEAD_M, DOCK_SPEED_SCALE, DOCK_SLOWDOWN_DISTANCE_M, DOCK_MIN_SPEED_SCALE, DOCK_MIN_SURGE_PWM
        line = line.strip()
        if not line:
            return
        if not HEADING_CALIBRATED and line.upper().lstrip("$") in ("START", "RTH"):
            self.queue_log("[AHRS] WARNING: heading is NOT calibrated - navigation will steer wrong. "
                           "Use Hardware Setup > AHRS Heading Calibration before running a mission.")

        self.state.log_incoming("UI_COMMAND", {"line": line})

        with self.state.lock:
            self.state.last_command_rx = time.monotonic()
            self.state.gcs_connected = True

        # Dedicated GCS watchdog heartbeat.  It intentionally performs no
        # thruster action, so autonomous T1/T2/T3 commands are never overwritten.
        if line.upper() in ("HB", "$HB"):
            return

        # Dialect 1: Legacy Surface Level Phase 1 Array Format
        if line.startswith("[") and line.endswith("]"):
            try:
                parsed = ast.literal_eval(line)
                if isinstance(parsed, list) and len(parsed) >= 11:
                    with self.state.lock:
                        self.state.operating_mode = int(parsed[0])
                        self.state.start_run_mode = int(parsed[1])
                        if parsed[3]:
                            route = [tuple(map(float, w)) for w in parsed[3]]
                            if not all(valid_coords(w) for w in route):
                                raise ValueError("invalid legacy waypoint")
                            self.state.waypoints = route
                            self.state.current_wp_index = 0
                        self.state.light = int(parsed[9])
                        self.state.camera = int(parsed[10])
                        p1, p2, p3 = int(parsed[6]), int(parsed[7]), int(parsed[8])

                    if self.state.operating_mode == 0:  # Manual
                        self.teensy.send_command(p1, p2, p3, self.state.light, self.state.camera)
                    return
            except Exception:
                pass

        # Dialect 2: Waypoint Route Upload Protocol
        if line.startswith("$WP,") or line.startswith("WP,"):
            with self.state.lock:
                route_busy = self.state.start_run_mode == 1 or self.state.mission_paused
            if route_busy:
                emit_reply(b"ERR,WP,MISSION_ACTIVE_USE_STOP_FIRST\n")
                return
            parts = line.split(",")
            if len(parts) >= 2 and parts[1].strip().upper() == "END":
                with self.state.lock:
                    route_ready = bool(self.state.waypoints) and all(valid_coords(w) for w in self.state.waypoints)
                if not route_ready:
                    emit_reply(b"ERR,WP,EMPTY_OR_INVALID\n")
                    return
                emit_reply(b"ACK,WP\n")
                emit_reply(b"$ACK,WP\n")
                self.queue_log(f"[SYS] Waypoint route verified ({len(self.state.waypoints)} points). Ready to ARM.")
                return
            elif len(parts) >= 4:
                try:
                    idx = int(parts[1])
                    lat = float(parts[2])
                    lon = float(parts[3])
                    if not valid_coords((lat, lon)) or idx < 0:
                        raise ValueError("invalid waypoint index or coordinates")
                    with self.state.lock:
                        if idx == 0:
                            self.state.waypoints = [(lat, lon)]
                        else:
                            # Append or update in order
                            if idx < len(self.state.waypoints):
                                self.state.waypoints[idx] = (lat, lon)
                            elif idx == len(self.state.waypoints):
                                self.state.waypoints.append((lat, lon))
                            else:
                                raise ValueError("waypoint indices must be consecutive")
                        self.state.current_wp_index = 0
                        self.state.mission_completed = False
                except (ValueError, IndexError) as exc:
                    emit_reply(f"ERR,WP,{exc}\n".encode("utf-8"))
                return

        # Persistent/runtime frontend configuration from Hardware Setup.
        # Navigation precision values are applied immediately so UI and backend
        # use one source of truth during the same run.
        config_up = line.upper()
        if config_up.startswith("$CONFIG,") or config_up.startswith("CONFIG,"):
            parts = line.split(",")[1:]
            values = {}
            for item in parts:
                if "=" not in item:
                    continue
                key, value = item.split("=", 1)
                values[key.strip().upper()] = value.strip()

            with self.state.lock:
                if self.state.is_armed or self.state.start_run_mode == 1:
                    emit_reply(b"ERR,CONFIG,DISARM_AND_STOP_FIRST\n")
                    return

            try:
                if "LOS_LOOKAHEAD" in values:
                    PURE_PURSUIT_LOOKAHEAD_M = max(0.5, min(20.0, float(values["LOS_LOOKAHEAD"])))
                if "WAYPOINT_TOLERANCE" in values:
                    tol = max(0.3, min(20.0, float(values["WAYPOINT_TOLERANCE"])))
                    PURE_PURSUIT_WAYPOINT_CAPTURE_M = tol
                    PURE_PURSUIT_PASS_CORRIDOR_M = max(tol * 2.0, tol + 0.5)
                if "STEERING_DEADBAND" in values:
                    YAW_DEADZONE_DEG = max(0.2, min(15.0, float(values["STEERING_DEADBAND"])))
                if "DOCK_LINE_LENGTH" in values:
                    DOCK_LINE_LENGTH_M = max(3.0, min(50.0, float(values["DOCK_LINE_LENGTH"])))
                if "DOCK_LOOKAHEAD" in values:
                    DOCK_LOOKAHEAD_M = max(0.5, min(10.0, float(values["DOCK_LOOKAHEAD"])))
                if "DOCK_SPEED_SCALE" in values:
                    DOCK_SPEED_SCALE = max(0.10, min(1.0, float(values["DOCK_SPEED_SCALE"])))
                if "DOCK_SLOWDOWN_DISTANCE" in values:
                    DOCK_SLOWDOWN_DISTANCE_M = max(0.5, min(20.0, float(values["DOCK_SLOWDOWN_DISTANCE"])))
                if "DOCK_MIN_SPEED_SCALE" in values:
                    DOCK_MIN_SPEED_SCALE = max(0.05, min(DOCK_SPEED_SCALE, float(values["DOCK_MIN_SPEED_SCALE"])))
                if "DOCK_MIN_SURGE_PWM" in values:
                    DOCK_MIN_SURGE_PWM = max(0, min(150, int(float(values["DOCK_MIN_SURGE_PWM"]))))
                DOCK_MIN_SPEED_SCALE = min(DOCK_MIN_SPEED_SCALE, DOCK_SPEED_SCALE)
            except (ValueError, TypeError):
                emit_reply(b"ERR,CONFIG,BAD_NAV_PARAMETER\n")
                return

            saved = None
            corrected_now = None
            if "AHRS_OFFSET" in values:
                try:
                    new_offset = float(values["AHRS_OFFSET"])
                    if not math.isfinite(new_offset):
                        raise ValueError
                    new_offset %= 360.0
                except ValueError:
                    emit_reply(b"ERR,CONFIG,BAD_AHRS_OFFSET\n")
                    return
                with self.state.lock:
                    raw_now = self.state.raw_yaw
                old_offset = AHRS_HEADING_OFFSET_DEG
                AHRS_HEADING_OFFSET_DEG = new_offset
                HEADING_CALIBRATED = True
                corrected_now = correct_heading(raw_now)
                saved = bool(HEADING_CAL_PATH) and save_heading_calibration(
                    HEADING_CAL_PATH, new_offset, raw_now, corrected_now
                )
                self.queue_log(
                    f"[AHRS] UI offset applied: {old_offset:.2f} -> {new_offset:.2f} deg; "
                    f"raw={raw_now:.2f}, corrected={corrected_now:.2f} "
                    f"({'saved' if saved else 'NOT saved'})."
                )

            self.queue_log(
                f"[CONFIG] Precision: WP tol={PURE_PURSUIT_WAYPOINT_CAPTURE_M:.2f}m, "
                f"lookahead={PURE_PURSUIT_LOOKAHEAD_M:.2f}m, yaw deadband={YAW_DEADZONE_DEG:.2f}deg; "
                f"dock line={DOCK_LINE_LENGTH_M:.1f}m, dock lookahead={DOCK_LOOKAHEAD_M:.2f}m, "
                f"dock speed={DOCK_SPEED_SCALE:.2f}x, slowdown={DOCK_SLOWDOWN_DISTANCE_M:.1f}m, "
                f"dock min surge={DOCK_MIN_SURGE_PWM} PWM."
            )
            extra = f",AHRS_OFFSET={AHRS_HEADING_OFFSET_DEG:.2f},heading={corrected_now:.2f},saved={int(bool(saved))}" if corrected_now is not None else ""
            emit_reply((f"ACK,CONFIG,WP_TOL={PURE_PURSUIT_WAYPOINT_CAPTURE_M:.2f},LOOKAHEAD={PURE_PURSUIT_LOOKAHEAD_M:.2f},DOCK_LINE={DOCK_LINE_LENGTH_M:.1f}{extra}\n").encode())
            return

        # Direct persistent heading-offset command for diagnostics/scripts.
        # Semantics match correct_heading(): heading = (offset - raw_yaw) mod 360.
        if config_up.startswith("$CALOFFSET,") or config_up.startswith("CALOFFSET,"):
            try:
                new_offset = float(line.split(",", 1)[1])
                if not math.isfinite(new_offset):
                    raise ValueError
                new_offset %= 360.0
            except (ValueError, IndexError):
                emit_reply(b"ERR,CALOFFSET,BAD_OFFSET\n")
                return
            with self.state.lock:
                if self.state.is_armed or self.state.start_run_mode == 1:
                    emit_reply(b"ERR,CALOFFSET,DISARM_AND_STOP_FIRST\n")
                    return
                raw_now = self.state.raw_yaw
            AHRS_HEADING_OFFSET_DEG = new_offset
            HEADING_CALIBRATED = True
            corrected_now = correct_heading(raw_now)
            saved = bool(HEADING_CAL_PATH) and save_heading_calibration(
                HEADING_CAL_PATH, new_offset, raw_now, corrected_now
            )
            emit_reply(
                f"ACK,CALOFFSET,offset={new_offset:.2f},heading={corrected_now:.2f},saved={int(saved)}\n".encode()
            )
            self.queue_log(
                f"[AHRS] Direct heading offset set to {new_offset:.2f} deg; "
                f"current corrected heading {corrected_now:.2f} deg ({'saved' if saved else 'NOT saved'})."
            )
            return

        # AHRS heading calibration: $CALNORTH | $CALHEADING,<deg> | $CALSTATUS
        cal_up = line.upper()
        if cal_up in ("$CALSTATUS", "CALSTATUS"):
            with self.state.lock:
                raw, hdg = self.state.raw_yaw, self.state.yaw
            emit_reply(f"CAL,STATUS,offset={AHRS_HEADING_OFFSET_DEG:.2f},raw={raw:.2f},heading={round(hdg, 2) % 360.0:.2f},"
                       f"calibrated={int(HEADING_CALIBRATED)}\n".encode())
            return
        if cal_up in ("$CALNORTH", "CALNORTH") or cal_up.startswith("$CALHEADING,") or cal_up.startswith("CALHEADING,"):
            try:
                ref = 0.0 if "NORTH" in cal_up else float(line.split(",", 1)[1])
                if not math.isfinite(ref):
                    raise ValueError
            except (ValueError, IndexError):
                emit_reply(b"ERR,CAL,BAD_HEADING\n")
                return
            with self.state.lock:
                if self.state.is_armed or self.state.start_run_mode == 1:
                    emit_reply(b"ERR,CAL,DISARM_AND_STOP_FIRST\n")
                    return
                last_rx = self.state.last_ahrs_rx
            if not last_rx or time.monotonic() - last_rx > NAV_SENSOR_TIMEOUT:
                emit_reply(b"ERR,CAL,AHRS_STALE\n")
                return
            # Circular mean of raw yaw over ~2 s (vehicle must be still).
            sx = sy = 0.0
            n = 0
            for _ in range(40):
                with self.state.lock:
                    r = self.state.raw_yaw
                if math.isfinite(r):
                    sx += math.cos(math.radians(r))
                    sy += math.sin(math.radians(r))
                    n += 1
                time.sleep(0.05)
            if n < 10 or math.hypot(sx, sy) / max(n, 1) < 0.98:
                emit_reply(b"ERR,CAL,YAW_UNSTABLE_KEEP_VEHICLE_STILL\n")
                return
            raw_mean = math.degrees(math.atan2(sy, sx)) % 360.0
            new_offset = (ref % 360.0 + raw_mean) % 360.0   # heading = offset - raw
            old_offset = AHRS_HEADING_OFFSET_DEG
            AHRS_HEADING_OFFSET_DEG = new_offset
            HEADING_CALIBRATED = True
            saved = bool(HEADING_CAL_PATH) and save_heading_calibration(HEADING_CAL_PATH, new_offset, raw_mean, ref)
            self.queue_log(f"[AHRS] Heading calibrated: raw {raw_mean:.1f} deg = {ref % 360.0:.1f} deg; "
                           f"offset {old_offset:.1f} -> {new_offset:.1f} deg ({'saved' if saved else 'NOT saved'}).")
            with self.state.lock:
                hdg_now = correct_heading(raw_mean)
            emit_reply(f"ACK,CAL,offset={new_offset:.2f},raw={raw_mean:.2f},heading={round(hdg_now, 2) % 360.0:.2f},"
                       f"saved={int(saved)}\n".encode())
            return

        if line.upper() in ("$MISSION_STATUS", "MISSION_STATUS"):
            with self.state.lock:
                status = "PAUSED" if self.state.mission_paused else ("RUNNING" if self.state.start_run_mode else "IDLE")
                emit_reply(f"MISSION,{status},{self.state.current_wp_index + 1},{len(self.state.waypoints)},{self.state.mission_pause_reason}\n".encode())
            return

        if line.upper() in ("$RESUME", "RESUME"):
            with self.state.lock:
                if not self.state.mission_paused:
                    emit_reply(b"ERR,RESUME,NOT_PAUSED\n")
                    return
                if not self.state.is_armed:
                    emit_reply(b"ERR,RESUME,DISARMED\n")
                    return
                self.state.mission_resume_pending = True
            self.queue_log("[AUTO] Operator requested resume; waiting for stable GCS and fresh GPS/AHRS.")
            emit_reply(b"ACK,RESUME,PENDING_SAFETY_CHECKS\n")
            return

        # Active stabilization / station-hold command. This switch is global:
        # PATH mode strengthens straight fixed-leg following during a mission;
        # HOLD mode captures the current GPS point whenever the route is stopped.
        stab_up = line.upper()
        if stab_up.startswith("$STABILIZE") or stab_up.startswith("STABILIZE"):
            parts = [part.strip() for part in line.split(",")]
            action = parts[1].upper() if len(parts) > 1 else "STATUS"

            if action in ("ON", "1", "START", "ENABLE"):
                now = time.monotonic()
                with self.state.lock:
                    self.state.active_stabilization = True
                    self.state.cross_track_m = 0.0
                    self.state.stabilization_correction_deg = 0.0
                    self.state.stabilization_hold_distance_m = 0.0
                    route_running = bool(self.state.start_run_mode and self.state.operating_mode == 1)
                    sensors_ok = (
                        self.state.gps_valid
                        and valid_coords((self.state.latitude, self.state.longitude))
                        and self.state.last_gps_rx > 0.0
                        and now - self.state.last_gps_rx <= NAV_SENSOR_TIMEOUT
                        and self.state.last_ahrs_rx > 0.0
                        and now - self.state.last_ahrs_rx <= NAV_SENSOR_TIMEOUT
                        and math.isfinite(self.state.yaw)
                    )
                    if route_running:
                        self.state.stabilization_hold_coords = None
                        self.state.stabilization_hold_heading = None
                        self.state.stabilization_mode = "PATH"
                    elif self.state.is_armed and sensors_ok and not self.state.is_docking:
                        self.state.stabilization_hold_coords = (self.state.latitude, self.state.longitude)
                        self.state.stabilization_hold_heading = self.state.yaw
                        self.state.stabilization_mode = "HOLD"
                    elif not self.state.is_armed:
                        self.state.stabilization_hold_coords = None
                        self.state.stabilization_hold_heading = None
                        self.state.stabilization_mode = "DISARMED"
                    else:
                        self.state.stabilization_hold_coords = None
                        self.state.stabilization_hold_heading = None
                        self.state.stabilization_mode = "HOLD_SENSOR_WAIT"
                    mode = self.state.stabilization_mode
                self.queue_log(
                    f"[STAB] Active stabilization ON ({mode}). "
                    "PATH rejects cross-current; HOLD maintains the captured GPS position."
                )
                emit_reply(f"ACK,STABILIZE,ON,{mode}\n".encode())
                return

            if action in ("OFF", "0", "STOP", "DISABLE"):
                with self.state.lock:
                    was_station_hold = bool(self.state.active_stabilization and not self.state.start_run_mode)
                    self.state.active_stabilization = False
                    self.state.stabilization_mode = "OFF"
                    self.state.stabilization_hold_coords = None
                    self.state.stabilization_hold_heading = None
                    self.state.cross_track_m = 0.0
                    self.state.stabilization_correction_deg = 0.0
                    self.state.stabilization_hold_distance_m = 0.0
                    light, camera = self.state.light, self.state.camera
                    if was_station_hold:
                        self.state.t1 = self.state.t2 = self.state.t3 = NEUTRAL_PWM
                if was_station_hold:
                    self.teensy.send_command(NEUTRAL_PWM, NEUTRAL_PWM, NEUTRAL_PWM, light, camera)
                self.queue_log("[STAB] Active stabilization OFF.")
                emit_reply(b"ACK,STABILIZE,OFF,OFF\n")
                return

            if action in ("STATUS", "?", "STATE"):
                with self.state.lock:
                    on = self.state.active_stabilization
                    mode = self.state.stabilization_mode
                    xte = self.state.cross_track_m
                    hold_d = self.state.stabilization_hold_distance_m
                emit_reply(
                    f"ACK,STABILIZE,{'ON' if on else 'OFF'},{mode},"
                    f"XTE={xte:.2f},HOLD={hold_d:.2f}\n".encode()
                )
                return

            emit_reply(b"ERR,STABILIZE,EXPECTED_ON_OFF_STATUS\n")
            return

        # Sensor-fusion link from the UI.  The UI fuses GPS, RM3100, Ping360
        # and camera detections and sends one relative dock target.  Backend
        # validates, timestamps and acknowledges every update.  Propulsion is
        # NOT silently reassigned here; the value is made available to onboard
        # control/telemetry without changing the existing docking safety model.
        fuse_up = line.upper()
        if fuse_up.startswith("$FUSE") or fuse_up.startswith("FUSE"):
            parts = [part.strip() for part in line.split(",")]
            action = parts[1].upper() if len(parts) > 1 else "STATUS"

            if action in ("STATUS", "?", "STATE"):
                now = time.monotonic()
                with self.state.lock:
                    age = (now - self.state.last_fusion_rx) if self.state.last_fusion_rx > 0.0 else float("inf")
                    fresh = bool(self.state.fusion_valid and age <= FUSION_RX_TIMEOUT_S)
                    rng = self.state.fusion_range_m
                    brg = self.state.fusion_bearing_rel_deg
                    sig = self.state.fusion_sigma_m
                    src = self.state.fusion_sources
                    stage = self.state.fusion_stage
                rng_txt = "" if rng is None else f"{rng:.3f}"
                brg_txt = "" if brg is None else f"{brg:.2f}"
                sig_txt = "" if sig is None else f"{sig:.3f}"
                age_txt = "" if not math.isfinite(age) else f"{age:.2f}"
                emit_reply((f"ACK,FUSE,{1 if fresh else 0},{rng_txt},{brg_txt},{sig_txt},"
                            f"{src},{stage},AGE={age_txt}\n").encode())
                return

            try:
                valid_flag = int(float(parts[1])) if len(parts) > 1 and parts[1] != "" else 0
            except (ValueError, TypeError):
                emit_reply(b"ERR,FUSE,BAD_VALID_FLAG\n")
                return

            now = time.monotonic()
            if valid_flag == 0:
                with self.state.lock:
                    self.state.fusion_valid = False
                    self.state.fusion_range_m = None
                    self.state.fusion_bearing_rel_deg = None
                    self.state.fusion_sigma_m = None
                    self.state.fusion_sources = ""
                    self.state.fusion_stage = "NO_FIX"
                    self.state.last_fusion_rx = now
                emit_reply(b"ACK,FUSE,0,,,,,NO_FIX,AGE=0.00\n")
                return

            try:
                if len(parts) < 4 or parts[3] == "":
                    raise ValueError("bearing is required for a valid fused target")
                rng = None if len(parts) < 3 or parts[2] == "" else float(parts[2])
                brg = float(parts[3])
                sig = 0.0 if len(parts) < 5 or parts[4] == "" else float(parts[4])
                if rng is not None and (not math.isfinite(rng) or rng < 0.0 or rng > 10000.0):
                    raise ValueError("range out of bounds")
                if not math.isfinite(brg):
                    raise ValueError("bearing is not finite")
                if not math.isfinite(sig) or sig < 0.0 or sig > 1000.0:
                    raise ValueError("sigma out of bounds")
                brg = wrap180(brg)
                sources_raw = parts[5].upper() if len(parts) > 5 else ""
                sources = "".join(ch for ch in sources_raw if ch in "GMSC")
                stage_raw = parts[6].upper() if len(parts) > 6 and parts[6] else "UNKNOWN"
                stage = "".join(ch for ch in stage_raw if ch.isalnum() or ch in "_-.")[:32] or "UNKNOWN"
            except (ValueError, TypeError) as exc:
                emit_reply(f"ERR,FUSE,{str(exc).replace(',', ';')}\n".encode("utf-8"))
                return

            with self.state.lock:
                self.state.fusion_valid = True
                self.state.fusion_range_m = rng
                self.state.fusion_bearing_rel_deg = brg
                self.state.fusion_sigma_m = sig
                self.state.fusion_sources = sources
                self.state.fusion_stage = stage
                self.state.last_fusion_rx = now
            self.state.log_incoming("FUSION_TARGET", {
                "valid": 1, "range_m": rng, "bearing_rel_deg": brg,
                "sigma_m": sig, "sources": sources, "stage": stage
            })
            rng_txt = "" if rng is None else f"{rng:.3f}"
            emit_reply((f"ACK,FUSE,1,{rng_txt},{brg:.2f},{sig:.3f},"
                        f"{sources},{stage},AGE=0.00\n").encode())
            return

        # Dialect 3: Arming & Disarming
        if line in ("$ARM", "ARM"):
            with self.state.lock:
                self.state.is_armed = True
                if self.state.active_stabilization and not self.state.start_run_mode:
                    # Capture a fresh hold point in the 20 Hz station-hold loop.
                    self.state.stabilization_hold_coords = None
                    self.state.stabilization_hold_heading = None
                    self.state.stabilization_mode = "HOLD_SENSOR_WAIT"
            self.queue_log("[SYS] Vehicle ARMED. Autonomous thruster control ENABLED.")
            emit_reply(b"ACK,ARM\n")
            return

        if line in ("$DISARM", "DISARM"):
            with self.state.lock:
                self.state.is_armed = False
                self.state.is_docking = False
                self.state.gps_docking_active = False
                self.state.start_run_mode = 0
                self.state.operating_mode = 0
                self.state.mission_paused = False
                self.state.mission_resume_pending = False
                self.state.mission_recovered = False
                self.state.stabilization_hold_coords = None
                self.state.stabilization_hold_heading = None
                self.state.stabilization_hold_distance_m = 0.0
                self.state.cross_track_m = 0.0
                self.state.stabilization_correction_deg = 0.0
                self.state.stabilization_mode = "DISARMED" if self.state.active_stabilization else "OFF"
            if self.checkpoint and self.state.waypoints:
                self.checkpoint.save(self.state.waypoints, self.state.current_wp_index, "DISARMED")
            self.teensy.send_command(NEUTRAL_PWM, NEUTRAL_PWM, NEUTRAL_PWM, self.state.light, self.state.camera)
            self.queue_log("[SYS] Vehicle DISARMED. Thrusters neutral.")
            emit_reply(b"ACK,DISARM\n")
            return

        # Dialect 4: Mission Execution ($START, $STOP, $RTH)
        if line in ("$START", "START"):
            with self.state.lock:
                if self.state.mission_paused or self.state.mission_recovered:
                    emit_reply(b"ERR,START,RECOVERY_PENDING_USE_RESUME_OR_STOP\n")
                    return
                if self.state.start_run_mode == 1:
                    emit_reply(b"ERR,START,ALREADY_RUNNING\n")
                    return
            if not self.state.is_armed:
                self.queue_log("[SYS] REJECT START: Vehicle is not armed! Send $ARM first.")
                return
            if not self.state.waypoints or not all(valid_coords(w) for w in self.state.waypoints):
                self.queue_log("[SYS] REJECT START: No valid waypoint route uploaded!")
                return
            if self.checkpoint and not self.checkpoint.save(self.state.waypoints, 0, "RUNNING"):
                emit_reply(b"ERR,START,CHECKPOINT_WRITE_FAILURE\n")
                return
            with self.state.lock:
                self.state.mission_recovered = False
                self.state.mission_paused = False
                self.state.is_docking = False
                self.state.gps_docking_active = False
                self.state.operating_mode = 1  # Automatic
                self.state.start_run_mode = 1  # Run
                self.state.mission_completed = False
                self.state.current_wp_index = 0
                self.state.stabilization_hold_coords = None
                self.state.stabilization_hold_heading = None
                if self.state.active_stabilization:
                    self.state.stabilization_mode = "PATH"
            self.queue_log(f"[AUTO] Mission STARTED with {len(self.state.waypoints)} waypoints.")
            emit_reply(b"ACK,START\n")
            return

        if line in ("$STOP", "STOP"):
            with self.state.lock:
                self.state.is_docking = False
                self.state.gps_docking_active = False
                self.state.start_run_mode = 0
                self.state.operating_mode = 0
                self.state.nav_direction = "STOPPED"
                self.state.mission_paused = False
                self.state.mission_resume_pending = False
                self.state.mission_recovered = False
                if self.state.active_stabilization and self.state.is_armed:
                    self.state.stabilization_hold_coords = None
                    self.state.stabilization_hold_heading = None
                    self.state.stabilization_mode = "HOLD_SENSOR_WAIT"
            if self.checkpoint and self.state.waypoints:
                self.checkpoint.save(self.state.waypoints, self.state.current_wp_index, "STOPPED")
            self.teensy.send_command(NEUTRAL_PWM, NEUTRAL_PWM, NEUTRAL_PWM, self.state.light, self.state.camera)
            self.queue_log("[SYS] Mission STOPPED. Returning to standby.")
            emit_reply(b"ACK,STOP\n")
            return

        if line in ("$RTH", "RTH"):
            if self.state.mission_paused:
                emit_reply(b"ERR,RTH,PAUSED_SEND_STOP_FIRST\n")
                return
            if not self.state.is_armed:
                self.queue_log("[SYS] REJECT RTH: Vehicle is not armed!")
                return
            with self.state.lock:
                self.state.is_docking = False
                self.state.gps_docking_active = False
                if self.state.home_coords:
                    if self.checkpoint and not self.checkpoint.save([self.state.home_coords], 0, "RUNNING", reason="RTH"):
                        emit_reply(b"ERR,RTH,CHECKPOINT_WRITE_FAILURE\n")
                        return
                    self.state.waypoints = [self.state.home_coords]
                    self.state.current_wp_index = 0
                    self.state.operating_mode = 1
                    self.state.start_run_mode = 1
                    self.state.mission_completed = False
                    self.state.stabilization_hold_coords = None
                    self.state.stabilization_hold_heading = None
                    if self.state.active_stabilization:
                        self.state.stabilization_mode = "PATH"
                    self.queue_log(f"[AUTO] RTH Triggered: Navigating home to {self.state.home_coords}")
                else:
                    self.queue_log("[SYS] RTH Error: No home coordinates recorded yet!")
            return

        with self.state.lock:
            pause_latched = self.state.mission_paused
        if pause_latched and (line.startswith("CMD,") or line.startswith("$CMD,") or line.startswith("$MANUAL,") or ("," in line and line[0].isdigit())):
            emit_reply(b"ERR,MANUAL,PAUSED_SEND_STOP_FIRST\n")
            return

        # Dialect 5: Standard Heartbeat / Manual Command ("pwm1,pwm2,pwm3,light,camera" or "CMD,p1,p2,p3,light,camera")
        cmd_args = None
        if line.startswith("CMD,") or line.startswith("$CMD,"):
            raw_parts = line.split(",")
            if len(raw_parts) >= 6:
                cmd_args = raw_parts[1:6]
        elif "," in line and not line.startswith("$") and not line.startswith("[") and not line.startswith("{"):
            raw_parts = line.split(",")
            if len(raw_parts) == 5:
                try:
                    _ = [float(x.strip()) for x in raw_parts]
                    cmd_args = raw_parts
                except ValueError:
                    cmd_args = None

        if cmd_args is not None:
            try:
                p1, p2, p3 = int(float(cmd_args[0])), int(float(cmd_args[1])), int(float(cmd_args[2]))
                lit, cam = int(float(cmd_args[3])), int(float(cmd_args[4]))
                with self.state.lock:
                    self.state.light = 1 if lit else 0
                    self.state.camera = 1 if cam else 0

                is_active_manual = (p1 != NEUTRAL_PWM or p2 != NEUTRAL_PWM or p3 != NEUTRAL_PWM)
                if is_active_manual:
                    self._manual_override_stabilization(emit_reply)
                if is_active_manual and self.state.operating_mode == 1:
                    with self.state.lock:
                        self.state.is_docking = False
                        self.state.gps_docking_active = False
                        self.state.operating_mode = 0
                        self.state.start_run_mode = 0
                    with self.state.lock:
                        self.state.mission_paused = False
                        self.state.mission_resume_pending = False
                        self.state.mission_recovered = False
                    if self.checkpoint and self.state.waypoints:
                        self.checkpoint.save(self.state.waypoints, self.state.current_wp_index, "MANUAL_OVERRIDE")
                    self.queue_log("[AUTO] Manual override detected. Autonomous control stopped.")

                # Neutral GUI/heartbeat packets must not overwrite the 20 Hz
                # station-hold controller. Non-neutral manual input disables
                # stabilization above and takes control immediately.
                station_hold_owns_thrusters = self._station_hold_owns_thrusters()
                if (((self.state.operating_mode == 0 and not self.state.is_docking) or is_active_manual)
                        and not (station_hold_owns_thrusters and not is_active_manual)):
                    self.teensy.send_command(p1, p2, p3, self.state.light, self.state.camera)
            except Exception:
                pass
            return

        # Dialect 6: "$MANUAL,p1,p2,p3"
        if line.startswith("$MANUAL,"):
            parts = line.split(",")
            if len(parts) >= 4:
                try:
                    p1, p2, p3 = int(float(parts[1])), int(float(parts[2])), int(float(parts[3]))
                    is_active_manual = (p1 != NEUTRAL_PWM or p2 != NEUTRAL_PWM or p3 != NEUTRAL_PWM)
                    if is_active_manual:
                        self._manual_override_stabilization(emit_reply)
                    if is_active_manual and self.state.operating_mode == 1:
                        with self.state.lock:
                            self.state.is_docking = False
                            self.state.gps_docking_active = False
                            self.state.operating_mode = 0
                            self.state.start_run_mode = 0
                        self.queue_log("[AUTO] Manual override detected. Autonomous control stopped.")
                    station_hold_owns_thrusters = self._station_hold_owns_thrusters()
                    if not (station_hold_owns_thrusters and not is_active_manual):
                        self.teensy.send_command(p1, p2, p3, self.state.light, self.state.camera)
                except Exception:
                    pass
            return

        # Dialect 8: RTSP Camera Recording Commands ($REC,START[,url] and $REC,STOP)
        if line.startswith("$REC,") or line.startswith("REC,"):
            parts = line.split(",")
            action = parts[1].strip().upper() if len(parts) > 1 else ""
            if action == "START":
                custom_url = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
                success, path_or_err = self.recorder.start_recording(custom_url, emit_log=self.queue_log)
                if success:
                    emit_reply(b"ACK,REC,START\n")
                else:
                    self.queue_log(f"[CAM] Recording start failed: {path_or_err}")
                    emit_reply(f"ERR,REC,START,{path_or_err}\n".encode("utf-8"))
                return
            elif action == "STOP":
                success, path_or_err = self.recorder.stop_recording()
                if success:
                    fn = os.path.basename(path_or_err) if path_or_err else "saved"
                    self.queue_log(f"[CAM] Recording stopped/saved: {fn}")
                    emit_reply(f"ACK,REC,STOP,{fn}\n".encode("utf-8"))
                else:
                    self.queue_log(f"[CAM] Recording stopped: {path_or_err}")
                    emit_reply(b"ACK,REC,STOP\n")
                return

        # Dialect 9: GPS Docking Route Commands
        if line.startswith("$DOCK") or line.startswith("DOCK"):
            parts = line.split(",")
            action = parts[1].strip().upper() if len(parts) > 1 else "START"
            if action == "SET":
                try:
                    # Preferred UI protocol: $DOCK,SET,dock_lat,dock_lon,dock_face_heading
                    # The approach point is projected DOCK_LINE_LENGTH_M outward from
                    # the dock face. Vehicle first captures that point, aligns to the
                    # fixed approach leg, then follows the same line into the dock.
                    if len(parts) == 5:
                        dock = (float(parts[2]), float(parts[3]))
                        dock_face_heading = float(parts[4]) % 360.0
                        approach = destination_point(dock, dock_face_heading, DOCK_LINE_LENGTH_M)
                    elif len(parts) == 6:
                        # Backward-compatible explicit approach + dock coordinates.
                        approach = (float(parts[2]), float(parts[3]))
                        dock = (float(parts[4]), float(parts[5]))
                        _, line_bearing = haversine_and_bearing(dock, approach)
                        dock_face_heading = line_bearing % 360.0
                    else:
                        raise ValueError("expected dock lat/lon/heading or approach lat/lon + dock lat/lon")
                    if not (-90 <= approach[0] <= 90 and -180 <= approach[1] <= 180 and
                            -90 <= dock[0] <= 90 and -180 <= dock[1] <= 180):
                        raise ValueError("coordinates are outside valid latitude/longitude ranges")
                    if approach == (0.0, 0.0) or dock == (0.0, 0.0):
                        raise ValueError("coordinates cannot be 0,0")
                    with self.state.lock:
                        self.state.dock_waypoints = [approach, dock]
                    self.queue_log(
                        f"[DOCK] Precision route set: projected approach {DOCK_LINE_LENGTH_M:.1f}m from dock face "
                        f"heading {dock_face_heading:.1f}deg -> ({approach[0]:.7f}, {approach[1]:.7f}), "
                        f"dock ({dock[0]:.7f}, {dock[1]:.7f})."
                    )
                    emit_reply(f"ACK,DOCK,SET,approach={approach[0]:.7f}:{approach[1]:.7f},line={DOCK_LINE_LENGTH_M:.1f}\n".encode())
                except (ValueError, IndexError) as exc:
                    self.queue_log(f"[DOCK] GPS route rejected: {exc}")
                    emit_reply(f"ERR,DOCK,SET,{exc}\n".encode("utf-8"))
                return
            if action in ("START", "1", "ON"):
                with self.state.lock:
                    already_active = bool(self.state.gps_docking_active and self.state.start_run_mode == 1)
                if already_active:
                    emit_reply(b"ACK,DOCK,START,ALREADY_ACTIVE\n")
                    return
                if not self.state.is_armed:
                    self.queue_log("[DOCK] GPS docking rejected: arm the vehicle first.")
                    emit_reply(b"ERR,DOCK,START,NOT_ARMED\n")
                    return
                if len(self.state.dock_waypoints) != 2:
                    self.queue_log("[DOCK] GPS docking rejected: set both GPS waypoints first.")
                    emit_reply(b"ERR,DOCK,START,ROUTE_NOT_SET\n")
                    return
                with self.state.lock:
                    self.state.is_docking = True
                    self.state.gps_docking_active = True
                    self.state.waypoints = list(self.state.dock_waypoints)
                    self.state.current_wp_index = 0
                    self.state.mission_completed = False
                    self.state.operating_mode = 1
                    self.state.start_run_mode = 1
                    self.state.dock_stage = "APPROACH"
                    self.state.dock_guideline = "Navigating to the GPS approach waypoint."
                    self.state.nav_direction = "DOCK_APPROACH"
                self.queue_log("[DOCK] GPS docking engaged: navigating via approach waypoint to dock waypoint.")
                emit_reply(b"ACK,DOCK,START\n")
            elif action in ("STOP", "0", "OFF"):
                with self.state.lock:
                    self.state.is_docking = False
                    self.state.gps_docking_active = False
                    self.state.operating_mode = 0
                    self.state.start_run_mode = 0
                    self.state.dock_stage = "STANDBY"
                    self.state.dock_guideline = "GPS docking stopped."
                    self.state.nav_direction = "STOPPED"
                    self.state.t1 = NEUTRAL_PWM
                    self.state.t2 = NEUTRAL_PWM
                    self.state.t3 = NEUTRAL_PWM
                self.teensy.send_command(NEUTRAL_PWM, NEUTRAL_PWM, NEUTRAL_PWM, self.state.light, self.state.camera)
                self.queue_log("[DOCK] GPS docking stopped. Thrusters neutral.")
                emit_reply(b"ACK,DOCK,STOP\n")
            return

        # Dialect 10: Scanning Sonar Commands ($SONAR, $SONAR,START, $SONAR,STOP, $SONAR,1, $SONAR,0)
        if line.startswith("$SONAR") or line.startswith("SONAR"):
            parts = line.split(",")
            action = parts[1].strip().upper() if len(parts) > 1 else "START"
            if action in ("START", "1", "ON"):
                with self.state.lock:
                    self.state.sonar_enabled = True
                    self.state.last_sonar_rx = time.monotonic()
                if self.sonar:
                    self.sonar.activate()
                else:
                    self.queue_log("[SONAR] Sonar hardware link not initialized, simulation/software mode active.")
                emit_reply(b"ACK,SONAR,START\n")
            elif action in ("STOP", "0", "OFF"):
                with self.state.lock:
                    self.state.sonar_enabled = False
                if self.sonar:
                    self.sonar.deactivate()
                emit_reply(b"ACK,SONAR,STOP\n")
            return

        # Dialect 7: Raw Heartbeat / Manual 5-tuple: "1500,1500,1500,light,camera"
        parts = line.split(",")
        if len(parts) == 5:
            try:
                p1, p2, p3 = int(float(parts[0])), int(float(parts[1])), int(float(parts[2]))
                lit, cam = int(float(parts[3])), int(float(parts[4]))
                with self.state.lock:
                    self.state.light = 1 if lit else 0
                    self.state.camera = 1 if cam else 0
                is_active_manual = (p1 != NEUTRAL_PWM or p2 != NEUTRAL_PWM or p3 != NEUTRAL_PWM)
                if is_active_manual:
                    self._manual_override_stabilization(emit_reply)
                if is_active_manual and self.state.operating_mode == 1:
                    with self.state.lock:
                        self.state.is_docking = False
                        self.state.gps_docking_active = False
                        self.state.operating_mode = 0
                        self.state.start_run_mode = 0
                    self.queue_log("[AUTO] Manual override detected. Autonomous control stopped.")
                station_hold_owns_thrusters = self._station_hold_owns_thrusters()
                if (((self.state.operating_mode == 0 and not self.state.is_docking) or is_active_manual)
                        and not (station_hold_owns_thrusters and not is_active_manual)):
                    self.teensy.send_command(p1, p2, p3, self.state.light, self.state.camera)
                return
            except Exception:
                pass

    def run_tcp_server(self) -> None:
        """TCP server loop allowing direct UI connection via 'TCP CLIENT 127.0.0.1:8888'."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", self.tcp_port))
            sock.listen(5)
            sock.settimeout(1.0)
            print(f"[TCP] Server listening on port {self.tcp_port}")

            while not self.stop.is_set():
                try:
                    client, addr = sock.accept()
                    client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    print(f"[TCP] Surface UI connected from {addr}")
                    threading.Thread(target=self._handle_tcp_client, args=(client,), daemon=True).start()
                except socket.timeout:
                    continue
                except Exception:
                    pass
        except Exception as e:
            print(f"[TCP] Server initialization error: {e}")

    def _handle_tcp_client(self, client: socket.socket) -> None:
        rx_buf = bytearray()
        next_tx = time.monotonic()

        def reply(data: bytes):
            try:
                client.sendall(data)
            except Exception:
                pass

        try:
            client.settimeout(0.02)
            while not self.stop.is_set():
                # Receive inbound commands
                try:
                    chunk = client.recv(4096)
                    if chunk:
                        rx_buf.extend(chunk)
                        while b"\n" in rx_buf:
                            raw_line, _, rx_buf = rx_buf.partition(b"\n")
                            self.handle_command_line(raw_line.decode("utf-8", errors="ignore"), reply)
                    elif chunk == b"":
                        break
                except socket.timeout:
                    pass

                # Emit outbound telemetry and queued logs
                now = time.monotonic()
                if now >= next_tx:
                    next_tx = now + self.period
                    # Flush event logs first
                    with self.tx_lock:
                        while self.log_queue:
                            msg = self.log_queue.pop(0)
                            reply(f"LOG,{msg}\n".encode("utf-8"))

                    # Send telemetry packet in configured format(s)
                    if self.telemetry_format in ("json", "both", "all"):
                        reply(self.state.json_packet())
                    if self.telemetry_format in ("csv", "both", "all"):
                        reply(self.state.csv_packet())
                    if self.telemetry_format in ("teensy", "all"):
                        reply(self.state.teensy_packet())

                time.sleep(0.002)
        except Exception:
            pass
        finally:
            try:
                client.close()
            except Exception:
                pass
            print("[TCP] Surface UI client disconnected")

    def run_serial(self) -> None:
        """Serial connection loop for RF / P8 UART hardware link."""
        if not serial:
            print("[P8/SERIAL] pyserial not available; serial link disabled")
            return
        while not self.stop.is_set():
            try:
                # Use write_timeout=1.0 so full packets are never truncated
                with serial.Serial(self.port, self.baud, timeout=0, write_timeout=1.0) as ser:
                    print(f"[P8/SERIAL] Link active: {self.port} @ {self.baud} (format: {self.telemetry_format})")
                    rx_buf = bytearray()
                    next_tx = time.monotonic()

                    def reply(data: bytes):
                        try:
                            ser.write(data)
                        except Exception:
                            pass

                    while not self.stop.is_set():
                        # Read inbound commands
                        waiting = ser.in_waiting
                        if waiting:
                            rx_buf.extend(ser.read(waiting))
                            while b"\n" in rx_buf:
                                raw_line, _, rx_buf = rx_buf.partition(b"\n")
                                self.handle_command_line(raw_line.decode("utf-8", errors="ignore"), reply)

                        # Emit outbound telemetry & logs
                        now = time.monotonic()
                        if now >= next_tx:
                            next_tx = now + self.period
                            with self.tx_lock:
                                while self.log_queue:
                                    msg = self.log_queue.pop(0)
                                    reply(f"LOG,{msg}\n".encode("utf-8"))

                            if not ser.out_waiting:
                                if self.telemetry_format in ("json", "both", "all"):
                                    reply(self.state.json_packet())
                                if self.telemetry_format in ("csv", "both", "all"):
                                    reply(self.state.csv_packet())
                                if self.telemetry_format in ("teensy", "all"):
                                    reply(self.state.teensy_packet())

                        time.sleep(0.002)
            except Exception as e:
                print(f"[P8/SERIAL] Port error on {self.port}: {e}; retrying in 1 s...")
                self.stop.wait(1.0)


# =============================================================================
# BACKEND TELEMETRY DISK LOGGER
# =============================================================================

def get_default_log_dir() -> str:
    """
    Resolves the canonical log folder on the backend.
    Checks parent directory (e.g. D:/NIOT/AUV_UPDATED/logs or D:/NIOT/AUV_UPDATED/log),
    then local script directory, and creates it if it doesn't exist.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(script_dir)
    # Check if parent or script folder already has 'logs' or 'log'
    for candidate in [
        os.path.join(parent_dir, "logs"),
        os.path.join(parent_dir, "log"),
        os.path.join(script_dir, "logs"),
        os.path.join(script_dir, "log")
    ]:
        if os.path.isdir(candidate):
            return candidate
            
    # Default to project root 'logs' if parent appears to be project root, else script_dir/'logs'
    if os.path.exists(os.path.join(parent_dir, "src")) or os.path.exists(os.path.join(parent_dir, "backend")):
        target = os.path.join(parent_dir, "logs")
    else:
        target = os.path.join(script_dir, "logs")
    os.makedirs(target, exist_ok=True)
    return target


class TelemetryDiskLogger(threading.Thread):
    """
    Continuous on-vehicle telemetry recorder logging complete vehicle state to timestamped CSV files.
    Ensures 100% of telemetry data, including active thruster PWM values, is preserved on the backend.
    Automatically creates the log directory if it does not exist, and generates a new timestamped file.
    """
    def __init__(self, state: VehicleState, stop: threading.Event, log_dir: str = None, rate_hz: float = 5.0):
        super().__init__(daemon=True, name="DiskLogger")
        self.state = state
        self.stop = stop
        if not log_dir:
            self.log_dir = get_default_log_dir()
        else:
            self.log_dir = os.path.abspath(log_dir)
        self.interval = 1.0 / max(1.0, min(20.0, rate_hz))
        
        # Ensure log folder exists (create if not exists)
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir, exist_ok=True)
            print(f"[LOGGER] Created telemetry log folder: {self.log_dir}")

        # Keep an unthrottled event stream alongside the periodic state snapshots.
        self.incoming_events = queue.SimpleQueue()
        self.state.incoming_log_queue = self.incoming_events
            
        # Generate new unique timestamped CSV file for this session
        timestamp_fn = time.strftime("sea_telemetry_%Y%m%d_%H%M%S.csv")
        self.log_file = os.path.join(self.log_dir, timestamp_fn)
        self.incoming_log_file = os.path.join(
            self.log_dir,
            time.strftime("sea_incoming_%Y%m%d_%H%M%S.csv")
        )

    def _flush_incoming_events(self, writer, file_handle) -> None:
        wrote_events = False
        while True:
            try:
                timestamp, source, payload = self.incoming_events.get_nowait()
            except queue.Empty:
                break
            timestamp_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
            timestamp_text += f".{int(timestamp * 1000) % 1000:03d}"
            writer.writerow((timestamp_text, source, json.dumps(payload, separators=(",", ":"), default=str)))
            wrote_events = True
        if wrote_events:
            file_handle.flush()

    def run(self) -> None:
        header = (
            "TIMESTAMP,UP_MS,ROLL,PITCH,YAW,LATITUDE,LONGITUDE,SATELLITES,"
            "CHAMBER_TEMP,CHAMBER_HUMIDITY,BMS_VOLT,BMS_CURR,BMS_POWER,BMS_SOC,BMS_SOH,BATT_TEMP,"
            "PWM_T1,PWM_T2,PWM_T3,LIGHT,CAMERA,FAILSAFE,"
            "MAG_X,MAG_Y,MAG_Z,MAG_B,SONAR_RANGE,SONAR_BEARING,SONAR_SCAN_ANGLE,"
            "OPERATING_MODE,IS_ARMED,NAV_DIR,TARGET_WP,DISTANCE_TO_TARGET,TARGET_BEARING,HEADING_ERROR,"
            "ACTIVE_STAB,STAB_MODE,CROSS_TRACK_M,STAB_CORR_DEG,HOLD_DISTANCE_M,"
            "FUSION_VALID,FUSION_RANGE_M,FUSION_BEARING_REL_DEG,FUSION_SIGMA_M,FUSION_SOURCES,FUSION_STAGE,FUSION_AGE_S,"
            "RECORDING,DOCKING,DOCK_CMD,DOCK_RANGE,DOCK_ERR,DOCK_HEADING,DOCK_MAG\n"
        )
        try:
            with open(self.incoming_log_file, "w", encoding="utf-8", newline="", buffering=1) as event_file:
                event_writer = csv.writer(event_file)
                event_writer.writerow(("TIMESTAMP", "SOURCE", "PAYLOAD_JSON"))
                with open(self.log_file, "w", encoding="utf-8", buffering=1) as f:
                    f.write(header)
                    f.flush()
                    print(f"[LOGGER] Backend vehicle logging active -> {self.log_file}")
                    print(f"[LOGGER] Incoming events logging active -> {self.incoming_log_file}")
                    while not self.stop.is_set():
                        t_iso = time.strftime("%Y-%m-%d %H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}"
                        with self.state.lock:
                            mode_lbl = "AUTO" if self.state.operating_mode == 1 else "MANUAL" if self.state.operating_mode == 0 else "CONFIG"
                            c_temp = f"{self.state.temp:.2f}" if self.state.temp is not None else "0.00"
                            c_hum = f"{self.state.hum:.2f}" if self.state.hum is not None else "0.00"
                            mx = f"{self.state.magX:.1f}" if self.state.magX is not None else "0.0"
                            my = f"{self.state.magY:.1f}" if self.state.magY is not None else "0.0"
                            mz = f"{self.state.magZ:.1f}" if self.state.magZ is not None else "0.0"
                            mb = f"{self.state.magB:.1f}" if self.state.magB is not None else "0.0"
                            sr = f"{self.state.sonar_range:.2f}" if self.state.sonar_range is not None else ""
                            sb = f"{self.state.sonar_bearing:.1f}" if self.state.sonar_bearing is not None else ""
                            up_ms = int((time.monotonic() - self.state.started) * 1000)
                            dk_r = f"{self.state.dock_range:.2f}" if self.state.dock_range is not None else ""
                            dk_e = f"{self.state.dock_err:.1f}" if self.state.dock_err is not None else ""
                            dk_h = f"{self.state.dock_heading:.1f}" if self.state.dock_heading is not None else ""
                            dk_m = f"{self.state.dock_mag:.2f}" if self.state.dock_mag is not None else ""
                            line = (
                                f"{t_iso},{up_ms},{self.state.roll:.2f},{self.state.pitch:.2f},{self.state.yaw:.2f},"
                                f"{self.state.latitude:.7f},{self.state.longitude:.7f},{self.state.no_of_satellites},"
                                f"{c_temp},{c_hum},{self.state.battery_voltage:.2f},{self.state.battery_current:.2f},"
                                f"{self.state.battery_power:.2f},{self.state.soc},{self.state.soh},{self.state.battery_temp:.1f},"
                                f"{self.state.t1},{self.state.t2},{self.state.t3},"
                                f"{self.state.light},{self.state.camera},{self.state.failsafe},"
                                f"{mx},{my},{mz},{mb},"
                                f"{sr},{sb},{self.state.sonar_scan_angle:.1f},"
                                f"{mode_lbl},{1 if self.state.is_armed else 0},{self.state.nav_direction},"
                                f"{self.state.current_wp_index + 1 if self.state.waypoints else 0},"
                                f"{self.state.distance_to_target:.2f},{self.state.target_bearing:.1f},{self.state.heading_error:.1f},"
                                f"{1 if self.state.active_stabilization else 0},{self.state.stabilization_mode},"
                                f"{self.state.cross_track_m:.3f},{self.state.stabilization_correction_deg:.2f},"
                                f"{self.state.stabilization_hold_distance_m:.3f},"
                                f"{1 if (self.state.fusion_valid and self.state.last_fusion_rx > 0.0 and time.monotonic() - self.state.last_fusion_rx <= FUSION_RX_TIMEOUT_S) else 0},"
                                f"{'' if self.state.fusion_range_m is None else f'{self.state.fusion_range_m:.3f}'},"
                                f"{'' if self.state.fusion_bearing_rel_deg is None else f'{self.state.fusion_bearing_rel_deg:.2f}'},"
                                f"{'' if self.state.fusion_sigma_m is None else f'{self.state.fusion_sigma_m:.3f}'},"
                                f"{self.state.fusion_sources},{self.state.fusion_stage},"
                                f"{'' if self.state.last_fusion_rx <= 0.0 else f'{max(0.0, time.monotonic() - self.state.last_fusion_rx):.2f}'},"
                                f"{1 if self.state.is_recording else 0},{1 if self.state.is_docking else 0},"
                                f"{self.state.dock_cmd},{dk_r},{dk_e},{dk_h},{dk_m}\n"
                            )
                        f.write(line)
                        f.flush()
                        self._flush_incoming_events(event_writer, event_file)
                        time.sleep(self.interval)
                    self._flush_incoming_events(event_writer, event_file)
            if self.state.incoming_log_queue is self.incoming_events:
                self.state.incoming_log_queue = None
        except Exception as e:
            print(f"[LOGGER] Backend logging error: {e}")


# =============================================================================
# DESKTOP SIMULATOR WORKER (Fallback / Testing Mode)
# =============================================================================

class SimulationWorker(threading.Thread):
    """Generates realistic live telemetry for desktop testing without physical sensors."""
    def __init__(self, state: VehicleState, stop: threading.Event):
        super().__init__(daemon=True, name="SimWorker")
        self.state = state
        self.stop = stop

    def run(self) -> None:
        print("[SIMULATOR] Synthetic sensor stream active (10 Hz)")
        t0 = time.monotonic()
        lat = 12.946428
        lon = 80.212103
        scan_deg = 0.0

        while not self.stop.is_set():
            now = time.monotonic()
            elapsed = now - t0

            # Gentle roll and pitch motion
            roll = round(math.sin(elapsed * 0.8) * 1.8, 2)
            pitch = round(math.cos(elapsed * 0.6) * 1.2, 2)

            with self.state.lock:
                # Update AHRS if not receiving real hardware
                if now - self.state.last_ahrs_rx > 1.5:
                    self.state.roll = roll
                    self.state.pitch = pitch
                    if self.state.operating_mode == 1:
                        # Gradually rotate heading in auto mode
                        self.state.yaw = round((self.state.yaw + 0.5) % 360.0, 2)
                    else:
                        self.state.yaw = round((math.sin(elapsed * 0.15) * 20.0 + 85.0) % 360.0, 2)

                # Update GPS if not receiving real hardware
                if now - self.state.last_gps_rx > 1.5:
                    self.state.latitude = round(lat + math.sin(elapsed * 0.05) * 0.0002, 7)
                    self.state.longitude = round(lon + math.cos(elapsed * 0.05) * 0.0002, 7)
                    self.state.no_of_satellites = 11
                    self.state.altitude = 4.50
                    self.state.hdop = 2.35
                    self.state.speed_kmph = 0.35
                    self.state.course_deg = round(self.state.yaw, 2)
                    self.state.gps_status = "FIX"
                    self.state.gps_valid = 1
                    self.state.utc = time.strftime("%H:%M:%S")

                # Update Teensy env & RM3100 if not receiving real hardware
                if now - self.state.last_teensy_rx > 1.5:
                    self.state.temp = round(31.0 + math.sin(elapsed * 0.1) * 0.4, 2)
                    self.state.hum = round(73.1 + math.cos(elapsed * 0.1) * 1.5, 2)
                    self.state.valid = 15
                    mx = -0.026
                    my = -0.026
                    mz = -0.026
                    self.state.magX = mx
                    self.state.magY = my
                    self.state.magZ = mz
                    self.state.magB = 0.045

                # Update BMS if not receiving real hardware
                if self.state.battery_voltage == 0.0:
                    self.state.battery_voltage = round(49.8 - ((elapsed * 0.002) % 6.0), 2)
                    self.state.battery_current = 2.45
                    self.state.battery_power = round(self.state.battery_voltage * self.state.battery_current, 2)
                    self.state.battery_temp = 26.8
                    self.state.soc = max(10, min(100, int(92 - (elapsed * 0.05) % 80)))
                    self.state.soh = 98

                # Update Sonar if not receiving real hardware
                # Sweep only the vessel's active 180 deg - 360 deg sector, matching
                # the live Ping360 link's SONAR_SCAN_START_GRAD/END_GRAD limits.
                if self.state.sonar_enabled:
                    scan_deg = 180.0 + ((scan_deg - 180.0 + 4.0) % 180.0)
                    self.state.sonar_scan_angle = round(scan_deg, 1)
                    self.state.sonar_range = round(4.2 + math.sin(elapsed * 0.3) * 1.8, 2)
                    self.state.sonar_bearing = round((self.state.yaw + 12.0) % 360.0, 1)
                    self.state.last_sonar_rx = now
                elif now - self.state.last_sonar_rx > 4.0:
                    self.state.sonar_scan_angle = 0.0
                    self.state.sonar_range = None
                    self.state.sonar_bearing = None

            time.sleep(0.1)


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="sea_backend: Autonomous Waypoint Navigation & Telemetry Backend")
    parser.add_argument("--teensy", default=DEFAULT_TEENSY_PORT, help="Teensy 4.1 USB port")
    parser.add_argument("--teensy-baud", type=int, default=DEFAULT_TEENSY_BAUD)
    parser.add_argument("--ahrs", default=DEFAULT_AHRS_PORT, help="AHRS serial port")
    parser.add_argument("--ahrs-baud", type=int, default=DEFAULT_AHRS_BAUD)
    # Removed heading bypasses: still accepted so old launch scripts/services
    # keep starting, but IGNORED - heading_calibration.json is the only source.
    parser.add_argument("--no-calibrate-heading", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--heading-offset", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--invert-yaw-cmd", action="store_true", help="Reverse T2 yaw command direction (use if AUV turns the wrong way)")
    parser.add_argument("--can", default=DEFAULT_CAN_IFACE, help="SocketCAN interface")
    parser.add_argument("--sonar-ip", default=DEFAULT_SONAR_IP, help="Ping360 UDP IP address (default: 192.168.1.20)")
    parser.add_argument("--sonar-port", type=int, default=DEFAULT_SONAR_PORT, help="Ping360 UDP port (default: 12345)")
    parser.add_argument("--sonar-start-grad", type=int, default=DEFAULT_SONAR_START_GRAD, help="Ping360 sector start grad (default: 200 = 180°)")
    parser.add_argument("--sonar-stop-grad", type=int, default=DEFAULT_SONAR_STOP_GRAD, help="Ping360 sector stop grad (default: 399 = 359.1°)")
    parser.add_argument("--sonar-gain", type=int, default=DEFAULT_SONAR_GAIN, choices=[0, 1, 2], help="Ping360 gain: 0=low, 1=medium, 2=high (default: 1)")
    parser.add_argument("--no-sonar", action="store_true", help="Disable Ping360 scanning sonar communication")
    parser.add_argument("--p8", default=DEFAULT_RF_PORT, help="Surface RF UART port")
    parser.add_argument("--p8-baud", type=int, default=DEFAULT_RF_BAUD)
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT, help="TCP Server port for GCS UI")
    parser.add_argument("--telemetry-hz", type=float, default=4.0, help="Telemetry broadcast frequency (1-10 Hz)")
    parser.add_argument("--format", default="csv", choices=["json", "csv", "teensy", "both", "all"], help="Telemetry link format: json, csv, teensy, both, all (default: csv)")
    parser.add_argument("--simulate", "--sim", action="store_true", dest="simulate", help="Run simulated telemetry for testing without physical sensors")
    parser.add_argument("--log-dir", default=None, help="Directory path to save CSV telemetry logs on backend (default: automatic project logs/ folder)")
    parser.add_argument("--mission-checkpoint", default=None, help="On-vehicle durable mission checkpoint JSON path")
    parser.add_argument("--no-log", action="store_true", help="Disable CSV disk logging on backend")
    parser.add_argument("--angle-threshold", type=float, default=DEFAULT_ANGLE_THRESHOLD, help="Turn-to-target threshold (degrees)")
    parser.add_argument("--distance-threshold", type=float, default=DEFAULT_DISTANCE_THRESHOLD, help="Waypoint arrival threshold (meters)")
    parser.add_argument("--rtsp-url", default=DEFAULT_RTSP_URL, help="Camera RTSP stream URL for MP4 video recording")
    parser.add_argument("--rec-dir", default=None, help="Directory path to save camera MP4 recordings on backend (default: recordings/)")

    args = parser.parse_args()

    global AHRS_HEADING_OFFSET_DEG, HEADING_CALIBRATED, HEADING_CAL_PATH, YAW_CMD_SIGN
    # Keep heading calibration beside the backend code so changing --log-dir or
    # rotating telemetry folders cannot make the persistent offset disappear.
    # If an older calibration exists in the legacy log folder, load it once and
    # migrate it to the stable path.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    HEADING_CAL_PATH = os.path.join(script_dir, "heading_calibration.json")
    legacy_heading_cal_path = os.path.join(args.log_dir or get_default_log_dir(), "heading_calibration.json")
    if not os.path.exists(HEADING_CAL_PATH) and os.path.exists(legacy_heading_cal_path):
        legacy_offset = load_heading_calibration(legacy_heading_cal_path)
        if legacy_offset is not None:
            try:
                with open(legacy_heading_cal_path, "r", encoding="utf-8") as src_fh:
                    legacy_record = json.load(src_fh)
                with open(HEADING_CAL_PATH + ".tmp", "w", encoding="utf-8") as dst_fh:
                    json.dump(legacy_record, dst_fh, indent=2)
                    dst_fh.flush()
                    os.fsync(dst_fh.fileno())
                os.replace(HEADING_CAL_PATH + ".tmp", HEADING_CAL_PATH)
                print(f"[AHRS] Migrated legacy heading calibration to {HEADING_CAL_PATH}")
            except OSError as exc:
                print(f"[AHRS] WARNING: could not migrate legacy heading calibration: {exc}")
    if args.heading_offset is not None:
        print("[AHRS] WARNING: --heading-offset is no longer supported and is IGNORED "
              "(heading_calibration.json is the only source). Remove it from your start command.")
    if args.no_calibrate_heading:
        print("[AHRS] WARNING: --no-calibrate-heading is no longer supported and is IGNORED. "
              "Remove it from your start command.")
    saved_offset = load_heading_calibration(HEADING_CAL_PATH)
    if saved_offset is not None:
        AHRS_HEADING_OFFSET_DEG = saved_offset
        HEADING_CALIBRATED = True
        print(f"[AHRS] Heading offset loaded from {HEADING_CAL_PATH}: {AHRS_HEADING_OFFSET_DEG:.2f} deg")
    else:
        print(f"[AHRS] *** HEADING NOT CALIBRATED *** no file at {HEADING_CAL_PATH}. "
              "Calibrate from the GCS (AHRS Heading Calibration) or send $CALHEADING,<deg>.")
    if args.invert_yaw_cmd:
        YAW_CMD_SIGN = -YAW_CMD_SIGN
    print(f"[AUTONAV] Pure Pursuit lookahead {PURE_PURSUIT_LOOKAHEAD_M} m, yaw sign {YAW_CMD_SIGN:+.0f}, "
          f"min yaw PWM {YAW_MIN_PWM}")

    stop = threading.Event()
    state = VehicleState()
    checkpoint_path = args.mission_checkpoint or os.path.join(args.log_dir or get_default_log_dir(), "mission_checkpoint.json")
    checkpoint = MissionCheckpoint(checkpoint_path)
    recovered = checkpoint.load()
    if recovered and recovered["status"] in ("RUNNING", "PAUSED") and recovered["active_index"] < len(recovered["route"]):
        # After a process restart, restore the route but NEVER restart motors
        # automatically. Operator must reconnect, ARM, and explicitly RESUME.
        state.waypoints = [tuple(w) for w in recovered["route"]]
        state.current_wp_index = recovered["active_index"]
        state.mission_paused = True
        state.mission_recovered = True
        state.mission_pause_reason = "RECOVERED_AFTER_RESTART"
        state.nav_direction = "RECOVERED_AWAITING_OPERATOR"
        state.start_run_mode = 1
        state.operating_mode = 1
        print(f"[MISSION] Restored waypoint {state.current_wp_index + 1}/{len(state.waypoints)}; ARM + RESUME required")

    # Hardware worker links
    teensy = TeensyLink(args.teensy, args.teensy_baud, state, stop)
    ahrs = AhrsLink(args.ahrs, args.ahrs_baud, state, stop, calibrate=True)
    bms = BmsLink(args.can, state, stop)

    # RTSP Camera Recorder (matching camera_rec.py on Jetson)
    recorder = CameraRecorder(state, output_dir=args.rec_dir, default_rtsp=args.rtsp_url, teensy=teensy)

    # Ping360 Scanning Sonar link (connecting to 192.168.1.20:12345)
    sonar = None
    if not args.no_sonar:
        sonar = Ping360Link(
            state=state,
            stop=stop,
            ip=args.sonar_ip,
            port=args.sonar_port,
            start_grad=args.sonar_start_grad,
            stop_grad=args.sonar_stop_grad,
            gain=args.sonar_gain,
            surface_emit_log=None
        )

    # Surface UI link
    surface = SurfaceLink(
        state=state,
        teensy=teensy,
        stop=stop,
        recorder=recorder,
        sonar=sonar,
        port=args.p8,
        baud=args.p8_baud,
        telemetry_hz=args.telemetry_hz,
        tcp_port=args.tcp_port,
        telemetry_format=args.format,
        checkpoint=checkpoint
    )

    if sonar:
        sonar.emit_log = surface.queue_log

    # Autonomous navigation engine
    auto_engine = AutoNavigationEngine(
        state=state,
        teensy=teensy,
        stop=stop,
        surface_emit_log=surface.queue_log,
        angle_threshold=args.angle_threshold,
        distance_threshold=args.distance_threshold,
        checkpoint=checkpoint
    )

    # Autonomous magnetic docking guidance engine
    dock_engine = MagneticDockingEngine(
        state=state,
        teensy=teensy,
        stop=stop,
        surface_emit_log=surface.queue_log
    )

    # Backend CSV logger
    disk_logger = None
    if not args.no_log:
        disk_logger = TelemetryDiskLogger(state, stop, log_dir=args.log_dir, rate_hz=max(1.0, args.telemetry_hz))

    def request_stop(_sig, _frame):
        print("\n[sea_backend] Stopping all threads, video recording, and neutralizing thrusters...")
        with state.lock:
            state.is_docking = False
            state.start_run_mode = 0
            state.operating_mode = 0
            state.t1 = state.t2 = state.t3 = NEUTRAL_PWM
            teensy.send_command(NEUTRAL_PWM, NEUTRAL_PWM, NEUTRAL_PWM, state.light, state.camera)
        stop.set()
        recorder.stop_recording()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    # Start all background workers
    teensy.start()
    ahrs.start()
    bms.start()
    if sonar:
        sonar.start()
    auto_engine.start()
    dock_engine.start()
    if disk_logger:
        disk_logger.start()

    # Optional simulator for testing
    sim_worker = None
    if args.simulate:
        sim_worker = SimulationWorker(state, stop)
        sim_worker.start()

    # Start TCP server in background for network connections
    threading.Thread(target=surface.run_tcp_server, daemon=True, name="TcpServer").start()

    print("[sea_backend] All autonomous navigation & telemetry services started.")
    print(f"[sea_backend] Telemetry format: {args.format.upper()} | Listening on RF {args.p8} @ {args.p8_baud} and TCP port {args.tcp_port}")
    if disk_logger:
        print(f"[sea_backend] Logging all sensor & thruster data to: {disk_logger.log_file}")

    # Run serial surface link on main thread
    try:
        surface.run_serial()
    except KeyboardInterrupt:
        pass

    stop.set()
    auto_engine.join(timeout=1.0)
    dock_engine.join(timeout=1.0)
    for worker in (teensy, ahrs, bms):
        worker.join(timeout=1.0)
    if sonar:
        sonar.join(timeout=1.0)
    if disk_logger:
        disk_logger.join(timeout=1.0)
    if sim_worker:
        sim_worker.join(timeout=1.0)
    print("[sea_backend] Shutdown complete.")


if __name__ == "__main__":
    main()
