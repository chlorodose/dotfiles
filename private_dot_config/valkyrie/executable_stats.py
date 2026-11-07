#!/usr/bin/python3
"""Read-only host telemetry. One sampler feeds both Eww instances; no dependencies."""
import argparse
import json
import os
import re
import signal
import subprocess
import time
from collections import deque
from pathlib import Path

CACHE = Path.home() / '.cache/valkyrie'
CACHE.mkdir(parents=True, exist_ok=True)
PREV = {}
GPU_LOAD_CONFIRM_SAMPLES = 3


def read(path, default=''):
    try:
        return Path(path).read_text().strip()
    except (OSError, ValueError):
        return default


def number(path, default=None):
    try:
        return float(read(path))
    except ValueError:
        return default


def text(value, suffix='', decimals=0):
    return 'N/A' if value is None else f'{value:.{decimals}f}{suffix}'


def pct(value):
    return max(0, min(100, value))


def gib(value):
    return value / 1024**3


def delta(key, value, elapsed):
    old = PREV.get(key)
    PREV[key] = value
    return max(0, value - old) / elapsed if old is not None and elapsed > 0 else 0


def add_health(out, *, previous_gpu_loads=(), cpu_temp=None, gpu_temp=None,
               dimm_temp=None, disk_temp=None):
    """Keep warning thresholds and severity shared by both panels.

    Utilization is a capacity warning, not a hardware fault. Missing temperature
    sensors stay neutral; they must not be interpreted as overheating.
    """
    # A load threshold must hold for every sample in the window. The minimum
    # lets alerts clear immediately and requires separate confirmation for red.
    gpu_loads = (*previous_gpu_loads, out['gpu_pct'])
    confirmed_gpu_load = min(gpu_loads) if len(gpu_loads) == GPU_LOAD_CONFIRM_SAMPLES else None
    checks = [
        ('cpu', 'cpu_load', out['cpu_pct'], 80, 95, 'CPU LOAD'),
        ('cpu', 'cpu_temp', cpu_temp, 80, 90, 'CPU TEMP'),
        ('gpu', 'gpu_load', confirmed_gpu_load, 80, 95, 'GPU LOAD'),
        ('gpu', 'gpu_temp', gpu_temp, 78, 85, 'GPU TEMP'),
        ('gpu', 'gpu_vram', out['gpu_vram_pct'], 80, 95, 'VRAM'),
        ('memory', 'ram', out['ram_pct'], 80, 95, 'RAM'),
        ('memory', 'swap', out['swap_pct'], 80, 95, 'SWAP'),
        ('memory', 'dimm_temp', dimm_temp, 70, 80, 'DIMM TEMP'),
        ('storage', 'var', out['var_pct'], 80, 90, '/VAR'),
        ('storage', 'disk_temp', disk_temp, 65, 75, 'DISK TEMP'),
    ]
    alerts = []
    classes = ('ok', 'warn', 'hot')
    for group in ('cpu', 'gpu', 'memory', 'storage'):
        out[f'{group}_class'] = 'ok'
        out[f'{group}_status'] = 'NORMAL'
    for group, metric, value, warning, critical, label in checks:
        severity = 0 if value is None else 2 if value >= critical else 1 if value >= warning else 0
        out[f'{metric}_class'] = classes[severity]
        if severity:
            alerts.append((severity, group, label))
    # Show the most severe condition first, retaining stable order for ties.
    alerts.sort(key=lambda alert: alert[0], reverse=True)
    for group in ('cpu', 'gpu', 'memory', 'storage'):
        group_alerts = [alert for alert in alerts if alert[1] == group]
        if group_alerts:
            severity, _, label = group_alerts[0]
            out[f'{group}_class'] = classes[severity]
            out[f'{group}_status'] = f'{label} {"HIGH" if severity == 2 else "WARN"}'
    out['health_class'] = classes[alerts[0][0]] if alerts else 'ok'
    out['health'] = out[f'{alerts[0][1]}_status'] if alerts else 'SYSTEM ONLINE'
    if len(alerts) > 1:
        out['health'] += f' +{len(alerts) - 1}'
    out['health_detail'] = ' · '.join(
        f'{label} {"HIGH" if severity == 2 else "WARN"}'
        for severity, _, label in alerts
    ) or 'All monitored metrics below warning thresholds'


