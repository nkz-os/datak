#!/usr/bin/env python3
"""
DaTaK Test Data Injector
Simulate sensor data for tilt, piranometer, and temperature.
"""

import time
import json
import math
import random
import argparse
import datetime
import paho.mqtt.client as mqtt

# Configuration for Latitude 44°N (Northern Hemisphere)
LATITUDE = 44.0
SOLAR_CONSTANT = 1361.0  # W/m2

def calculate_solar_position(dt, lat):
    """
    Calculate solar elevation and azimuth.
    Simplified version for testing.
    """
    # Day of year
    day_of_year = dt.timetuple().tm_yday
    
    # Declination angle (delta)
    delta = 23.45 * math.sin(math.radians(360/365 * (day_of_year - 81)))
    
    # Hour angle (H) - 12:00 is 0, 15 degrees per hour
    # Local solar time approximation
    solar_noon = 12.0
    hour = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    h_angle = (hour - solar_noon) * 15.0
    
    # Elevation (alpha)
    phi = math.radians(lat)
    delta_rad = math.radians(delta)
    h_rad = math.radians(h_angle)
    
    sin_alpha = math.sin(phi) * math.sin(delta_rad) + math.cos(phi) * math.cos(delta_rad) * math.cos(h_rad)
    alpha = math.degrees(math.asin(max(-1.0, min(1.0, sin_alpha))))
    
    return alpha

def get_tilt(dt):
    """
    Simulate tilt tracking the sun elevation.
    If sun is below horizon, stay at 0 or park at night.
    """
    elevation = calculate_solar_position(dt, LATITUDE)
    # Solar trackers usually have limits (e.g., -45 to 45 or 0 to 60)
    # The user mentioned -45 to 45 earlier.
    # If we track the sun elevation directly:
    tilt = max(-45, min(45, elevation))
    return tilt

def get_insolation(dt):
    """Simulate solar radiation based on elevation."""
    elevation = calculate_solar_position(dt, LATITUDE)
    if elevation <= 0:
        return 0.0
    
    # Simple model: I = I0 * sin(elevation) * atmospheric_loss
    # Atmospheric loss is roughly 0.7 at sea level
    val = SOLAR_CONSTANT * math.sin(math.radians(elevation)) * 0.75
    return min(1200.0, val)

def get_temperature(dt):
    """Simulate temperature following the solar cycle."""
    elevation = calculate_solar_position(dt, LATITUDE)
    # Base 15C, peak after solar noon
    base = 15.0
    peak = 15.0 * max(0, math.sin(math.radians(elevation)))
    return base + peak + random.uniform(-0.5, 0.5)

def main():
    parser = argparse.ArgumentParser(description="DaTaK MQTT Data Injector")
    parser.add_argument("--host", default="100.95.129.22", help="MQTT Broker host")
    parser.add_argument("--port", type=int, default=1883, help="MQTT Broker port")
    parser.add_argument("--interval", type=float, default=2.0, help="Publish interval in seconds")
    parser.add_argument("--accel", type=float, default=1.0, help="Time acceleration factor (default 1.0)")
    args = parser.parse_args()

    client = mqtt.Client()
    
    print(f"Connecting to MQTT broker at {args.host}:{args.port}...")
    try:
        client.connect(args.host, args.port, 60)
    except Exception as e:
        print(f"Error connecting to broker: {e}")
        return

    client.loop_start()

    # Start from current time
    sim_time = datetime.datetime.now()
    
    try:
        while True:
            tilt = get_tilt(sim_time)
            insolation = get_insolation(sim_time)
            temp = get_temperature(sim_time)

            # Publish Tilt
            client.publish("sensors/tilt", f"{tilt:.2f}")
            # Publish Insolation (Piranometer)
            client.publish("sensors/insolation", f"{insolation:.2f}")
            # Publish Temperature
            client.publish("sensors/temperature", f"{temp:.2f}")

            print(f"[{sim_time.strftime('%Y-%m-%d %H:%M:%S')}] Pushed -> Tilt: {tilt:.2f}°, Insolation: {insolation:.2f} W/m2, Temp: {temp:.2f}°C")
            
            time.sleep(args.interval)
            # Advance simulation time
            sim_time += datetime.timedelta(seconds=args.interval * args.accel)

    except KeyboardInterrupt:
        print("\nStopping injector...")
    finally:
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()
