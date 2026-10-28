"""Read and change the selected macOS UVC camera's hardware exposure controls.

OpenCV's AVFoundation capture backend does not reliably expose these UVC
controls. The bundled, MIT-licensed uvc-util helper talks to the USB device
directly; every write is followed by a hardware readback.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import threading


ROOT = Path(__file__).resolve().parent.parent
CONTROL_NAMES = ('auto-exposure-mode', 'exposure-time-abs', 'gain')
MODE_MANUAL = 1
MODE_AUTO = 8


class UvcControlError(ValueError):
    pass


def _usb_id(name: str, fallback: str) -> int:
    value = os.environ.get(name, fallback)
    try:
        result = int(value, 0)
    except ValueError as exc:
        raise UvcControlError(f'{name} 必须是 USB 厂商或产品编号。') from exc
    if not 0 <= result <= 0xffff:
        raise UvcControlError(f'{name} 必须在 0–65535 之间。')
    return result


class UvcControls:
    def __init__(self, binary: str | Path | None = None, vendor_id: int | None = None,
                 product_id: int | None = None):
        self.binary = Path(binary or os.environ.get('STAR16_UVC_UTIL', ROOT/'bin'/'uvc-util'))
        self.vendor_id = vendor_id if vendor_id is not None else _usb_id('STAR16_UVC_VENDOR_ID', '0x05a3')
        self.product_id = product_id if product_id is not None else _usb_id('STAR16_UVC_PRODUCT_ID', '0x9230')
        self.lock = threading.RLock()

    @property
    def selector(self) -> str:
        return f'0x{self.vendor_id:04x}:0x{self.product_id:04x}'

    def _run(self, *args: str) -> str:
        if sys.platform != 'darwin':
            raise UvcControlError('当前硬件曝光控制仅支持 macOS UVC 相机。')
        if not self.binary.is_file():
            raise UvcControlError('缺少 UVC 控制工具；请运行 tools/build_uvc_util.sh。')
        try:
            completed = subprocess.run([str(self.binary), *args], capture_output=True,
                                       text=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UvcControlError(f'读取 UVC 控制失败：{type(exc).__name__}') from exc
        if completed.returncode:
            message = (completed.stderr or completed.stdout).strip().splitlines()
            raise UvcControlError(f'UVC 设备拒绝操作：{message[-1] if message else completed.returncode}')
        return completed.stdout

    def _device(self) -> dict:
        lines = self._run('-d').splitlines()
        pattern = re.compile(r'^\s*\d+\s+(0x[0-9a-fA-F]{4}):(0x[0-9a-fA-F]{4})\s+\S+\s+\S+\s+(.+)$')
        matches = []
        for line in lines:
            match = pattern.match(line)
            if match and int(match[1], 16) == self.vendor_id and int(match[2], 16) == self.product_id:
                matches.append(match[3].strip())
        if not matches:
            raise UvcControlError(f'未找到 USB 相机 {self.selector}；请检查连接，或配置 STAR16_UVC_VENDOR_ID / PRODUCT_ID。')
        if len(matches) > 1:
            raise UvcControlError(f'发现多台相同 USB ID 的相机 {self.selector}，无法安全指定控制目标。')
        return {'name': matches[0], 'vendor_id': f'0x{self.vendor_id:04x}',
                'product_id': f'0x{self.product_id:04x}'}

    def _control(self, name: str) -> dict:
        if name not in CONTROL_NAMES:
            raise UvcControlError('不支持的 UVC 控制项。')
        output = self._run('-V', self.selector, '-S', name, '-g', name)
        values = {}
        for key in ('minimum', 'maximum', 'step-size', 'default-value', 'current-value'):
            match = re.search(rf'^\s*{re.escape(key)}:\s*(-?\d+)\s*$', output, re.MULTILINE)
            values[key.replace('-', '_')] = int(match[1]) if match else None
        if values['current_value'] is None:
            raise UvcControlError(f'相机未返回 {name} 的当前值。')
        return values

    def _status(self) -> dict:
        device = self._device()
        available = set(self._run('-V', self.selector, '-c').split())
        if not set(CONTROL_NAMES) <= available:
            missing = ', '.join(sorted(set(CONTROL_NAMES) - available))
            raise UvcControlError(f'这台相机没有开放所需控制项：{missing}。')
        mode = self._control('auto-exposure-mode')
        exposure = self._control('exposure-time-abs')
        gain = self._control('gain')
        return {'device': device, 'mode': 'manual' if mode['current_value'] == MODE_MANUAL else 'auto',
                'auto_exposure_mode': mode, 'exposure_100us': exposure, 'gain': gain,
                'note': '曝光单位为 0.1 ms；设置只影响相机硬件，不控制红外补光灯。'}

    def status(self) -> dict:
        with self.lock:
            return self._status()

    def _set(self, name: str, value: int) -> None:
        self._run('-V', self.selector, '-s', f'{name}={value}')
        actual = self._control(name)['current_value']
        if actual != value:
            raise UvcControlError(f'{name} 设置未生效：请求 {value}，相机回读 {actual}。')

    def apply(self, mode: str, exposure_100us: int | None, gain: int | None) -> dict:
        with self.lock:
            before = self._status()
            if mode == 'manual':
                if exposure_100us is None or gain is None:
                    raise UvcControlError('手动曝光需要同时提供曝光时间和增益。')
                for name, value in (('exposure_100us', exposure_100us), ('gain', gain)):
                    limits = before[name]
                    minimum, maximum = limits['minimum'], limits['maximum']
                    step = limits['step_size'] or 1
                    if minimum is None or maximum is None or not minimum <= value <= maximum or (value-minimum) % step:
                        raise UvcControlError(f'{name} 超出相机允许的范围或步长。')
                self._set('auto-exposure-mode', MODE_MANUAL)
                self._set('exposure-time-abs', exposure_100us)
                self._set('gain', gain)
            elif mode == 'auto':
                default = before['auto_exposure_mode']['default_value']
                self._set('auto-exposure-mode', default if default not in (None, MODE_MANUAL) else MODE_AUTO)
            else:
                raise UvcControlError('曝光模式必须是 auto 或 manual。')
            return self._status()

    def reset(self) -> dict:
        with self.lock:
            before = self._status()
            defaults = {name: before[key]['default_value'] for name, key in
                        (('auto-exposure-mode', 'auto_exposure_mode'),
                         ('exposure-time-abs', 'exposure_100us'), ('gain', 'gain'))}
            if any(value is None for value in defaults.values()):
                raise UvcControlError('相机没有报告完整的出厂默认值，无法一键恢复。')
            # Manual mode is required for exposure and gain writes to take effect.
            self._set('auto-exposure-mode', MODE_MANUAL)
            self._set('exposure-time-abs', defaults['exposure-time-abs'])
            self._set('gain', defaults['gain'])
            self._set('auto-exposure-mode', defaults['auto-exposure-mode'])
            return self._status()