def sensors():
    result = []
    for hw in Path('/sys/class/hwmon').glob('hwmon*'):
        chip = read(hw / 'name')
        device = str((hw / 'device').resolve())
        for kind, divisor in [('temp', 1000), ('fan', 1)]:
            for f in hw.glob(f'{kind}*_input'):
                value = number(f)
                if value is None:
                    continue
                channel = f.name.removesuffix('_input')
                result.append(dict(chip=chip, device=device, channel=channel,
                                   label=read(hw / f'{channel}_label', channel),
                                   kind=kind, value=value / divisor))
    return result


def sample(elapsed, previous_gpu_loads=()):
    out = {}
    cpu = [int(x) for x in read('/proc/stat').splitlines()[0].split()[1:9]]
    total, idle = sum(cpu), cpu[3] + cpu[4]
    before = PREV.get('cpu')
    PREV['cpu'] = (total, idle)
    load = 100 * (1 - (idle-before[1]) / max(1, total-before[0])) if before else 0
    freqs = [float(x) for x in re.findall(r'cpu MHz\s*:\s*([\d.]+)', read('/proc/cpuinfo'))]
    items = sensors()
    tctl = next((s['value'] for s in items if s['chip'] == 'k10temp' and s['label'] == 'Tctl'), None)
    dimms = [s['value'] for s in items if s['chip'] == 'spd5118' and s['kind'] == 'temp']
    # Raw controller channel names only; physical positions belong to the panel config.
    out['fans'] = {f'fan{i}': 'N/A' for i in range(1, 8)}
    for sensor in items:
        if sensor['kind'] == 'fan' and sensor['chip'] == 'nct6799':
            out['fans'][sensor['channel']] = int(sensor['value'])
    out.update(cpu_pct=round(pct(load), 1), cpu_load=text(load, '%', 1), cpu_temp=text(tctl, '°'),
               cpu_temp_num=tctl or 0, cpu_temp_c=text(tctl, '°C'), cpu_ghz=text(sum(freqs)/len(freqs)/1000 if freqs else None, ' GHz', 2),
               dimm_temp=text(max(dimms) if dimms else None, '°C'),
               dimm_detail=' / '.join(text(t) for t in dimms) + ' °C' if dimms else 'N/A')
    mem = {}
    for line in read('/proc/meminfo').splitlines():
        k, value = line.split(':', 1)
        mem[k] = int(value.strip().split()[0]) * 1024
    ram_total = mem['MemTotal']; ram_used = ram_total - mem['MemAvailable']
    swap_total = mem.get('SwapTotal', 0); swap_used = swap_total - mem.get('SwapFree', 0)
    out.update(ram_pct=round(pct(ram_used/ram_total*100), 1),
               ram_load=text(ram_used/ram_total*100, '%'), ram_used=f'{gib(ram_used):.1f}',
               ram_total=f'{gib(ram_total):.0f}',
               ram_detail=f'{gib(ram_used):.1f} / {gib(ram_total):.0f} GiB',
               swap_pct=round(pct(swap_used/swap_total*100), 1) if swap_total else 0,
               swap_detail=f'{gib(swap_used):.1f} / {gib(swap_total):.0f} GiB')
    fs = os.statvfs('/var'); fs_total = fs.f_blocks * fs.f_frsize
    fs_used = (fs.f_blocks - fs.f_bfree) * fs.f_frsize; fs_avail = fs.f_bavail * fs.f_frsize
    fs_pct = fs_used / max(1, fs_used + fs_avail) * 100
    # Resolve the actual /var block device from the host mount table.
    source = next((line.split()[0] for line in read('/proc/mounts').splitlines()
                   if len(line.split()) > 1 and line.split()[1] == '/var'), '')
    block = Path(source).name
    block_device = Path('/sys/class/block') / block
    parent = block_device.resolve().parent.name if (block_device/'partition').exists() else block
    nvme_controller = re.match(r'(nvme\d+)n\d+', parent)
    ctrl = nvme_controller.group(1) if nvme_controller else None
    disk_temp = next((s['value'] for s in items if s['chip'] == 'nvme' and
                      s['label'] == 'Composite' and ctrl and Path(s['device']).name == ctrl), None)
    diskstat = read(Path('/sys/class/block')/parent/'stat').split()
    disk_read = delta('disk_read', int(diskstat[2])*512, elapsed) if len(diskstat)>6 else 0
    disk_write = delta('disk_write', int(diskstat[6])*512, elapsed) if len(diskstat)>6 else 0
    out.update(var_pct=round(pct(fs_pct),1), var_load=text(fs_pct,'%'),
               var_detail=f'{gib(fs_used):.1f} / {gib(fs_total):.0f} GiB',
               var_free=f'{gib(fs_avail):.0f} GiB free', disk_temp=text(disk_temp,'°C'),
               disk_name=parent or 'N/A', disk_read=f'{disk_read/1024**2:.1f}', disk_write=f'{disk_write/1024**2:.1f}')
    # Query the NVIDIA card once per sample, shared by both dashboards.
    gpu = [None] * 8
    try:
        result = subprocess.run(['/usr/bin/nvidia-smi', '--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,fan.speed,power.draw,power.limit,clocks.gr',
                                 '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=1.5)
        if result.returncode == 0:
            values = result.stdout.splitlines()[0].split(',')
            gpu = [float(v.strip()) if re.fullmatch(r'[\d.]+', v.strip()) else None for v in values]
    except (OSError, subprocess.TimeoutExpired, IndexError, ValueError):
        pass
    util, used, capacity, temp, fan, watts, limit, clock = gpu
    out.update(gpu_pct=pct(util or 0), gpu_load=text(util,'%'), gpu_temp=text(temp,'°C'),
               gpu_vram=f'{(used or 0)/1024:.1f} / {(capacity or 0)/1024:.0f} GiB' if capacity else 'N/A',
               gpu_vram_pct=pct(used/capacity*100) if capacity and used is not None else 0,
               gpu_power=text(watts,' W'), gpu_limit=text(limit,' W'), gpu_fan=text(fan,'%'), gpu_clock=text(clock,' MHz'))
    amd_link = Path('/dev/dri/by-path/pci-0000:7b:00.0-card')
    amd = Path('/sys/class/drm')/amd_link.resolve().name/'device'
    amd_temp = next((s['value'] for s in items if s['chip']=='amdgpu' and '7b:00.0' in s['device'] and s['kind']=='temp'),None)
    amd_busy = number(amd/'gpu_busy_percent')
    render_ns = None
    pid = os.environ.get('WESTON_PID', '')
    if pid.isdigit():
        for f in Path(f'/proc/{pid}/fdinfo').glob('*'):
            data = read(f)
            if 'drm-driver:\tamdgpu' in data and '0000:7b:00.0' in data:
                match = re.search(r'drm-engine-gfx:\s*(\d+) ns',data)
                if match:
                    render_ns = int(match.group(1)); break
    render_busy = delta('render_ns',render_ns,elapsed)/1e9*100 if render_ns is not None else None
    out.update(amd_load=text(amd_busy,'%'), amd_temp=text(amd_temp,'°C'),
               render_load=text(render_busy,'%',2), render_ns=render_ns or 0,
               renderer='AMD / GLES' if render_ns is not None else 'Not verified')
    # Package energy is optional: many kernels restrict it to root.
    energy = number('/sys/class/powercap/intel-rapl:0/energy_uj')
    power = delta('cpu_energy',energy,elapsed)/1e6 if energy is not None else None
    out['cpu_power'] = text(power,' W')
    uptime = int(float(read('/proc/uptime','0').split()[0])); days, hours = divmod(uptime//3600,24)
    out.update(clock=time.strftime('%H:%M'), date=time.strftime('%a · %d %b'),
               uptime=f'{days}d {hours}h' if days else f'{hours}h {uptime//60%60:02d}m',
               hostname=os.uname().nodename, updated=time.strftime('%H:%M:%S'))
    add_health(out, previous_gpu_loads=previous_gpu_loads, cpu_temp=tctl, gpu_temp=temp,
               dimm_temp=max(dimms) if dimms else None, disk_temp=disk_temp)
    return out


def write_stats(out):
    temp = CACHE/'stats.tmp'
    temp.write_text(json.dumps(out,ensure_ascii=False)+'\n')
    temp.replace(CACHE/'stats.json')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true');parser.add_argument('--list-sensors',action='store_true');args=parser.parse_args()
    if args.list_sensors:
        print(json.dumps(sensors(),ensure_ascii=False,indent=2));return
    previous_gpu_loads = deque(maxlen=GPU_LOAD_CONFIRM_SAMPLES - 1)
    before=time.monotonic()
    while True:
        now=time.monotonic()
        try:
            out = sample(max(.001, now-before), previous_gpu_loads)
            write_stats(out)
            previous_gpu_loads.append(out['gpu_pct'])
        except Exception as exc:
            # Preserve last good sample and report failures to the service journal.
            previous_gpu_loads.clear()
            print(f'Telemetry sample failed: {exc}',flush=True)
        before=now
        if args.once:return
        time.sleep(max(.1,2-(time.monotonic()-now)))

if __name__=='__main__':main()
