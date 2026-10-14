"""Hardware-free regression tests for transient GPS loss in AUTO."""
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sea_backend as auv


class Clock:
    def __init__(self):
        self.now = 100.0
    def monotonic(self):
        return self.now
    def advance(self, seconds):
        self.now += seconds


class FakeTeensy:
    def __init__(self):
        self.commands = []
        self.sends_work = True
    def send_command(self, *args, **kwargs):
        vals = (kwargs.get('fwd'), kwargs.get('lat'), kwargs.get('vert')) if kwargs else args[:3]
        self.commands.append(tuple(vals))
        return self.sends_work


class GpsLossTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.p_monotonic = patch.object(auv.time, 'monotonic', self.clock.monotonic)
        self.p_sleep = patch.object(auv.time, 'sleep', lambda secs: None)
        self.p_monotonic.start()
        self.p_sleep.start()
        self.addCleanup(self.p_sleep.stop)
        self.addCleanup(self.p_monotonic.stop)
        self.state = auv.VehicleState()
        self.teensy = FakeTeensy()
        self.logs = []
        self.engine = auv.AutoNavigationEngine(
            self.state, self.teensy, threading.Event(), self.logs.append
        )
        self.state.operating_mode = 1
        self.state.start_run_mode = 1
        self.state.is_armed = True
        self.state.waypoints = [(13.001, 80.0)]  # >100 m north of test position
        self.state.latitude = 13.0
        self.state.longitude = 80.0
        self.state.gps_valid = 1
        self.state.no_of_satellites = 11
        self.state.last_gps_rx = self.clock.now
        self.state.last_good_gps_speed_kmph = 1.8  # 0.5 m/s
        self.state.speed_kmph = 1.8
        self.state.yaw = 0.0
        self.state.last_ahrs_rx = self.clock.now
        self.state.last_command_rx = self.clock.now

    def advance_with_ahrs(self, seconds):
        self.clock.advance(seconds)
        self.state.last_ahrs_rx = self.clock.now

    def establish(self):
        self.engine._execute_auto_step()
        self.assertTrue(self.engine._gps_navigation_established)
        self.assertFalse(self.state.mission_paused)

    def dropout(self):
        self.state.gps_valid = 0
        self.state.no_of_satellites = 0

    def test_temporary_gps_loss_keeps_current_waypoint_and_thrust(self):
        self.establish()
        self.dropout()
        self.advance_with_ahrs(0.3)
        self.engine._execute_auto_step()
        self.assertFalse(self.state.mission_paused)
        self.assertTrue(self.state.gps_bridge_active)
        self.assertEqual(self.state.current_wp_index, 0)
        self.assertTrue(self.state.nav_direction.startswith('GPS_BRIDGE_'))
        self.assertGreater(self.teensy.commands[-1][0], auv.NEUTRAL_PWM)
        self.assertLessEqual(self.teensy.commands[-1][0], auv.NEUTRAL_PWM + auv.GPS_BRIDGE_MAX_SURGE_PWM)

    def test_reconnected_gps_returns_to_normal_navigation_without_resume(self):
        self.establish()
        self.dropout()
        self.advance_with_ahrs(0.5)
        self.engine._execute_auto_step()
        self.state.gps_valid = 1
        self.state.no_of_satellites = 9
        self.state.last_gps_rx = self.clock.now
        self.engine._execute_auto_step()  # first fix, still in bridge mode
        self.assertTrue(self.state.gps_bridge_active)
        self.advance_with_ahrs(0.6)
        self.state.last_gps_rx = self.clock.now
        self.engine._execute_auto_step()  # stable fix, switch to live GPS
        self.assertFalse(self.state.mission_paused)
        self.assertFalse(self.state.gps_bridge_active)
        self.assertTrue(self.state.nav_direction.startswith('PP_'))
        self.assertTrue(any('GPS FIX RESTORED' in l for l in self.logs))

    def test_long_gps_loss_pauses_then_auto_resumes_without_gcs(self):
        self.establish()
        self.dropout()
        self.advance_with_ahrs(0.5)
        self.engine._execute_auto_step()
        self.advance_with_ahrs(6.0)
        self.engine._execute_auto_step()
        self.assertTrue(self.state.mission_paused)
        self.assertEqual(self.state.mission_pause_reason, 'GPS_OUTAGE_EXCEEDED')
        self.assertFalse(self.state.gps_bridge_active)
        self.assertEqual(self.teensy.commands[-1], (1500, 1500, 1500))
        # No GCS heartbeat for >6 s: recovery should still work for GPS-only pause.
        self.state.gps_valid = 1
        self.state.no_of_satellites = 10
        self.state.last_gps_rx = self.clock.now
        self.engine._pause_step()
        self.assertTrue(self.state.mission_paused)
        self.advance_with_ahrs(0.7)
        self.state.last_gps_rx = self.clock.now
        self.engine._pause_step()
        self.assertFalse(self.state.mission_paused)
        self.assertEqual(self.state.current_wp_index, 0)
        self.assertTrue(any('AUTOMATIC GPS RECOVERY' in l for l in self.logs))

    def test_ahrs_failure_still_pauses_mission(self):
        self.establish()
        self.dropout()
        self.clock.advance(2.5)
        self.engine._execute_auto_step()
        self.assertTrue(self.state.mission_paused)
        self.assertEqual(self.state.mission_pause_reason, 'AHRS_STALE')
        self.assertEqual(self.teensy.commands[-1], (1500, 1500, 1500))

    def test_docking_refuses_gps_bridge(self):
        self.establish()
        self.dropout()
        self.state.is_docking = True
        self.state.gps_docking_active = True
        self.advance_with_ahrs(0.2)
        self.engine._execute_auto_step()
        self.assertTrue(self.state.mission_paused)
        self.assertEqual(self.state.mission_pause_reason, 'GPS_REQUIRED_FOR_DOCKING')

    def test_no_gps_navigation_history_does_not_blind_start(self):
        self.dropout()
        self.advance_with_ahrs(0.3)
        self.engine._execute_auto_step()
        self.assertTrue(self.state.mission_paused)
        self.assertEqual(self.state.mission_pause_reason, 'GPS_REQUIRED_AT_START')

    def test_near_waypoint_does_not_drive_blindly_or_capture_it(self):
        self.state.waypoints = [(13.000031, 80.0)]  # ~3.45 m away
        self.establish()
        self.dropout()
        self.advance_with_ahrs(0.3)
        self.engine._execute_auto_step()
        self.assertTrue(self.state.mission_paused)
        self.assertEqual(self.state.mission_pause_reason, 'GPS_BRIDGE_NEAR_WAYPOINT')
        self.assertEqual(self.state.current_wp_index, 0)
        self.assertEqual(self.teensy.commands[-1], (1500, 1500, 1500))

    def test_estimated_bridge_travel_limit_remains_effective(self):
        self.establish()
        self.dropout()
        self.advance_with_ahrs(3.0) # 1.5 m DR, within the 2 m estimate limit
        self.engine._execute_auto_step()
        self.assertFalse(self.state.mission_paused)
        self.advance_with_ahrs(1.2) # 2.1 m DR total, exceeds estimate limit
        self.engine._execute_auto_step()
        self.assertTrue(self.state.mission_paused)
        self.assertEqual(self.state.mission_pause_reason, 'GPS_BRIDGE_DISTANCE_LIMIT')
        self.assertEqual(self.teensy.commands[-1], (1500, 1500, 1500))

    def test_gps_jump_requires_operator_inspection(self):
        self.establish()
        self.dropout()
        self.advance_with_ahrs(0.5)
        self.engine._execute_auto_step()
        self.state.gps_valid = 1
        self.state.no_of_satellites = 10
        self.state.latitude += 0.0005  # ~55 m jump, implausible over 0.5 s
        self.state.last_gps_rx = self.clock.now
        self.engine._execute_auto_step()
        self.advance_with_ahrs(0.6)
        self.state.last_gps_rx = self.clock.now
        self.engine._execute_auto_step()
        self.assertTrue(self.state.mission_paused)
        self.assertEqual(self.state.mission_pause_reason, 'GPS_REACQUIRE_POSITION_JUMP')
        self.advance_with_ahrs(1.0)
        self.state.last_gps_rx = self.clock.now
        self.engine._pause_step()
        self.assertTrue(self.state.mission_paused)

    def test_teensy_command_error_still_requires_operator(self):
        self.establish()
        self.teensy.sends_work = False
        self.advance_with_ahrs(0.1)
        self.state.last_gps_rx = self.clock.now
        self.engine._execute_auto_step()
        self.assertTrue(self.state.mission_paused)
        self.assertEqual(self.state.mission_pause_reason, 'TEENSY_COMMAND_FAILED')
        self.assertTrue(self.engine._neutral_unconfirmed)

    def test_arm_start_stop_uses_original_ui_protocol(self):
        self.state.operating_mode = 0
        self.state.start_run_mode = 0
        self.state.is_armed = False
        self.state.waypoints = []
        link = auv.SurfaceLink(self.state, self.teensy, threading.Event())
        replies = []
        for command in ('$WP,0,13.0010000,80.0000000', '$WP,END', '$ARM', '$START'):
            link.handle_command_line(command, replies.append)
        self.assertIn(b'ACK,WP\n', replies)
        self.assertIn(b'ACK,ARM\n', replies)
        self.assertIn(b'ACK,START\n', replies)
        self.assertTrue(self.state.is_armed)
        self.assertEqual(self.state.operating_mode, 1)
        self.assertEqual(self.state.start_run_mode, 1)
        link.handle_command_line('$STOP', replies.append)
        self.assertIn(b'ACK,STOP\n', replies)
        self.assertEqual(self.state.start_run_mode, 0)

    def test_invalid_teensy_packet_preserves_last_good_fix_and_timestamp(self):
        self.state.operating_mode = 0
        fields = [
            '25', '70', '1500', '1500', '1500', '0', '0', '0', '15', '123',
            'FIX', '13.1234567', '80.1234567', '0', '11', '1.2', '1.8',
            '0', '12:34:56', '1', '2', '3', '4',
        ]
        self.state.update_teensy(fields)
        gps_fix = (self.state.latitude, self.state.longitude)
        last_good = self.state.last_gps_rx
        self.clock.advance(0.3)
        fields[8], fields[10], fields[11], fields[12], fields[14] = '7', 'NO_FIX', '0.0', '0.0', '0'
        fields[16] = '0.0'  # do not overwrite the last_good_gps_speed_kmph
        self.state.update_teensy(fields)
        self.assertEqual(self.state.gps_valid, 0)
        self.assertEqual((self.state.latitude, self.state.longitude), gps_fix)
        self.assertEqual(self.state.last_gps_rx, last_good)
        self.assertAlmostEqual(self.state.last_good_gps_speed_kmph, 1.8)

    def test_explicit_no_fix_wins_over_stale_valid_mask(self):
        fields = [
            '25', '70', '1500', '1500', '1500', '0', '0', '0', '15', '123',
            'FIX', '13.1234567', '80.1234567', '0', '11', '1.2', '1.8',
            '0', '12:34:56', '1', '2', '3', '4',
        ]
        self.state.update_teensy(fields)
        fix_timestamp = self.state.last_gps_rx
        fields[10] = 'NO_FIX'  # Some Teensy versions may leave VALID bit 3 set.
        fields[11], fields[12] = '0.0', '0.0'
        self.clock.advance(0.1)
        self.state.update_teensy(fields)
        self.assertEqual(self.state.gps_valid, 0)
        self.assertEqual(self.state.last_gps_rx, fix_timestamp)

    def test_all_three_ui_telemetry_formats_report_no_fix_during_bridge(self):
        self.establish()
        self.dropout()
        self.advance_with_ahrs(0.1)
        self.engine._execute_auto_step()
        self.assertTrue(self.state.gps_bridge_active)
        internal_position = (self.state.latitude, self.state.longitude)
        payload = json.loads(self.state.json_packet())
        self.assertEqual(payload['gps_valid'], 0)
        self.assertEqual(payload['latitude'], 0.0)
        self.assertEqual(payload['longitude'], 0.0)
        self.assertEqual(payload['gps_bridge_active'], 1)
        self.assertAlmostEqual(payload['last_known_gps_latitude'], internal_position[0])
        self.assertAlmostEqual(payload['last_known_gps_longitude'], internal_position[1])
        packet_csv = self.state.csv_packet().decode().strip().split(',')
        self.assertEqual(len(packet_csv), 25)
        self.assertEqual([float(v) for v in packet_csv[3:6]], [0.0, 0.0, 0.0])
        packet_teensy = self.state.teensy_packet().decode().strip().split(',')
        self.assertEqual(len(packet_teensy), 23)
        self.assertEqual(packet_teensy[10], 'NO_FIX')
        self.assertEqual([float(v) for v in packet_teensy[11:13]], [0.0, 0.0])
        self.assertEqual(int(packet_teensy[8]) & 8, 0)
        self.assertEqual((self.state.latitude, self.state.longitude), internal_position)

    def test_ui_arm_disarm_ack_unchanged(self):
        self.state.start_run_mode = 0
        self.state.operating_mode = 0
        self.state.is_armed = False
        link = auv.SurfaceLink(self.state, self.teensy, threading.Event())
        replies = []
        link.handle_command_line('$ARM', replies.append)
        self.assertIn(b'ACK,ARM\n', replies)
        self.assertTrue(self.state.is_armed)
        link.handle_command_line('$DISARM', replies.append)
        self.assertIn(b'ACK,DISARM\n', replies)
        self.assertFalse(self.state.is_armed)


if __name__ == '__main__':
    unittest.main(verbosity=2)
