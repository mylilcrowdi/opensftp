#!/usr/bin/env python3
"""Resource monitor: samples CPU, RAM, temperature every second and writes a report."""
import psutil
import time
import json
import sys
import os
import signal
import subprocess
from datetime import datetime

samples = []
start_time = time.time()

def get_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000
    except Exception:
        return None

def sample():
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    temp = get_temp()
    elapsed = round(time.time() - start_time, 1)
    return {
        "t": elapsed,
        "cpu_pct": cpu,
        "ram_used_mb": round(mem.used / 1024 / 1024, 1),
        "ram_total_mb": round(mem.total / 1024 / 1024, 1),
        "ram_pct": mem.percent,
        "temp_c": temp,
    }

def monitor(output_file):
    psutil.cpu_percent(interval=None)  # warm up
    print(f"[monitor] Starting resource sampling → {output_file}", flush=True)
    try:
        while True:
            s = sample()
            samples.append(s)
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        write_report(output_file)

def write_report(output_file):
    if not samples:
        return
    cpu_vals = [s["cpu_pct"] for s in samples]
    ram_vals = [s["ram_used_mb"] for s in samples]
    temp_vals = [s["temp_c"] for s in samples if s["temp_c"] is not None]

    report = {
        "duration_s": samples[-1]["t"] if samples else 0,
        "samples": len(samples),
        "cpu": {
            "avg": round(sum(cpu_vals) / len(cpu_vals), 1),
            "max": max(cpu_vals),
            "min": min(cpu_vals),
        },
        "ram_mb": {
            "avg": round(sum(ram_vals) / len(ram_vals), 1),
            "max": max(ram_vals),
            "min": min(ram_vals),
            "total": samples[0]["ram_total_mb"],
        },
        "temp_c": {
            "avg": round(sum(temp_vals) / len(temp_vals), 1) if temp_vals else None,
            "max": max(temp_vals) if temp_vals else None,
        },
        "timeline": samples,
    }
    with open(output_file, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n[monitor] Report saved to {output_file}", flush=True)

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "resource_report.json"
    monitor(out)
